"""HTML desktop host with a narrow, credential-free JavaScript bridge."""
from __future__ import annotations

import base64
from collections import OrderedDict
from copy import deepcopy
import ctypes
import io
import json
import math
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time

from .download import Cancelled, DownloadError, download_video
from .engagement import merge_engagement, normalize_engagement
from .memory import ScanError, scan_loaded_videos
from .playback import PlaybackServer
from .thumbnails import load_thumbnail

BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ClipGoChannels'
REPORT_PATH = BASE / 'last-web-state.json'
MAX_ITEMS = 1000
MAX_THUMBNAILS = 64
MAX_PREVIEW_FILES = 2
MAX_PREVIEW_BYTES = 256 * 1024 * 1024


def _duration(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and 0 <= value < 10_000_000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _title(item):
    return str(item.get('title') or '').strip()[:4096] or '暂未读取到标题'


class Api:
    """Only explicitly public commands are exported by pywebview.

    Mutable internals and callbacks start with an underscore so pywebview does
    not expose them. Media URLs, signatures and keys remain in _candidates.
    """
    def __init__(self, *, folder=None, scanner=None, downloader=None,
                 thumbnail_loader=None, report_path=None, auto_refresh_enabled=True):
        self._lock = threading.RLock()
        self._window = None
        self._scanner = scanner or scan_loaded_videos
        self._downloader = downloader or download_video
        self._thumbnail_loader = thumbnail_loader or load_thumbnail
        self._folder = Path(folder or Path.home() / 'Downloads' / 'ClipGo视频号')
        self._report_path = Path(report_path) if report_path else None
        self._source = 'all'
        self._revision = 0
        self._library_revision = 0
        self._seen_ids = set()
        self._removed_ids = set()
        self._auto_refresh_enabled = bool(auto_refresh_enabled)
        self._generation = 0
        self._candidates = {}
        self._items = OrderedDict()
        self._saved = {}
        self._saved_metadata = {}
        self._item_states = {}
        self._scan = {'state': 'idle', 'message': '在微信中播放视频，然后刷新列表'}
        self._download = {'state': 'idle', 'id': None, 'title': '', 'received': 0,
                          'total': 0, 'percent': 0, 'message': ''}
        self._preview = {'state': 'idle', 'id': None, 'title': '', 'received': 0,
                         'total': 0, 'percent': 0, 'message': '', 'url': None}
        self._preview_generation = 0
        self._preview_cancel = threading.Event()
        self._preview_jobs = queue.Queue(maxsize=1)
        self._preview_worker = None
        self._preview_temp = None
        self._preview_files = OrderedDict()
        self._playback = None
        self._diagnostics = {}
        self._scan_cancel = threading.Event()
        self._download_cancel = threading.Event()
        self._stop = threading.Event()
        self._scan_worker = None
        self._download_worker = None
        self._thumb_workers_started = False
        self._thumb_jobs = queue.Queue()
        self._thumb_pending = set()
        self._thumb_requested = set()
        self._thumb_cache = OrderedDict()
        self._closing = False
        self._close_waiter_started = False
        self._allow_close = False
        self._dialog_busy = False
        self._page_controller = None
        self._page_worker = None
        self._page_requested = False
        self._page_state = {'state': 'disabled', 'message': '在微信页面内显示下载按钮',
                            'connected': False, 'buttons': 0, 'current': False}
        from .page_controller import certificate_status
        self._page_certificate = certificate_status()

    def get_state(self, known_revision=None):
        with self._lock:
            if type(known_revision) is int and known_revision == self._revision:
                return {'revision': self._revision, 'unchanged': True}
            # Explicit public records prevent future private fields from leaking.
            return {'revision': self._revision, 'source': self._source,
                    'library_revision': self._library_revision,
                    'auto_refresh_enabled': self._auto_refresh_enabled,
                    'folder': str(self._folder), 'scan': dict(self._scan),
                    'items': [dict(item, engagement=dict(item['engagement']),
                                   status=self._item_states.get(ident, item['status']))
                              for ident, item in self._items.items()],
                    'download': dict(self._download), 'preview': dict(self._preview),
                    'page_button': deepcopy(self._page_state),
                    'page_certificate': dict(self._page_certificate)}

    def _page_status(self):
        with self._lock:
            return {'ok': not self._closing, 'download': {
                key: self._download[key] for key in ('state', 'id', 'percent', 'message')}}

    def _page_event(self, event):
        with self._lock:
            if self._closing:
                return
            self._page_state['connected'] = True
            if type(event.get('buttons')) is int:
                self._page_state['buttons'] = event['buttons']
            if event.get('kind') == 'current' and 'has_feed' in event:
                self._page_state['current'] = bool(event['has_feed'])
            if event.get('placement') in ('inline', 'floating', 'none'):
                self._page_state['placement'] = event['placement']
            diagnostic_names = ('source_count', 'candidate_count', 'hook_count', 'video_count', 'vue_components', 'selected_index_valid',
                                'vue_component', 'vue_feed', 'detail_seen')
            diagnostics = {key: event[key] for key in diagnostic_names
                           if type(event.get(key)) in (int, bool)}
            if diagnostics:
                self._page_state['diagnostics'] = diagnostics
            self._page_state['message'] = ('下载按钮已加入微信页面' if self._page_state['buttons']
                                           else '已连接微信页面，正在等待操作栏')
            self._revision += 1
            self._write_report()

    def _page_candidate(self, candidate, *, download=False):
        # Only the authenticated backend bridge calls this with a validated
        # same-feed snapshot. It is not exported to the desktop HTML page.
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '工具正在关闭。'}
            ident = candidate['id']
            if download:
                self._removed_ids.discard(ident)
            self._apply_candidates([candidate], self._generation, final=False)
            self._revision += 1

            if not download:
                return {'ok': True}
            if ident not in self._candidates:
                return {'ok': False, 'message': '列表已满，请先清理部分记录。'}
            # An explicit page click downloads this exact URL/key pair, even if
            # a memory refresh previously retained another signature for the ID.
            self._candidates[ident] = deepcopy(candidate)
            return self.download(ident)

    def _set_page_state(self, update):
        with self._lock:
            self._page_state.update(update)
            if update.get('state') in ('disabled', 'error'):
                self._page_state.update(connected=False, buttons=0, current=False)
            self._revision += 1
            self._write_report()

    def set_page_button(self, enabled):
        if type(enabled) is not bool:
            return {'ok': False, 'message': '请选择开启或关闭。'}
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '工具正在关闭。'}
            if self._page_worker and self._page_worker.is_alive():
                if not enabled:
                    self._page_requested = False
                    if self._page_controller:
                        self._page_controller.request_stop()
                    self._set_page_state({'state': 'stopping', 'message': '正在取消开启并恢复设置…'})
                    return {'ok': True}
                return {'ok': False, 'message': '正在更新微信内按钮，请稍候。'}
            self._page_requested = enabled
            self._set_page_state({'state': 'starting' if enabled else 'stopping',
                                 'message': '正在开启微信内按钮…' if enabled else '正在关闭并恢复设置…'})
            def change():
                try:
                    if enabled:
                        from .page_controller import PageController
                        if self._page_controller is None:
                            self._page_controller = PageController(on_candidate=self._page_candidate,
                                get_status=self._page_status, on_event=self._page_event,
                                on_state=self._set_page_state)
                        self._page_controller.start(cancelled=lambda: self._closing or not self._page_requested)
                        if self._closing:
                            self._page_controller.stop()
                    elif self._page_controller:
                        self._page_controller.stop()
                    else:
                        self._set_page_state({'state': 'disabled', 'message': '微信内按钮已关闭'})
                except Exception as exc:
                    from .page_controller import PageSetupError, PageSetupCancelled
                    message = str(exc) if isinstance(exc, PageSetupError) else '页内按钮未能启动，请关闭后重试。'
                    self._set_page_state({'state': 'disabled' if isinstance(exc, PageSetupCancelled) else 'error', 'message': message})
                finally:
                    self._refresh_page_certificate()
            self._page_worker = threading.Thread(target=change, daemon=True, name='ClipGo page button')
            self._page_worker.start()
        return {'ok': True}

    def _refresh_page_certificate(self):
        from .page_controller import certificate_status
        status = certificate_status()
        with self._lock:
            self._page_certificate = status
            self._revision += 1

    def set_page_certificate_keep(self, keep):
        if type(keep) is not bool:
            return {'ok': False, 'message': '请选择是否保留证书。'}
        with self._lock:
            if self._closing or self._page_worker and self._page_worker.is_alive():
                return {'ok': False, 'message': '正在更新微信内按钮，请稍候。'}
            from .page_controller import set_certificate_preference, PageSetupError
            try:
                set_certificate_preference(keep)
            except (OSError, PageSetupError):
                return {'ok': False, 'message': '未能保存证书设置，请重试。'}
            self._refresh_page_certificate()
            if not keep and self._page_state['state'] != 'enabled' and self._page_certificate['installed']:
                return self.remove_page_certificate()
        return {'ok': True, 'message': ('证书将保留在本机，下次开启可直接复用。' if keep
                                      else '关闭微信内按钮时将移除证书。')}

    def remove_page_certificate(self):
        with self._lock:
            if self._closing or self._page_worker and self._page_worker.is_alive():
                return {'ok': False, 'message': '正在更新微信内按钮，请稍候。'}
            self._page_requested = False
            self._set_page_state({'state': 'stopping', 'message': '正在关闭微信内按钮并移除证书…'})
            def remove():
                from .page_controller import remove_owned_certificate, recover_session, PageSetupError
                try:
                    if self._page_controller:
                        self._page_controller.stop()
                    else:
                        recover_session()
                    status = remove_owned_certificate()
                    if status['installed']:
                        raise PageSetupError('证书不属于本工具安装，未作移除。')
                    self._set_page_state({'state': 'disabled', 'message': '本机证书已移除'})
                except Exception as exc:
                    message = str(exc) if isinstance(exc, PageSetupError) else '证书未能移除，请重试。'
                    self._set_page_state({'state': 'error', 'message': message})
                finally:
                    self._refresh_page_certificate()
            self._page_worker = threading.Thread(target=remove, daemon=True, name='ClipGo remove certificate')
            self._page_worker.start()
        return {'ok': True}

    def scan(self, source='all'):
        if source not in ('all', 'standalone', 'main'):
            return {'ok': False, 'message': '请选择有效的视频来源。'}
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '窗口正在关闭。'}
            if self._scan['state'] == 'scanning':
                return {'ok': False, 'message': '正在读取视频，请稍候。'}
            self._source = source
            self._generation += 1
            generation = self._generation
            # Old jobs cannot publish into a new generation. If refresh fails,
            # retained cards must not keep spinning for those abandoned jobs.
            self._thumb_pending.clear()
            self._thumb_requested = {ident for ident, item in self._items.items()
                                     if item['thumbnail_state'] == 'ready'}
            for item in self._items.values():
                if item['thumbnail_state'] == 'loading':
                    item['thumbnail_state'] = 'unavailable'
            self._scan_cancel = threading.Event()
            cancel = self._scan_cancel
            self._scan = {'state': 'scanning', 'message': '正在读取视频标题和封面…'}
            self._diagnostics = {}
            self._revision += 1
            self._scan_worker = threading.Thread(target=self._scan_work,
                args=(source, generation, cancel), daemon=True, name='ClipGo scan')
            self._scan_worker.start()
        return {'ok': True}

    def _scan_work(self, source, generation, cancel):
        stats = {}
        def on_items(candidates):
            with self._lock:
                if self._closing or cancel.is_set() or generation != self._generation:
                    return
                self._apply_candidates(candidates, generation, final=False)
                self._scan['message'] = f'已读取 {len(candidates)} 个视频，继续查找中…'
                self._revision += 1
        try:
            candidates = self._scanner(cancel, diagnostics=stats, source=source, on_items=on_items)
            with self._lock:
                if self._closing or cancel.is_set() or generation != self._generation:
                    return
                self._diagnostics = dict(stats)
                self._apply_candidates(candidates, generation, final=True)
                self._scan = {'state': 'done', 'message': f'已找到 {len(self._items)} 个视频' if self._items
                              else '暂未找到视频，请在微信中播放后再次刷新'}
                self._revision += 1
                self._ensure_thumbnail_workers()
                self._write_report()
        except Exception as exc:
            message = str(exc) if isinstance(exc, ScanError) else '暂时无法读取视频，请在微信中重新播放后重试。'
            with self._lock:
                if generation == self._generation and not self._closing:
                    self._diagnostics = dict(stats)
                    self._scan = {'state': 'error', 'message': message}
                    self._revision += 1
                    self._write_report()

    def _apply_candidates(self, candidates, generation, *, final):
        # The list is a session library, not a replaceable scan result. Admit new
        # IDs ahead of the existing stable order; empty/failed/source-switched
        # scans must never remove an item the user has already seen.
        previous_candidates, previous_items = self._candidates, self._items
        updates, added = {}, OrderedDict()
        for item in candidates:
            if not isinstance(item, dict) or not item.get('id'):
                continue
            ident = str(item['id'])
            self._seen_ids.add(ident)
            if ident in self._removed_ids:
                continue
            previous = updates.get(ident) or previous_candidates.get(ident) or added.get(ident)
            if previous is None and len(previous_candidates) + len(added) >= MAX_ITEMS:
                # The cap limits admission; it never evicts existing cards.
                continue
            merged = deepcopy(item)
            if previous:
                if previous.get('decode_key') not in (None, '') and item.get('decode_key') in (None, ''):
                    # Retain the old complete URL/key/options together when a
                    # refresh only rediscovers an unpaired raw URL for this ID.
                    merged = deepcopy(previous)
                for field in ('title', 'author', 'thumbnail_url'):
                    value = item.get(field)
                    if value and (field != 'title' or str(value).strip() not in ('视频号视频', '暂未读取到标题')):
                        merged[field] = value
                    elif previous.get(field):
                        merged[field] = previous[field]
                if _duration(item.get('duration')) is None:
                    merged['duration'] = previous.get('duration')
                else:
                    merged['duration'] = item['duration']
            if 'engagement' in item or previous and 'engagement' in previous:
                # Counts belong to the same feed, independently of which valid
                # URL/key pair is retained for download. Zero and lower updated
                # counts are meaningful; absent/invalid fields retain known ones.
                merged['engagement'] = merge_engagement(
                    previous.get('engagement') if previous else None, item.get('engagement'))
            if ident in previous_candidates:
                updates[ident] = merged
            else:
                added[ident] = merged
        incoming = dict(added)
        incoming.update((ident, updates.get(ident, item)) for ident, item in previous_candidates.items())
        self._candidates = incoming
        self._items = OrderedDict()
        self._thumb_requested = set()
        for ident, item in incoming.items():
            has_key = item.get('decode_key') not in (None, '')
            raw_title = str(item.get('title') or '').strip()
            thumbnail_url = item.get('thumbnail_url')
            public = {'id': ident, 'title': _title(item),
                      'author': str(item.get('author') or '').strip()[:256],
                      'duration': _duration(item.get('duration')),
                      'engagement': normalize_engagement(item.get('engagement')),
                      'recognized': bool((raw_title and raw_title != '视频号视频') or has_key or thumbnail_url),
                      'status': 'ready' if has_key else 'pending',
                      'thumbnail': None, 'thumbnail_state': 'unavailable'}
            self._items[ident] = public
            old = previous_items.get(ident)
            same_cover = previous_candidates.get(ident, {}).get('thumbnail_url') == thumbnail_url
            if old:
                if not raw_title or raw_title == '视频号视频':
                    public['title'] = old['title']
                if not public['author']:
                    public['author'] = old['author']
                if public['duration'] is None:
                    public['duration'] = old['duration']
                if not thumbnail_url and old['thumbnail_state'] == 'ready':
                    public.update(thumbnail=old['thumbnail'], thumbnail_state='ready')
                public['recognized'] = public['recognized'] or old['recognized']
            if old and same_cover and old['thumbnail_state'] == 'ready':
                public.update(thumbnail=old['thumbnail'], thumbnail_state='ready')
            if thumbnail_url and len(self._thumb_requested) < MAX_THUMBNAILS:
                self._thumb_requested.add(ident)
                job = (generation, ident, thumbnail_url)
                if thumbnail_url in self._thumb_cache:
                    public.update(thumbnail=self._thumb_cache[thumbnail_url], thumbnail_state='ready')
                    self._thumb_cache.move_to_end(thumbnail_url)
                elif public['thumbnail_state'] != 'ready':
                    public['thumbnail_state'] = 'loading'
                    if job not in self._thumb_pending:
                        self._thumb_pending.add(job)
                        self._thumb_jobs.put(job)
        self._thumb_pending.intersection_update(
            (generation, ident, incoming[ident].get('thumbnail_url')) for ident in self._thumb_requested)
        if self._items != previous_items:
            self._library_revision += 1
        self._ensure_thumbnail_workers()

    def remove_video(self, ident):
        if not isinstance(ident, str):
            return {'ok': False, 'message': '请选择列表中的视频。'}
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '窗口正在关闭。'}
            if ident not in self._items:
                return {'ok': True, 'removed': 0}
            self._removed_ids.add(ident)
            self._candidates.pop(ident, None)
            self._items.pop(ident, None)
            self._thumb_requested.discard(ident)
            self._thumb_pending = {job for job in self._thumb_pending if job[1] != ident}
            if self._preview['id'] == ident:
                self.close_preview()
            self._library_revision += 1
            self._revision += 1
            self._write_report()
            return {'ok': True, 'removed': 1}

    def clear_videos(self):
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '窗口正在关闭。'}
            removed = len(self._items)
            self._removed_ids.update(self._seen_ids)
            self._removed_ids.update(self._items)
            self._scan_cancel.set()
            self._generation += 1
            self._candidates.clear()
            self._items.clear()
            self._thumb_pending.clear()
            self._thumb_requested.clear()
            self._scan = {'state': 'idle', 'message': '列表已清空，新发现的视频会自动加入'}
            self.close_preview()
            self._library_revision += 1
            self._revision += 1
            self._write_report()
            return {'ok': True, 'removed': removed}

    def _ensure_thumbnail_workers(self):
        if self._thumb_workers_started or not self._thumb_pending:
            return
        self._thumb_workers_started = True
        for index in range(3):
            threading.Thread(target=self._thumbnail_work, daemon=True,
                             name=f'ClipGo cover {index}').start()

    def _thumbnail_work(self):
        while not self._stop.is_set():
            try:
                generation, ident, url = self._thumb_jobs.get(timeout=.25)
            except queue.Empty:
                continue
            with self._lock:
                if (generation, ident, url) not in self._thumb_pending or self._closing:
                    self._thumb_pending.discard((generation, ident, url))
                    continue
            try:
                image = self._thumbnail_loader(url)
                encoded = io.BytesIO()
                image.convert('RGB').save(encoded, format='JPEG', quality=88)
                data = 'data:image/jpeg;base64,' + base64.b64encode(encoded.getvalue()).decode('ascii')
            except Exception:
                data = None
            with self._lock:
                expected = (generation, ident, url) in self._thumb_pending
                self._thumb_pending.discard((generation, ident, url))
                if self._closing or not expected or generation != self._generation or ident not in self._items:
                    continue
                if data is not None:
                    self._thumb_cache[url] = data
                    self._thumb_cache.move_to_end(url)
                    while len(self._thumb_cache) > MAX_THUMBNAILS:
                        self._thumb_cache.popitem(last=False)
                self._items[ident].update(thumbnail=data, thumbnail_state='ready' if data else 'unavailable')
                self._revision += 1
                self._write_report()

    def download(self, ident):
        if not isinstance(ident, str):
            return {'ok': False, 'message': '请选择列表中的视频。'}
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '窗口正在关闭。'}
            if self._download['state'] in ('downloading', 'cancelling'):
                return {'ok': False, 'message': '已有视频正在下载，请等待完成。'}
            if ident not in self._candidates:
                return {'ok': False, 'message': '视频已不在当前列表，请刷新后重试。'}
            candidate = deepcopy(self._candidates[ident])
            folder = self._folder
            self._download_cancel = threading.Event()
            cancel = self._download_cancel
            self._download = {'state': 'downloading', 'id': ident, 'title': _title(candidate),
                              'received': 0, 'total': 0, 'percent': 0, 'message': '正在连接视频…'}
            self._item_states[ident] = 'downloading'
            self._revision += 1
            self._download_worker = threading.Thread(target=self._download_work,
                args=(ident, candidate, folder, cancel), daemon=True, name='ClipGo download')
            self._download_worker.start()
        return {'ok': True}

    def _download_work(self, ident, candidate, folder, cancel):
        def progress(received, total):
            with self._lock:
                received, total = max(0, int(received)), max(0, int(total))
                self._download.update(received=received, total=total,
                    percent=min(100, received / total * 100) if total else 0)
                if not cancel.is_set():
                    self._download['message'] = '正在下载视频…'
                self._revision += 1
        try:
            path = self._downloader(candidate, folder, cancel, progress)
            with self._lock:
                self._saved[ident] = Path(path)
                self._saved_metadata[ident] = {'title': _title(candidate)}
                self._download.update(state='done', percent=100, message='下载完成')
                self._item_states[ident] = 'done'
                self._revision += 1
        except Exception as exc:
            cancelled = isinstance(exc, Cancelled)
            state = 'cancelled' if cancelled else 'error'
            message = str(exc) if isinstance(exc, DownloadError) else '下载失败，请重新播放视频后重试。'
            with self._lock:
                self._download.update(state=state, percent=0, message=message)
                self._item_states[ident] = state
                self._revision += 1

    def cancel_download(self):
        with self._lock:
            if self._download['state'] not in ('downloading', 'cancelling'):
                return {'ok': False, 'message': '当前没有正在下载的视频。'}
            self._download_cancel.set()
            self._download.update(state='cancelling', message='正在结束请求并清理未完成文件…')
            self._revision += 1
        return {'ok': True}

    def prepare_preview(self, ident):
        """Prepare a registered video; browser input can never supply a URL/path."""
        if not isinstance(ident, str):
            return {'ok': False, 'message': '请选择列表中的视频。'}
        with self._lock:
            if self._closing:
                return {'ok': False, 'message': '窗口正在关闭。'}
            if ident in self._removed_ids:
                return {'ok': False, 'message': '该视频已从列表移除。'}
            candidate = self._candidates.get(ident)
            saved = self._saved.get(ident)
            cached = self._preview_files.get(ident)
            path = saved if saved and saved.is_file() else None
            if path is None and cached and cached['path'].is_file():
                path = cached['path']
                self._preview_files.move_to_end(ident)
            if candidate is None and path is None:
                return {'ok': False, 'message': '视频已不在当前列表，请刷新后重试。'}
            if self._preview['id'] == ident and (self._preview['state'] == 'preparing'
                    or self._preview['state'] == 'ready' and path is not None):
                return {'ok': True}
            title = _title(candidate or self._saved_metadata.get(ident) or cached or {})
            self._preview_cancel.set()
            self._preview_generation += 1
            generation = self._preview_generation
            self._preview_cancel = threading.Event()
            cancel = self._preview_cancel
            if self._playback:
                self._playback.revoke_all()
            self._preview = {'state': 'preparing', 'id': ident, 'title': title,
                             'received': 0, 'total': 0, 'percent': 0,
                             'message': '正在准备播放，首次打开需要加载视频…', 'url': None}
            self._revision += 1
            # At most one transfer runs. A new selection replaces the queued
            # request while the previous request performs cancellation cleanup.
            try:
                self._preview_jobs.get_nowait()
            except queue.Empty:
                pass
            self._preview_jobs.put_nowait((generation, ident, deepcopy(candidate), path, title, cancel))
            if self._preview_worker is None or not self._preview_worker.is_alive():
                self._preview_worker = threading.Thread(target=self._preview_work,
                    daemon=True, name='ClipGo preview')
                self._preview_worker.start()
        return {'ok': True}

    def close_preview(self):
        with self._lock:
            self._preview_cancel.set()
            self._preview_generation += 1
            if self._playback:
                self._playback.revoke_all()
            self._preview = {'state': 'idle', 'id': None, 'title': '', 'received': 0,
                             'total': 0, 'percent': 0, 'message': '', 'url': None}
            self._revision += 1
            self._trim_preview_cache()
        return {'ok': True}

    def _preview_work(self):
        try:
            self._preview_loop()
        finally:
            # Also clean up when a timed-out shutdown could not join this worker;
            # the transfer owns its partial file until cancellation has completed.
            if self._closing:
                self._cleanup_preview()

    def _preview_loop(self):
        while not self._stop.is_set():
            try:
                generation, ident, candidate, path, title, cancel = self._preview_jobs.get(timeout=.2)
            except queue.Empty:
                continue
            with self._lock:
                if self._closing or cancel.is_set() or generation != self._preview_generation:
                    continue
            created = None
            def progress(received, total):
                with self._lock:
                    if self._closing or cancel.is_set() or generation != self._preview_generation:
                        return
                    received, total = max(0, int(received)), max(0, int(total))
                    self._preview.update(received=received, total=total,
                        percent=min(100, received / total * 100) if total else 0,
                        message='正在加载视频，完成后即可播放…')
                    self._revision += 1
            try:
                if path is None:
                    with self._lock:
                        if self._preview_temp is None:
                            self._preview_temp = tempfile.TemporaryDirectory(prefix='clipgo-preview-')
                        folder = Path(self._preview_temp.name)
                    path = Path(self._downloader(candidate, folder, cancel, progress))
                    # Only this owned temporary directory is ever cleaned up.
                    if path.resolve().parent != folder.resolve():
                        raise DownloadError('播放缓存无法建立，请重试。')
                    created = path
                with self._lock:
                    if self._closing or cancel.is_set() or generation != self._preview_generation:
                        if created:
                            created.unlink(missing_ok=True)
                        continue
                    if self._playback is None:
                        self._playback = PlaybackServer()
                    url = self._playback.register(path)
                    if created:
                        previous = self._preview_files.pop(ident, None)
                        if previous and previous['path'] != path:
                            previous['path'].unlink(missing_ok=True)
                        self._preview_files[ident] = {'path': path, 'title': title}
                    self._preview.update(state='ready', percent=100, url=url, message='视频已准备好')
                    self._revision += 1
                    self._trim_preview_cache(keep=ident)
            except Exception as exc:
                if created:
                    try:
                        created.unlink(missing_ok=True)
                    except OSError:
                        pass
                with self._lock:
                    if self._closing or generation != self._preview_generation:
                        continue
                    state = 'cancelled' if isinstance(exc, Cancelled) else 'error'
                    message = str(exc) if isinstance(exc, DownloadError) else '暂时无法播放，请在微信中重新播放后刷新。'
                    self._preview.update(state=state, percent=0, message=message, url=None)
                    self._revision += 1

    def _trim_preview_cache(self, keep=None):
        def size(entry):
            try:
                return entry['path'].stat().st_size
            except OSError:
                return 0
        for ident, entry in list(self._preview_files.items()):
            if len(self._preview_files) <= MAX_PREVIEW_FILES and sum(map(size, self._preview_files.values())) <= MAX_PREVIEW_BYTES:
                break
            if ident != keep:
                try:
                    entry['path'].unlink(missing_ok=True)
                    del self._preview_files[ident]
                except OSError:
                    pass

    def _cleanup_preview(self):
        with self._lock:
            playback, temporary = self._playback, self._preview_temp
            self._playback = self._preview_temp = None
            self._preview_files.clear()
        if playback:
            playback.close()
        if temporary:
            # Revoked HTTP handlers release their file handle after the current
            # local chunk. Allow that short interval on Windows before deleting.
            for attempt in range(6):
                try:
                    temporary.cleanup()
                    break
                except OSError:
                    if attempt < 5:
                        time.sleep(.1)

    def choose_folder(self):
        with self._lock:
            if self._closing or self._window is None or self._dialog_busy:
                return {'ok': False, 'message': '请稍后再选择保存位置。'}
            self._dialog_busy = True
            directory = str(self._folder)
        try:
            import webview
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=directory)
            if result:
                folder = Path(result[0]).expanduser().resolve()
                with self._lock:
                    self._folder = folder
                    self._revision += 1
            return {'ok': True, 'folder': str(self._folder)}
        except Exception:
            return {'ok': False, 'message': '无法选择保存位置，请稍后重试。'}
        finally:
            with self._lock:
                self._dialog_busy = False

    def open_folder(self):
        with self._lock:
            folder = self._folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
            return {'ok': True}
        except OSError:
            return {'ok': False, 'message': '保存文件夹无法打开，请更改保存位置。'}

    def open_video(self, ident):
        if not isinstance(ident, str):
            return {'ok': False, 'message': '请选择已下载的视频。'}
        with self._lock:
            path = self._saved.get(ident)
        if path is None:
            return {'ok': False, 'message': '该视频尚未下载完成。'}
        try:
            if not path.is_file():
                raise FileNotFoundError()
            os.startfile(path)
            return {'ok': True}
        except OSError:
            return {'ok': False, 'message': '文件可能已移动，请打开保存文件夹查看。'}

    def _report(self):
        with self._lock:
            recognized = [item for item in self._items.values() if item['recognized']]
            visible = recognized or list(self._items.values())
            counts = [tuple(normalize_engagement(item.get('engagement')).values()) for item in visible]
            return {'result': self._scan['state'], 'candidate_count': len(self._items),
                    'page_button_state': self._page_state['state'],
                    'page_message': self._page_state['message'],
                    'page_buttons': self._page_state['buttons'],
                    'page_current': self._page_state['current'],
                    'page_diagnostics': dict(self._page_state.get('diagnostics', {})),
                    'recognized_count': len(recognized), 'ui_rows': len(visible),
                    'titled_rows': sum(item['title'] != '暂未读取到标题' for item in visible),
                    'thumbnail_rows': sum(item['thumbnail_state'] == 'ready' for item in visible),
                    'engagement_rows': sum(any(value is not None for value in row) for row in counts),
                    'complete_engagement_rows': sum(all(value is not None for value in row) for row in counts),
                    'thumbnail_expected': len(self._thumb_requested),
                    'pending_thumbnails': len(self._thumb_pending),
                    'workers_discovered': int(self._diagnostics.get('workers_discovered', 0)),
                    'workers_opened': int(self._diagnostics.get('workers_opened', 0))}

    def _write_report(self):
        if self._report_path is None:
            return
        try:
            self._report_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._report_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self._report()), encoding='utf-8')
            os.replace(temporary, self._report_path)
        except OSError:
            pass

    def _begin_close(self):
        with self._lock:
            if self._allow_close:
                return True
            self._closing = True
            self._scan_cancel.set()
            self._download_cancel.set()
            self._preview_cancel.set()
            if self._playback:
                self._playback.revoke_all()
            self._stop.set()
            workers = [worker for worker in (self._download_worker, self._preview_worker)
                       if worker and worker.is_alive()]
            if not workers:
                self._allow_close = True
                return True
            if self._download['state'] in ('downloading', 'cancelling'):
                self._download.update(state='cancelling', message='正在结束下载并清理文件，稍后关闭…')
            if self._preview['state'] == 'preparing':
                self._preview.update(message='正在清理播放缓存，稍后关闭…')
            self._revision += 1
            if not self._close_waiter_started:
                self._close_waiter_started = True
                def wait_and_close():
                    # Request reads have a 20-second timeout; the worker owns .part cleanup.
                    deadline = time.monotonic() + 25
                    for worker in workers:
                        worker.join(timeout=max(0, deadline - time.monotonic()))
                    if any(worker.is_alive() for worker in workers):
                        with self._lock:
                            self._close_waiter_started = False
                            self._download['message'] = '请求仍在结束，请稍候再关闭窗口。'
                            self._revision += 1
                        return
                    with self._lock:
                        self._allow_close = True
                    if self._window is not None:
                        self._window.destroy()
                threading.Thread(target=wait_and_close, daemon=True, name='ClipGo close').start()
            return False

    def _shutdown(self):
        self._closing = True
        self._scan_cancel.set()
        self._download_cancel.set()
        self._preview_cancel.set()
        self._stop.set()
        self._page_requested = False
        if self._page_controller:
            self._page_controller.request_stop()
        if self._page_worker and self._page_worker.is_alive():
            self._page_worker.join(timeout=25)
        if self._page_controller:
            try:
                self._page_controller.stop()
            except Exception:
                # The recovery journal survives; next launch retries cleanup.
                self._set_page_state({'state': 'error', 'message': '临时设置将在下次打开时继续清理'})
        deadline = time.monotonic() + 25
        for worker in (self._download_worker, self._preview_worker):
            if worker and worker.is_alive():
                worker.join(timeout=max(0, deadline - time.monotonic()))
        if not self._preview_worker or not self._preview_worker.is_alive():
            self._cleanup_preview()


