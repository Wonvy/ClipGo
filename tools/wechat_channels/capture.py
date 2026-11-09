"""Narrow mitmproxy observer for WeChat Channels.

The launcher must use process-scoped local capture, ALLOW_HOSTS_PATTERN and its
own confdir. This addon never installs a certificate or changes proxy settings.
Only signed media URLs and explicit Channels metadata are retained. Headers,
cookies, requests and response bodies are never written to disk.

Run through mitmdump, for example (certificate setup is a separate user action):
  mitmdump --mode local:Weixin.exe,WeChat.exe,WeChatAppEx.exe \
    --allow-hosts '^(?:channels\\.weixin\\.qq\\.com|finder\\.video\\.qq\\.com)(?::443)?$' \
    --set confdir=<private-directory> --set block_global=true -s capture.py
Set CLIPGO_CHANNELS_STATE to a private JSON state file before launching.
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import zlib


CHANNELS_HOST = "channels.weixin.qq.com"
MEDIA_HOSTS = frozenset({"finder.video.qq.com"})
ALLOW_HOSTS_PATTERN = r"^(?:channels\.weixin\.qq\.com|finder\.video\.qq\.com)(?::443)?$"
LOCAL_CAPTURE_MODE = "local:Weixin.exe,WeChat.exe,WeChatAppEx.exe"
MAX_BODY = 2 * 1024 * 1024
MAX_ITEMS = 200
MAX_AGE = 4 * 60 * 60
_TRANSIENT_PARAMS = frozenset({"token", "vkey", "web", "range", "offset", "start", "end"})


def tls_failure_reason(error: object) -> str:
    """Return a fixed category, never an error string containing private data."""
    text = str(error or "").lower()[:4096]
    if any(term in text for term in ("unknown ca", "unknown_ca", "certificate unknown",
                                    "certificate_unknown", "bad certificate", "bad_certificate")):
        return "certificate_rejected"
    if "certificate verify failed" in text or "certificate_verify_failed" in text:
        return "certificate_validation_failed"
    if "certificate" in text:
        return "certificate_error"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(term in text for term in ("reset", "closed", "eof")):
        return "connection_closed"
    if any(term in text for term in ("wrong version", "unsupported protocol", "protocol version", "no shared cipher")):
        return "protocol_mismatch"
    return "handshake_failed"


def tls_scope(data) -> str:
    """Classify scope without persisting SNI, IP addresses or certificates."""
    context = getattr(data, "context", None)
    client = getattr(context, "client", None)
    server = getattr(context, "server", None)
    names = [getattr(getattr(data, "conn", None), "sni", None),
             getattr(client, "sni", None), getattr(server, "sni", None)]
    address = getattr(server, "address", None)
    if isinstance(address, tuple) and address:
        names.append(address[0])
    known_names = []
    for name in names:
        if not isinstance(name, str) or not name:
            continue
        normalized = name.lower().rstrip(".")
        if normalized == CHANNELS_HOST or normalized in MEDIA_HOSTS:
            return "channels"
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            known_names.append(normalized)
    return "other" if known_names else "unknown"


def valid_media_url(value: object) -> bool:
    """Only accept the observed Channels media origin, never arbitrary URLs."""
    if not isinstance(value, str) or not value or len(value) > 16384:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname in MEDIA_HOSTS
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.fragment
                and bool(parsed.path))
    except (ValueError, TypeError):
        return False


def media_identity(url: str) -> str:
    """Use media query identity, excluding signatures and range requests."""
    if not valid_media_url(url):
        raise ValueError("不支持的视频地址")
    parsed = urlsplit(url)
    query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                   if k.lower() not in _TRANSIENT_PARAMS)
    canonical = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, urlencode(query), ""))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _text(value: object, limit: int) -> str:
    return value[:limit].replace("\x00", "") if isinstance(value, str) else ""


def _decode_key(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", text):
        return None
    return text if int(text) < 2**64 else None


def _media_url(media: dict) -> str | None:
    url = media.get("url")
    if not valid_media_url(url):
        return None
    token = media.get("urlToken")
    if isinstance(token, str) and token:
        # A token is a query suffix, never a URL/path or replacement origin.
        if len(token) > 8192 or token[0] not in "&?" or "#" in token:
            return None
        suffix = token.lstrip("&?")
        existing = {k.lower() for k, _ in parse_qsl(urlsplit(url).query)}
        additions = [(k, v) for k, v in parse_qsl(suffix, keep_blank_values=True)
                     if k.lower() not in existing]
        if additions:
            url += ("&" if "?" in url else "?") + urlencode(additions)
    return url if valid_media_url(url) else None


def extract_media(payload: object, now: float | None = None) -> list[dict]:
    """Parse only explicit objectDesc.media entries in bounded Channels JSON.

    Native protobuf traffic is not guessed at. If that is all a WeChat version
    sends, media requests may be found without their decode key.
    """
    seen_at = time.time() if now is None else now
    found: dict[str, dict] = {}
    pending = [(payload, 0)]
    visited = 0
    while pending and visited < 20000:
        value, depth = pending.pop()
        visited += 1
        if depth > 24:
            continue
        if isinstance(value, list):
            pending.extend((item, depth + 1) for item in value[:2000])
            continue
        if not isinstance(value, dict):
            continue
        desc = value.get("objectDesc")
        if isinstance(desc, dict):
            contact = value.get("contact")
            author = _text(contact.get("nickname"), 120) if isinstance(contact, dict) else ""
            media_list = desc.get("media", [])
            if isinstance(media_list, list):
                for media in media_list[:100]:
                    if not isinstance(media, dict):
                        continue
                    # mediaType 4 is video; unknown types remain acceptable when
                    # their URL belongs to the dedicated video origin.
                    media_type = media.get("mediaType", desc.get("mediaType"))
                    if media_type is not None and str(media_type) != "4":
                        continue
                    url = _media_url(media)
                    if not url:
                        continue
                    ident = media_identity(url)
                    found[ident] = {
                        "id": ident,
                        "title": _text(desc.get("description"), 500) or "视频号视频",
                        "author": author,
                        "url": url,
                        "decode_key": _decode_key(media.get("decodeKey")),
                        "seen_at": seen_at,
                        "source": "metadata",
                    }
        pending.extend((item, depth + 1) for item in list(value.values())[:2000])
    return list(found.values())


class CaptureState:
    """Bounded session-only state, replacing one private file atomically."""
    def __init__(self, path: Path | None = None):
        self.path = path
        self.items: dict[str, dict] = {}
        self.status = "starting"
        self.metadata_responses = 0
        self.media_requests = 0
        self.error_count = 0
        self.http_requests = 0
        self.tls_failed_client = 0
        self.tls_failed_server = 0
        self.tls_established_client = 0
        self.tls_established_server = 0
        self.tls_unknown_failures = 0
        self.last_diagnostic = None

    def put(self, item: dict):
        if not valid_media_url(item.get("url")):
            return
        ident = media_identity(item["url"])
        old = self.items.get(ident)
        clean = {
            "id": ident, "title": _text(item.get("title"), 500) or "视频号视频",
            "author": _text(item.get("author"), 120), "url": item["url"],
            "decode_key": _decode_key(item.get("decode_key")),
            "seen_at": float(item.get("seen_at", time.time())),
            "source": "metadata" if item.get("source") == "metadata" else "media",
        }
        if old:
            # Range-request events update recency/signatures but must never erase
            # the useful metadata we learned from an earlier JSON response.
            if clean["source"] == "media":
                for key in ("title", "author", "decode_key", "source"):
                    clean[key] = old[key]
                if old.get("decode_key") not in (None, ""):
                    clean["url"] = old["url"]
        self.items[ident] = clean
        self.prune()

    def prune(self, now: float | None = None):
        now = time.time() if now is None else now
        recent = [item for item in self.items.values() if item["seen_at"] >= now - MAX_AGE]
        recent.sort(key=lambda item: item["seen_at"], reverse=True)
        self.items = {item["id"]: item for item in recent[:MAX_ITEMS]}

    def snapshot(self) -> dict:
        self.prune()
        return {"version": 1, "status": self.status, "updated_at": time.time(),
                "metadata_responses": self.metadata_responses,
                "media_requests": self.media_requests, "error_count": self.error_count,
                "http_requests": self.http_requests,
                "tls_failed_client": self.tls_failed_client,
                "tls_failed_server": self.tls_failed_server,
                "tls_established_client": self.tls_established_client,
                "tls_established_server": self.tls_established_server,
                "tls_unknown_failures": self.tls_unknown_failures,
                "last_diagnostic": self.last_diagnostic,
                "media": sorted(self.items.values(), key=lambda item: item["seen_at"], reverse=True)}

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)


class ParentMonitor:
    """Keep a Windows process handle, avoiding PID reuse and pointer truncation."""
    def __init__(self, pid: str | None):
        self.enabled = pid is not None
        self.handle = None
        self.kernel = None
        if not self.enabled or os.name != "nt":
            return
        try:
            process_id = int(pid)
            if not 0 < process_id < 2**32:
                return
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x00100000, False, process_id)  # SYNCHRONIZE
        except (ValueError, TypeError, OSError):
            pass

    def gone(self) -> bool:
        if not self.enabled:
            return False
        if not self.handle or self.kernel is None:
            return True
        # WAIT_TIMEOUT means the parent remains alive. An unavailable handle or
        # any unexpected result fails closed, rather than leaving capture active.
        return self.kernel.WaitForSingleObject(self.handle, 0) != 258

    def close(self):
        if self.handle and self.kernel is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class ChannelsCapture:
    def __init__(self, path: Path | None = None):
        configured = os.environ.get("CLIPGO_CHANNELS_STATE")
        self.state = CaptureState(path or (Path(configured) if configured else None))
        self.parent = None
        self.watchdog = None

    def _shutdown(self):
        from mitmproxy import ctx
        ctx.master.shutdown()

    async def _watch_lifecycle(self):
        stop_path = self.state.path.with_suffix(".stop")
        while True:
            try:
                stop_requested = stop_path.exists()
                if stop_requested:
                    stop_path.unlink(missing_ok=True)
                if stop_requested or (self.parent is not None and self.parent.gone()):
                    self._shutdown()
                    return
            except OSError:
                self._shutdown()
                return
            await asyncio.sleep(0.25)

    def running(self):
        if self.state.path is None:
            # Fail closed instead of writing captured URLs to a guessed location.
            raise RuntimeError("CLIPGO_CHANNELS_STATE 未设置")
        self.state.status = "listening"
        self.state.save()
        self.parent = ParentMonitor(os.environ.get("CLIPGO_PARENT_PID"))
        self.watchdog = asyncio.create_task(self._watch_lifecycle())

    def requestheaders(self, flow):
        if flow.request.host == CHANNELS_HOST or flow.request.host in MEDIA_HOSTS:
            self.state.http_requests += 1
            self.state.save()

    def _tls_failed(self, data, side: str):
        scope = tls_scope(data)
        if scope == "other":
            return
        if scope == "unknown":
            self.state.tls_unknown_failures += 1
        else:
            name = "tls_failed_" + side
            setattr(self.state, name, getattr(self.state, name) + 1)
        self.state.last_diagnostic = {
            "kind": "tls_" + side, "scope": scope,
            "reason": tls_failure_reason(getattr(getattr(data, "conn", None), "error", None)),
            "at": time.time(),
        }
        self.state.save()

    def tls_failed_client(self, data):
        self._tls_failed(data, "client")

    def tls_failed_server(self, data):
        self._tls_failed(data, "server")

    def _tls_established(self, data, side: str):
        if tls_scope(data) != "channels":
            return
        name = "tls_established_" + side
        setattr(self.state, name, getattr(self.state, name) + 1)
        self.state.save()

    def tls_established_client(self, data):
        self._tls_established(data, "client")

    def tls_established_server(self, data):
        self._tls_established(data, "server")

    def responseheaders(self, flow):
        response = flow.response
        if response is None:
            return
        # Stream everything by default: never accumulate video/chat bodies.
        response.stream = True
        if flow.request.host in MEDIA_HOSTS:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            path = urlsplit(flow.request.url).path.lower()
            if (response.status_code in (200, 206) and valid_media_url(flow.request.url)
                    and (content_type.startswith("video/") or content_type == "application/octet-stream"
                         or path.endswith("/stodownload") or path.endswith(".mp4"))):
                self.state.media_requests += 1
                self.state.put({"url": flow.request.url, "seen_at": time.time(), "source": "media"})
                self.state.save()
            return
        if flow.request.host != CHANNELS_HOST or response.status_code != 200:
            return
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            return
        try:
            if int(response.headers.get("content-length", "0")) > MAX_BODY:
                return
        except ValueError:
            return
        buffer = bytearray()
        flow.metadata["clipgo_body"] = buffer
        flow.metadata["clipgo_overflow"] = False

        def observe(chunk: bytes) -> bytes:
            if not flow.metadata["clipgo_overflow"]:
                if len(buffer) + len(chunk) > MAX_BODY:
                    buffer.clear()
                    flow.metadata["clipgo_overflow"] = True
                else:
                    buffer.extend(chunk)
            return chunk

        response.stream = observe

    def response(self, flow):
        body = flow.metadata.pop("clipgo_body", None)
        overflow = flow.metadata.pop("clipgo_overflow", False)
        if body is None or overflow or flow.response is None:
            return
        try:
            encoding = flow.response.headers.get("content-encoding", "identity").lower()
            if encoding in ("gzip", "deflate"):
                inflater = zlib.decompressobj(31 if encoding == "gzip" else zlib.MAX_WBITS)
                body = inflater.decompress(bytes(body), MAX_BODY + 1)
                if len(body) > MAX_BODY or not inflater.eof:
                    return
            elif encoding == "br":
                # Brotli 1.2+ supplies a bounded-output incremental interface.
                # Older installations are skipped rather than decompressing an
                # unbounded response. The capture runtime pins a compatible one.
                try:
                    import brotli
                except ImportError:
                    return
                try:
                    inflater = brotli.Decompressor()
                    body = inflater.process(bytes(body), output_buffer_limit=MAX_BODY + 1)
                    if len(body) > MAX_BODY or not inflater.is_finished():
                        return
                except (brotli.error, TypeError):
                    return
            elif encoding != "identity":
                return
            payload = json.loads(body)
        except (ValueError, UnicodeError, zlib.error):
            return
        self.state.metadata_responses += 1
        for item in extract_media(payload):
            self.state.put(item)
        self.state.save()

    def error(self, flow):
        if flow.request.host in MEDIA_HOSTS or flow.request.host == CHANNELS_HOST:
            self.state.error_count += 1
            self.state.save()

    def done(self):
        if self.watchdog is not None:
            self.watchdog.cancel()
            self.watchdog = None
        if self.parent is not None:
            self.parent.close()
            self.parent = None
        self.state.status = "stopped"
        self.state.save()


addons = [ChannelsCapture()]
