"""Private loopback IPC from the page proxy to the existing download host.

Only the proxy knows this server's per-run secret. The WeChat page uses the
proxy's same-origin endpoints; this server never exposes a browser API, an
arbitrary URL fetcher, a file path, cookies or the general pywebview API.
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl, urlsplit

from .download import DownloadError, validate_url
from .engagement import engagement_from_feed
from .loaded_urls import usable_video_url
from .memory import loaded_identity
from .thumbnails import valid_thumbnail_url

MAX_BODY = 128 * 1024
MAX_REJECT_DRAIN = 2 * MAX_BODY
REJECT_DRAIN_SECONDS = .25
MAX_URL = 16384
MAX_SAFE_INTEGER = (1 << 53) - 1
_ID = re.compile(r'[a-f0-9]{24}')
_STATES = frozenset({'idle', 'pending', 'downloading', 'cancelling', 'cancelled', 'done', 'error'})


class PageDataError(ValueError):
    """Fixed user-safe errors; never include input values in an exception."""


def _text(value, maximum):
    return value[:maximum].replace('\x00', '') if isinstance(value, str) else ''


def _key(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PageDataError('视频解码信息无效，请重新播放后重试。')
    # A JS Number may already have lost bits before JSON serialization.
    if isinstance(value, int) and not 0 <= value <= MAX_SAFE_INTEGER:
        raise PageDataError('视频解码信息精度不足，请重新播放后重试。')
    text = str(value)
    if not re.fullmatch(r'[0-9]{1,20}', text) or int(text) >= 1 << 64:
        raise PageDataError('视频解码信息无效，请重新播放后重试。')
    return text


def _exact_url(media):
    base = media.get('url')
    if not isinstance(base, str) or len(base) > MAX_URL:
        raise PageDataError('页面尚未提供有效的视频地址。')
    try:
        validate_url(base)
    except DownloadError:
        raise PageDataError('页面提供的视频地址不受支持。') from None
    suffix = media.get('urlToken', '')
    if suffix is None:
        suffix = ''
    if not isinstance(suffix, str) or len(suffix) > 8192:
        raise PageDataError('视频地址信息无效，请重新播放后重试。')
    if suffix:
        # This is a literal query suffix belonging to this exact media object.
        # Do not decode/re-encode signed values or borrow another object's key.
        if suffix[0] not in '&?' or '#' in suffix or any(ord(c) < 33 or ord(c) > 126 for c in suffix):
            raise PageDataError('视频地址信息无效，请重新播放后重试。')
        if (suffix[0] == '?' and '?' in base) or (suffix[0] == '&' and '?' not in base):
            raise PageDataError('视频地址信息无效，请重新播放后重试。')
    url = base + suffix
    if not usable_video_url(url):
        raise PageDataError('页面尚未提供完整的视频地址，请播放后重试。')
    try:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True, max_num_fields=128)
    except ValueError:
        raise PageDataError('视频地址信息无效，请重新播放后重试。') from None
    keys = [name.lower() for name, _value in pairs]
    if len(set(keys)) != len(keys):
        raise PageDataError('视频地址信息有重复，请重新播放后重试。')
    return url


def candidate_from_feed(feed):
    """Accept one bounded raw-shaped feed, never search neighboring objects."""
    if not isinstance(feed, dict):
        raise PageDataError('尚未识别当前视频，请播放后重试。')
    desc = feed.get('objectDesc')
    if not isinstance(desc, dict):
        raise PageDataError('尚未识别当前视频，请播放后重试。')
    media_list = desc.get('media')
    if not isinstance(media_list, list) or not media_list or not isinstance(media_list[0], dict):
        raise PageDataError('尚未识别当前视频，请播放后重试。')
    media = media_list[0]
    media_type = media.get('mediaType', desc.get('mediaType'))
    if media_type is not None and (isinstance(media_type, bool) or media_type not in (4, '4')):
        raise PageDataError('当前内容不是受支持的视频。')
    url = _exact_url(media)
    key = _key(media.get('decodeKey'))
    duration = media.get('videoPlayLen', media.get('duration'))
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not 0 <= duration < 10_000_000):
        duration = None
    cover = ''
    for field in ('thumbUrl', 'coverUrl'):
        value = media.get(field)
        if valid_thumbnail_url(value):
            cover = value
            break
    contact = feed.get('contact')
    author = _text(contact.get('nickname'), 120) if isinstance(contact, dict) else ''
    feed_id = feed.get('id', feed.get('objectId'))
    if isinstance(feed_id, int) and not isinstance(feed_id, bool) and 0 <= feed_id <= MAX_SAFE_INTEGER:
        feed_id = str(feed_id)
    if not isinstance(feed_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', feed_id):
        feed_id = ''
    return {'id': loaded_identity(url), 'page_feed_id': feed_id,
            'url': url, 'decode_key': key,
            'title': _text(desc.get('description'), 4096) or '视频号视频',
            'author': author, 'duration': duration, 'thumbnail_url': cover,
            'engagement': engagement_from_feed(feed), 'source': 'page', 'seen_at': time.time()}


def _message(value, fallback='请在 ClipGo 窗口查看下载状态。'):
    if not isinstance(value, str):
        return fallback
    value = value[:256].replace('\x00', '')
    if re.search(r'https?://|[A-Za-z]:[\\/]|(?:token|decode_?key|encfilekey)\s*[=:]', value, re.I):
        return fallback
    return value


def _safe_response(value, *, default_ok=True):
    """Return an explicit public shape, even if a callback returns all state."""
    value = value if isinstance(value, dict) else {}
    result = {'ok': value.get('ok', default_ok) is True}
    if 'message' in value:
        result['message'] = _message(value['message'])
    for key in ('id', 'current_id'):
        ident = value.get(key)
        if isinstance(ident, str) and _ID.fullmatch(ident):
            result[key] = ident
    task = value.get('download')
    if isinstance(task, dict):
        state = task.get('state')
        if not isinstance(state, str) or state not in _STATES:
            state = 'idle'
        download = {'state': state, 'message': _message(task.get('message', ''))}
        ident = task.get('id')
        download['id'] = ident if isinstance(ident, str) and _ID.fullmatch(ident) else None
        percent = task.get('percent')
        download['percent'] = (min(100, max(0, percent)) if type(percent) in (int, float)
                               and 0 <= percent <= 100 else 0)
        for field in ('received', 'total'):
            number = task.get(field)
            if type(number) is int and 0 <= number <= MAX_SAFE_INTEGER:
                download[field] = number
        result['download'] = download
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON property')
        result[key] = value
    return result


def _discard_rejected_body(headers, stream, connection, consumed=0):
    """Avoid a TCP reset on early rejection, without trusting an unlimited body."""
    lengths = headers.get_all('Content-Length', [])
    if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,8}', lengths[0]):
        return
    remaining = min(MAX_REJECT_DRAIN, max(0, int(lengths[0]) - consumed))
    if not remaining:
        return
    deadline = time.monotonic() + REJECT_DRAIN_SECONDS
    original_timeout = connection.gettimeout()
    try:
        while remaining:
            available_time = deadline - time.monotonic()
            if available_time <= 0:
                break
            connection.settimeout(available_time)
            # read1 performs at most one underlying read, unlike read(n), so
            # a slowly trickling body cannot repeatedly renew this deadline.
            chunk = stream.read1(min(8192, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
    except (OSError, ValueError):
        pass
    finally:
        connection.settimeout(original_timeout)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # BaseServer normally prints callback tracebacks and their local values.
        pass


class PageBridge:
    """One secret, one bound listener, no disk persistence or browser exposure."""
    def __init__(self, *, on_candidate, get_status, on_event=None):
        self._on_candidate = on_candidate
        self._get_status = get_status
        self._on_event = on_event
        self.secret = secrets.token_hex(32)
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._active = threading.Event()

    @property
    def base_url(self):
        with self._lock:
            if self._server is None:
                return None
            return f'http://127.0.0.1:{self._server.server_port}'

    def start(self):
        with self._lock:
            if self._server is None:
                bridge = self

                class Handler(BaseHTTPRequestHandler):
                    protocol_version = 'HTTP/1.0'
                    server_version = 'ClipGo'

                    def setup(self):
                        self.request.settimeout(3)
                        self._body_bytes_read = 0
                        super().setup()

                    def log_message(self, *_args):
                        pass

                    def _reply(self, status, data):
                        if status >= 400:
                            # Closing with unread inbound data can send RST on
                            # Windows and discard the HTTP error at the client.
                            _discard_rejected_body(self.headers, self.rfile, self.request,
                                                   self._body_bytes_read)
                        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
                        self.send_response(status)
                        self.send_header('Content-Type', 'application/json; charset=utf-8')
                        self.send_header('Content-Length', str(len(body)))
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.wfile.write(body)
                        self.close_connection = True

                    def _authorized(self):
                        expected_host = f'127.0.0.1:{self.server.server_port}'
                        secrets_received = self.headers.get_all('X-ClipGo-Secret', [])
                        authorized = (bridge._active.is_set()
                                      and self.headers.get_all('Host', []) == [expected_host]
                                      and self.headers.get('Origin') is None
                                      and len(secrets_received) == 1
                                      and re.fullmatch(r'[a-f0-9]{64}', secrets_received[0]) is not None
                                      and hmac.compare_digest(secrets_received[0], bridge.secret))
                        if not authorized:
                            self._reply(403, {'ok': False, 'message': '页面连接未授权。'})
                        return authorized

                    def _body(self):
                        lengths = self.headers.get_all('Content-Length', [])
                        content_type = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                        if self.headers.get('Transfer-Encoding') is not None or len(lengths) != 1:
                            raise PageDataError('页面请求格式无效。')
                        if content_type != 'application/json' or not re.fullmatch(r'[0-9]{1,8}', lengths[0]):
                            raise PageDataError('页面请求格式无效。')
                        length = int(lengths[0])
                        if not 0 < length <= MAX_BODY:
                            raise PageDataError('页面请求过大或为空。')
                        raw = self.rfile.read(length)
                        self._body_bytes_read += len(raw)
                        if len(raw) != length:
                            raise PageDataError('页面请求不完整。')
                        try:
                            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                                              parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
                        except (ValueError, UnicodeError, RecursionError):
                            raise PageDataError('页面请求格式无效。') from None
                        if not isinstance(body, dict):
                            raise PageDataError('页面请求格式无效。')
                        return body

                    def do_GET(self):
                        if not self._authorized():
                            return
                        if self.path != '/status':
                            self._reply(404, {'ok': False})
                            return
                        try:
                            self._reply(200, _safe_response(bridge._get_status()))
                        except Exception:
                            self._reply(500, {'ok': False, 'message': '暂时无法读取下载状态。'})

                    def do_POST(self):
                        if not self._authorized():
                            return
                        if self.path not in ('/event', '/download'):
                            self._reply(404, {'ok': False})
                            return
                        try:
                            body = self._body()
                            if self.path == '/download':
                                if set(body) != {'feed'}:
                                    raise PageDataError('页面下载请求格式无效。')
                                candidate = candidate_from_feed(body.get('feed'))
                                result = _safe_response(bridge._on_candidate(candidate, download=True))
                                result.setdefault('id', candidate['id'])
                            else:
                                if set(body) - {'kind', 'feed', 'buttons', 'active', 'position', 'page', 'placement',
                                                'source_count', 'candidate_count', 'hook_count', 'video_count', 'vue_components',
                                                'selected_index_valid', 'vue_component', 'vue_feed', 'detail_seen'}:
                                    raise PageDataError('页面事件格式无效。')
                                kind = body.get('kind')
                                if kind not in ('ready', 'current', 'diagnostic'):
                                    raise PageDataError('页面事件无效。')
                                event = {'kind': kind, 'has_feed': False}
                                result = {'ok': True}
                                if kind == 'current' and body.get('feed') is not None:
                                    candidate = candidate_from_feed(body['feed'])
                                    bridge._on_candidate(candidate, download=False)
                                    event.update(has_feed=True, id=candidate['id'])
                                    result['id'] = candidate['id']
                                if kind == 'diagnostic':
                                    if type(body.get('buttons')) is int and 0 <= body['buttons'] <= 32:
                                        event['buttons'] = body['buttons']
                                    if type(body.get('active')) is bool:
                                        event['active'] = body['active']
                                    if body.get('position') in ('toolbar', 'sidebar', 'none'):
                                        event['position'] = body['position']
                                    if body.get('placement') in ('inline', 'floating', 'none'):
                                        event['placement'] = body['placement']
                                    for field in ('source_count', 'candidate_count', 'hook_count', 'video_count', 'vue_components'):
                                        if type(body.get(field)) is int and 0 <= body[field] <= 1_000_000:
                                            event[field] = body[field]
                                    for field in ('selected_index_valid', 'vue_component', 'vue_feed', 'detail_seen'):
                                        if type(body.get(field)) is bool:
                                            event[field] = body[field]
                                if body.get('page') in ('/web/pages/home', '/web/pages/feed'):
                                    event['page'] = body['page']
                                if bridge._on_event:
                                    bridge._on_event(event)
                            self._reply(200, result)
                        except PageDataError as exc:
                            self._reply(400, {'ok': False, 'message': str(exc)})
                        except Exception:
                            self._reply(500, {'ok': False, 'message': '页面操作未完成，请重新播放后重试。'})

                    def do_OPTIONS(self):
                        self._reply(403, {'ok': False})

                self._server = _Server(('127.0.0.1', 0), Handler)
                self._active.set()
                self._thread = threading.Thread(target=self._server.serve_forever,
                                                kwargs={'poll_interval': .1}, daemon=True,
                                                name='ClipGo page bridge')
                self._thread.start()
            return f'http://127.0.0.1:{self._server.server_port}'

    def close(self):
        with self._lock:
            self._active.clear()
            server, thread = self._server, self._thread
            self._server = self._thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