def _assets_path():
    base = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
    return base / 'web_assets' / 'index.html'


def _diagnostic_result(report, dom, revision):
    """Return a safe final report, or None while normal rendering is pending."""
    if not isinstance(dom, dict) or not dom.get('ready'):
        return None
    try:
        errors = int(dom.get('frontendErrors', 0))
        rendered_revision = int(dom.get('renderedRevision', -1))
    except (ValueError, TypeError, OverflowError):
        return dict(report, result='error', page_loaded=True, reason='invalid_dom_report', frontend_errors=0)
    if errors:
        return dict(report, result='error', page_loaded=True, reason='frontend_error', frontend_errors=max(0, errors))
    if rendered_revision < revision:
        return None
    try:
        cards, titled, images = (int(dom[key]) for key in ('cards', 'titled', 'thumbnails'))
        if min(cards, titled, images) < 0 or not isinstance(dom.get('horizontalOverflow'), bool):
            raise ValueError('Invalid counters')
    except (KeyError, ValueError, TypeError, OverflowError):
        return dict(report, result='error', page_loaded=True, reason='invalid_dom_report', frontend_errors=0)
    result = dict(report, page_loaded=True, dom_cards=cards, dom_titled=titled,
                  dom_images=images, horizontal_overflow=dom['horizontalOverflow'], frontend_errors=0)
    engagement_mismatch = None
    for key, dom_key, reason in (
            ('engagement_rows', 'engagementRows', 'engagement_count_mismatch'),
            ('complete_engagement_rows', 'completeEngagementRows', 'complete_engagement_count_mismatch')):
        if key not in report:
            continue
        value = dom.get(dom_key)
        if type(value) is not int or value < 0:
            return dict(result, result='error', reason='invalid_dom_report')
        result['dom_' + key] = value
        if value != report[key] and engagement_mismatch is None:
            engagement_mismatch = reason
    reason = None
    if cards != report['ui_rows']:
        reason = 'card_count_mismatch'
    elif titled != report['titled_rows']:
        reason = 'title_count_mismatch'
    elif dom['horizontalOverflow']:
        reason = 'horizontal_overflow'
    elif engagement_mismatch:
        reason = engagement_mismatch
    elif images > report['thumbnail_rows']:
        reason = 'image_count_mismatch'
    elif images < report['thumbnail_rows']:
        # Setting img.src is synchronous; decoding/loading the data URL is not.
        return None
    if reason:
        result.update(result='error', reason=reason)
    return result


