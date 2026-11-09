"""Read already-loaded Channels resources from the selected WeChat renderer kind.

No code injection, process writes, network requests, proxy changes or memory
dumps. The scanner opens at most two recently-created renderers per supported player kind
and keeps only allowlisted media URLs (plus explicitly paired media metadata).
"""
from __future__ import annotations

import ctypes
import copy
import hashlib
from ctypes import wintypes
import json
import os
from pathlib import PureWindowsPath
import re
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlsplit

from .capture import media_identity
from .loaded_urls import extract_loaded_urls

MAX_PROCESS_BYTES = 512 * 1024 * 1024
MAX_PROCESS_SECONDS = 20
MAX_PROCESSES = 2
MAX_CANDIDATES = 1000
CHUNK_SIZE = 1024 * 1024
OVERLAP = 32 * 1024
MAX_ADDRESS = 0x7FFFFFFFFFFF
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class ScanError(Exception):
    """User-safe scan failure without process data or signed URLs."""


def select_processes(rows: list[dict], source='all') -> list[dict]:
    """Defense-in-depth filter for the fixed CIM query; never scan generic WeChat."""
    groups = {'main': [], 'standalone': []}
    if source not in ('all', 'standalone', 'main'):
        raise ValueError('Unsupported player source')
    for row in rows:
        try:
            path = row['ExecutablePath']
            command = row['CommandLine']
            pid = int(row['ProcessId'])
            created = int(row['CreationTicks'])
            if (not isinstance(path, str) or not isinstance(command, str) or pid <= 0
                    or created <= 0 or PureWindowsPath(path).name.lower() != 'wechatappex.exe'
                    or not re.search(r'(?:^|\s)--type=renderer(?:\s|$)', command)
                    or not re.search(r'(?:^|\s)--wmpf-render-type=[67](?:\s|$)', command)):
                continue
            kind = 'main' if re.search(r'(?:^|\s)--wmpf-render-type=6(?:\s|$)', command) else 'standalone'
            if source == 'all' or source == kind:
                groups[kind].append({'pid': pid, 'path': path, 'created': created, 'source': kind})
        except (KeyError, TypeError, ValueError):
            continue
    for group in groups.values():
        group.sort(key=lambda value: value['created'], reverse=True)
        del group[MAX_PROCESSES:]
    if source != 'all':
        return groups[source]
    # Main panel first, then the newest standalone window: one player's second
    # worker cannot delay the other player's first results.
    return [group[index] for index in range(MAX_PROCESSES)
            for group in (groups['main'], groups['standalone']) if index < len(group)]


def discover_processes(source='all') -> list[dict]:
    # This is a constant read-only query, never assembled from page/user content.
    query = r'''Get-CimInstance Win32_Process -Filter "Name = 'WeChatAppEx.exe'" |
Where-Object { $_.CommandLine -match '(?:^|\s)--type=renderer(?:\s|$)' -and $_.CommandLine -match '(?:^|\s)--wmpf-render-type=[67](?:\s|$)' } |
Select-Object ProcessId,ExecutablePath,CommandLine,@{Name='CreationTicks';Expression={$_.CreationDate.ToUniversalTime().ToFileTimeUtc()}} |
ConvertTo-Json -Compress'''
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', query],
                                capture_output=True, timeout=15, creationflags=_NO_WINDOW)
        if result.returncode:
            raise ScanError('暂时无法查找微信视频进程，请稍后重试。')
        payload = result.stdout.decode('utf-8-sig', errors='replace').strip()
        if not payload:
            return []
        rows = json.loads(payload)
        return select_processes(rows if isinstance(rows, list) else [rows], source)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise ScanError('暂时无法查找微信视频进程，请稍后重试。') from exc


class _MemoryInfo(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('allocation', ctypes.c_void_p),
                ('allocation_protect', wintypes.DWORD), ('size', ctypes.c_size_t),
                ('state', wintypes.DWORD), ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]


class WindowsMemoryReader:
    """Small read-only Win32 surface, replaceable with a fake reader in tests."""
    def __init__(self):
        self.last_error = None
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MemoryInfo), ctypes.c_size_t]
        self.api.VirtualQueryEx.restype = ctypes.c_size_t
        self.api.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.api.ReadProcessMemory.restype = wintypes.BOOL
        self.api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.api.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.api.GetProcessTimes.restype = wintypes.BOOL

    def open(self, process: dict):
        # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, never write/operation rights.
        self.last_error = None
        handle = self.api.OpenProcess(0x0400 | 0x0010, False, process['pid'])
        if not handle:
            self.last_error = 'access_denied' if ctypes.get_last_error() == 5 else 'open_failed'
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.api.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                self.last_error = 'identity_unavailable'
                raise ValueError('Process identity unavailable')
            if not self.api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                self.last_error = 'identity_unavailable'
                raise ValueError('Process creation time unavailable')
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            # CIM truncates its datetime to microseconds, so permit <1us rounding.
            if (PureWindowsPath(path.value) != PureWindowsPath(process['path'])
                    or abs(ticks - process['created']) >= 10):
                self.last_error = 'identity_changed'
                raise ValueError('Process identity changed')
            return handle
        except Exception:
            if self.last_error is None:
                self.last_error = 'open_failed'
            self.api.CloseHandle(handle)
            return None

    def query(self, handle, address: int):
        info = _MemoryInfo()
        if not self.api.VirtualQueryEx(handle, address, ctypes.byref(info), ctypes.sizeof(info)):
            return None
        return (info.base or 0, info.size,
                info.state == 0x1000 and info.protect in (0x02, 0x04, 0x08) and info.kind == 0x20000)

    def read(self, handle, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        self.api.ReadProcessMemory(handle, address, buffer, size, ctypes.byref(received))
        return buffer.raw[:min(received.value, size)]

    def close(self, handle):
        self.api.CloseHandle(handle)


def iter_scoped_chunks(reader, process: dict, cancel: threading.Event, progress,
                       *, byte_limit=MAX_PROCESS_BYTES, seconds=MAX_PROCESS_SECONDS,
                       chunk_size=CHUNK_SIZE, diagnostics=None):
    """Yield bounded overlapping readable chunks, including across region borders."""
    if cancel.is_set():
        return
    handle = reader.open(process)
    if handle is None:
        if diagnostics is not None:
            error = getattr(reader, 'last_error', None)
            if error not in {'access_denied', 'open_failed', 'identity_unavailable', 'identity_changed'}:
                error = 'open_failed'
            diagnostics['read_failures'][error] = diagnostics['read_failures'].get(error, 0) + 1
        return
    if diagnostics is not None:
        diagnostics['workers_opened'] += 1
    try:
        address = 0
        attempted = 0
        deadline = time.monotonic() + seconds
        tail = b''
        contiguous_end = None
        while address < MAX_ADDRESS and attempted < byte_limit:
            if cancel.is_set() or time.monotonic() >= deadline:
                break
            region = reader.query(handle, address)
            if region is None:
                break
            base, size, readable = region
            if size <= 0 or base + size <= address:
                break
            address = base + size
            if not readable:
                tail, contiguous_end = b'', None
                continue
            offset = 0
            while offset < size and attempted < byte_limit:
                if cancel.is_set() or time.monotonic() >= deadline:
                    break
                start = base + offset
                if start != contiguous_end:
                    tail = b''
                amount = min(chunk_size, size - offset, byte_limit - attempted)
                data = reader.read(handle, start, amount)
                if diagnostics is not None:
                    diagnostics['bytes_read'] += len(data)
                    diagnostics['bytes_attempted'] += amount
                attempted += amount  # Failed reads also consume the safety budget.
                offset += amount
                joined = tail + data
                if joined:
                    yield joined
                if len(data) == amount:
                    tail = joined[-OVERLAP:]
                    contiguous_end = start + amount
                else:
                    tail, contiguous_end = b'', None
                progress(attempted, byte_limit)
    finally:
        reader.close(handle)


def _url_rank(url: str) -> tuple:
    params = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    return (bool(params.get('basedata') and params.get('sign')), bool(params.get('token')), len(params), len(url))


def loaded_identity(url: str) -> str:
    parsed = urlsplit(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if params.get('encfilekey'):
        identity = json.dumps([parsed.hostname, parsed.path, params['encfilekey'], params.get('idx', '')])
        return hashlib.sha256(identity.encode()).hexdigest()[:24]
    return media_identity(url)


def merge_media_candidate(old: dict | None, candidate: dict) -> dict:
    """Keep exact response URL/key pairs, preferring the first complete pair.

    Renderers are scanned newest first. Later keyless metadata may enrich display
    fields, but must never erase a working pair or borrow its key for another URL.
    At most four complete pairs are retained for the downloader's bounded retry.
    """
    old_paired = old is not None and old.get('decode_key') not in (None, '')
    new_paired = candidate.get('decode_key') not in (None, '')
    if not old_paired and not new_paired:
        return dict(candidate)
    chosen = dict(old if old_paired else candidate)
    pairs, seen = [], set()
    for source in (old, candidate):
        if source is None or source.get('decode_key') in (None, ''):
            continue
        for pair in (source, *source.get('media_options', [])[:3]):
            url, key = pair.get('url'), pair.get('decode_key')
            identity = (url, str(key))
            if key in (None, '') or identity in seen:
                continue
            seen.add(identity)
            pairs.append({'url': url, 'decode_key': key})
    chosen['media_options'] = pairs[1:4]
    for field in ('title', 'author', 'duration', 'thumbnail_url'):
        missing = not chosen.get(field) or (field == 'title' and chosen.get(field) == '视频号视频')
        if missing and candidate.get(field) not in (None, ''):
            chosen[field] = candidate[field]
    return chosen


def scan_loaded_videos(cancel: threading.Event, progress=lambda scanned, total: None,
                       diagnostics=None, source='all', on_items=None) -> list[dict]:
    stats = diagnostics if diagnostics is not None else {}
    stats.clear()
    stats.update(workers_discovered=0, workers_opened=0, bytes_read=0,
                 bytes_attempted=0, url_matches=0, videos_found=0, paired_media=0, read_failures={})
    if os.name != 'nt':
        raise ScanError('读取已加载视频目前仅支持 Windows。')
    if cancel.is_set():
        return []
    processes = discover_processes(source)
    stats['workers_discovered'] = len(processes)
    if not processes:
        raise ScanError('没有找到视频号工作进程。请在微信中打开视频号并播放后重试。')
    reader = WindowsMemoryReader()
    found = {}
    now = time.time()
    last_emitted = None
    last_emit_time = 0.0

    def publish(force=False):
        nonlocal last_emitted, last_emit_time
        if on_items is None or not found or cancel.is_set():
            return
        timestamp = time.monotonic()
        if not force and timestamp - last_emit_time < .15:
            return
        # Give callbacks a detached snapshot: their UI merges must not mutate
        # URL/key pairs still being assembled by the scanner.
        snapshot = list(found.values())
        signature = tuple((item['id'], item.get('url'), item.get('decode_key'),
                           item.get('title'), item.get('author'), item.get('duration'), item.get('thumbnail_url'),
                           tuple((p.get('url'), p.get('decode_key')) for p in item.get('media_options', [])))
                          for item in snapshot)
        if signature != last_emitted:
            on_items(copy.deepcopy(snapshot))
            last_emitted = signature
            last_emit_time = timestamp

    for number, process in enumerate(processes):
        if cancel.is_set():
            break
        local_ids = set()
        process_source = process.get('source', source)
        def update(scanned, _total):
            progress(number * MAX_PROCESS_BYTES + scanned, len(processes) * MAX_PROCESS_BYTES)
        for chunk in iter_scoped_chunks(reader, process, cancel, update, diagnostics=stats):
            for url in extract_loaded_urls(chunk):
                stats['url_matches'] += 1
                ident = loaded_identity(url)
                local_ids.add(ident)
                old = found.get(ident)
                if old and old.get('decode_key') not in (None, ''):
                    continue  # A signed URL and its key are one indivisible pair.
                if old and _url_rank(old['url']) >= _url_rank(url):
                    continue
                if old is None and len(found) >= MAX_CANDIDATES:
                    continue
                found[ident] = {'id': ident, 'title': old.get('title', '') if old else '',
                                'author': '', 'url': url, 'decode_key': None,
                                'seen_at': now, 'source': process_source}
                if old:
                    for key in ('title', 'author', 'decode_key'):
                        found[ident][key] = old.get(key)
            if b'objectDesc' in chunk or b'decodeKey' in chunk or b'd\x00e\x00c\x00o\x00d\x00e\x00K\x00e\x00y' in chunk:
                from .memory_metadata import extract_media_metadata
                for candidate in extract_media_metadata(chunk):
                    if candidate['url'] not in extract_loaded_urls(candidate['url'].encode() + b'\0'):
                        continue
                    ident = loaded_identity(candidate['url'])
                    if ident not in found and len(found) >= MAX_CANDIDATES:
                        continue
                    old = found.get(ident)
                    candidate['id'] = ident
                    found[ident] = merge_media_candidate(old, candidate)
            publish()
        publish(force=True)
        # The native main player keeps signed URL parts in separate V8 strings;
        # its complete, same-object pairs need not have a flat string duplicate.
        # Standalone windows keep their stricter observed-resource identity gate.
        if (local_ids or process_source == 'main') and not cancel.is_set():
            from .v8_media import extract_bound_media
            seen_paired = set()
            def add_bound(items):
                for item in items:
                    ident = item['id']
                    if process_source != 'main' and ident not in local_ids:
                        continue
                    if process_source == 'main' and item['url'] not in extract_loaded_urls(item['url'].encode() + b'\0'):
                        continue
                    if ident not in found and len(found) >= MAX_CANDIDATES:
                        continue
                    found[ident] = merge_media_candidate(found.get(ident), item)
                    seen_paired.add(ident)
                publish(force=True)
            bound = extract_bound_media(reader, process, cancel, on_items=add_bound, allowed_ids=None if process_source == 'main' else local_ids)
            add_bound(bound)
            stats['paired_media'] += len(seen_paired)
    # No URLs, tokens, process command lines or memory bytes are written to disk.
    stats['videos_found'] = len(found)
    if not cancel.is_set() and not stats['workers_opened']:
        if stats['read_failures'].get('access_denied'):
            raise ScanError('找到微信视频进程，但读取权限不足。请以与微信相同的权限启动本工具。')
        raise ScanError('找到微信视频进程，但未能读取。它可能刚刚重启，请重新播放后重试。')
    if not cancel.is_set() and not stats['bytes_read']:
        raise ScanError('已连接微信视频进程，但没有读到播放数据。请重新播放后重试。')
    publish(force=True)
    return list(found.values())