def main(diagnostic_path=None, enable_page_button=False):
    """Start the native HTML window. Diagnostic mode uses the same page/API."""
    if os.name != 'nt':
        raise SystemExit('本工具目前仅支持 Windows。')
    from .runtime import InstanceLock, MARKER, untrust_certificate
    lock = InstanceLock(BASE / 'instance.lock')
    if diagnostic_path is None and not lock.acquire():
        ctypes.windll.user32.MessageBoxW(None, '视频号助手已经打开，请使用现有窗口。', 'ClipGo 视频号', 0x40)
        return
    if diagnostic_path is None:
        from .page_controller import certificate_requires_admin
        from .elevation import is_admin, request_elevated_start
        if certificate_requires_admin() and not is_admin():
            # No window, controller or network changes exist yet. Release the
            # startup lock before launching the authorized replacement process.
            lock.close()
            try:
                started = request_elevated_start(enable_page_button)
                message = '已取消管理员授权。临时证书模式需要管理员权限，请重新打开工具后允许。'
            except OSError:
                started = False
                message = '未能申请管理员权限，请右键工具并选择“以管理员身份运行”。'
            if not started:
                ctypes.windll.user32.MessageBoxW(None, message, 'ClipGo 视频号', 0x40)
            return
    import webview
    api = Api(report_path=None if diagnostic_path else REPORT_PATH,
              auto_refresh_enabled=diagnostic_path is None)
    try:
        if MARKER.exists():
            untrust_certificate()
        if diagnostic_path is None:
            from .page_controller import recover_session
            recover_session()
            api._refresh_page_certificate()
        if enable_page_button:
            api.set_page_button(True)
        entrypoint = _assets_path()
        if not entrypoint.is_file():
            raise RuntimeError('界面文件缺失，请重新解压完整的软件文件夹。')
        webview.settings['ALLOW_DOWNLOADS'] = False
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
        window = webview.create_window('ClipGo · 视频号', str(entrypoint), js_api=api,
            width=1120, height=800, min_size=(840, 600), hidden=diagnostic_path is not None,
            background_color='#F6F7F9', text_select=True)
        api._window = window
        window.events.closing += api._begin_close
        loaded = threading.Event()

        def initialized(renderer):
            # gui='edgechromium' can still fall back if the runtime is missing.
            from webview.platforms import winforms
            if getattr(winforms, 'renderer', None) != 'edgechromium':
                api._startup_error = True
                return False

        def on_loaded():
            # Do not access WebView2's COM properties from this asynchronous
            # callback. Trusted local assets/CSP and text-only rendering provide
            # the document boundary, without touching the native UI thread.
            loaded.set()

        window.events.initialized += initialized
        window.events.loaded += on_loaded

        def diagnose():
            deadline = time.monotonic() + 110
            report = {'result': 'timeout', 'page_loaded': False}
            while time.monotonic() < deadline:
                snapshot = api.get_state()
                if loaded.is_set() and snapshot['scan']['state'] in ('done', 'error'):
                    with api._lock:
                        pending = bool(api._thumb_pending)
                    if not pending:
                        try:
                            dom = window.evaluate_js('window.clipgoDiagnostics && window.clipgoDiagnostics()')
                        except Exception:
                            dom = None
                        checked = _diagnostic_result(api._report(), dom, snapshot['revision'])
                        if checked is not None:
                            report = checked
                            break
                time.sleep(.15)
            if report['result'] == 'timeout':
                report.update(page_loaded=loaded.is_set(), frontend_errors=0)
            if diagnostic_path:
                Path(diagnostic_path).write_text(json.dumps(report), encoding='utf-8')
            window.destroy()

        webview.start(diagnose if diagnostic_path else None, gui='edgechromium', debug=False,
                      http_server=True, private_mode=True)
        if getattr(api, '_startup_error', False):
            raise RuntimeError('WebView2 is required')
    except Exception as exc:
        if diagnostic_path:
            Path(diagnostic_path).write_text(json.dumps({'result': 'error', 'message': 'HTML 窗口未能完成启动。'}), encoding='utf-8')
            raise
        from .page_controller import PageSetupError
        message = (str(exc) if isinstance(exc, PageSetupError) else
                   '新版界面未能启动。请确认软件文件完整，并已安装 Microsoft Edge WebView2 运行时。')
        ctypes.windll.user32.MessageBoxW(None, message,
            'ClipGo 视频号', 0x10)
    finally:
        api._shutdown()
        lock.close()
