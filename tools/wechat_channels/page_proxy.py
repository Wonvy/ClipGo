"""Isolated, narrowly scoped HTTPS page rewriter for the Channels button.

This worker never installs a certificate or selects a Windows proxy. Those
operations belong to the controller. It modifies only supported Channels HTML
and JavaScript, serves same-origin assets, and relays bounded button messages
to an authenticated loopback bridge. Media and unrelated TLS hosts are tunneled.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zlib

from .capture import ParentMonitor, tls_failure_reason

CHANNELS_HOST = 'channels.weixin.qq.com'
SCRIPT_HOST = 'res.wx.qq.com'
ORIGIN = 'https://' + CHANNELS_HOST
ALLOW_HOSTS_PATTERN = r'^(?:channels\.weixin\.qq\.com|res\.wx\.qq\.com)(?::443)?$'
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_BYTES = 128 * 1024
MAX_REPLY_BYTES = 64 * 1024
PAGE_PATHS = frozenset({'/web/pages/home', '/web/pages/feed'})
JS_TYPES = frozenset({'application/javascript', 'text/javascript', 'application/x-javascript'})
_HEX_SECRET = re.compile(r'[0-9a-fA-F]{32,128}\Z')
_PUBLIC_ASSET_PATH = re.compile(r'/t/wx_fed/[A-Za-z0-9._~/-]{1,900}\.js\Z')
_PUBLIC_ASSET_NAMES = {
    'flow': re.compile(r'virtual_svg-icons-register\.publish(?:\.[A-Za-z0-9_-]{4,64})?\.js\Z'),
    'index': re.compile(r'index\.publish(?:\.[A-Za-z0-9_-]{4,64})?\.js\Z'),
}
_HOOK_MARKERS = {'track_hooks': 'globalThis.__clipgoPage.track(',
                 'wrap_hooks': 'globalThis.__clipgoPage.wrap(',
                 'detail_hooks': 'globalThis.__clipgoPage.detail('}


def validate_config(config):
    """Reject destinations/paths that could turn IPC into an arbitrary fetch."""
    if not isinstance(config, dict):
        raise ValueError('Invalid page service configuration')
    clean = dict(config)
    for field in ('token', 'bridge_secret'):
        if not isinstance(clean.get(field), str) or not _HEX_SECRET.fullmatch(clean[field]):
            raise ValueError('Invalid page service credential')
    for field in ('listen_port', 'parent_pid'):
        if type(clean.get(field)) is not int or clean[field] <= 0:
            raise ValueError('Invalid page service process or port')
    if clean['listen_port'] > 65535 or clean['parent_pid'] >= 2**32:
        raise ValueError('Invalid page service process or port')
    try:
        url = urlsplit(clean['bridge_url'])
        valid = (url.scheme == 'http' and url.hostname == '127.0.0.1'
                 and url.port is not None and 0 < url.port <= 65535
                 and not url.username and not url.password
                 and url.path in ('', '/') and not url.query and not url.fragment)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('Invalid page bridge address')
    clean['bridge_url'] = clean['bridge_url'].rstrip('/')
    for field in ('confdir', 'status_path', 'stop_path'):
        if not isinstance(clean.get(field), str) or not Path(clean[field]).is_absolute():
            raise ValueError('Page service paths must be absolute')
    recovery = clean.get('recovery_path')
    if recovery is not None and (not isinstance(recovery, str) or not Path(recovery).is_absolute()):
        raise ValueError('Page recovery path must be absolute')
    return clean


def load_config(path):
    data = Path(path).read_bytes()
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError('Page service configuration is too large')
    return validate_config(json.loads(data))


def build_options(config):
    config = validate_config(config)
    return {
        'mode': ['regular'], 'listen_host': '127.0.0.1',
        'listen_port': config['listen_port'], 'confdir': config['confdir'],
        'allow_hosts': [ALLOW_HOSTS_PATTERN], 'connection_strategy': 'lazy',
        'block_global': True,
        'stream_large_bodies': str(MAX_TEXT_BYTES), 'store_streamed_bodies': False,
        'ssl_insecure': False,
    }


def _response(status, body, content_type='application/json; charset=utf-8'):
    from mitmproxy import http
    if not isinstance(body, bytes):
        body = body.encode('utf-8')
    return http.Response.make(status, body, {
        'Content-Type': content_type, 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    })


def _json_response(status, **body):
    return _response(status, json.dumps(body, ensure_ascii=False, separators=(',', ':')))


def _bounded_text(response):
    """Decode compression ourselves, avoiding an unbounded decompression step."""
    raw = response.raw_content
    if raw is None or len(raw) > MAX_TEXT_BYTES:
        return None
    encoding = response.headers.get('content-encoding', 'identity').strip().lower()
    try:
        if encoding in ('gzip', 'deflate'):
            decoder = zlib.decompressobj(31 if encoding == 'gzip' else zlib.MAX_WBITS)
            raw = decoder.decompress(raw, MAX_TEXT_BYTES + 1)
            if len(raw) > MAX_TEXT_BYTES or not decoder.eof or decoder.unused_data:
                return None
        elif encoding == 'br':
            import brotli
            decoder = brotli.Decompressor()
            raw = decoder.process(raw, output_buffer_limit=MAX_TEXT_BYTES + 1)
            if len(raw) > MAX_TEXT_BYTES or not decoder.is_finished():
                return None
        elif encoding not in ('identity', ''):
            return None
        if len(raw) > MAX_TEXT_BYTES:
            return None
        # Channels assets are UTF-8. An unexpected encoding is passed through.
        return raw.decode('utf-8-sig')
    except (ValueError, UnicodeError, zlib.error, ImportError, TypeError):
        return None
    except Exception:
        # Brotli exposes its own error class. Never surface response contents.
        return None


class PageProxy:
    def __init__(self, config, *, html_rewriter=None, script_rewriter=None,
                 bridge_request=None, script_path=None):
        self.config = validate_config(config)
        self.base_path = '/__clipgo/' + self.config['token']
        self.html_rewriter = html_rewriter
        self.script_rewriter = script_rewriter
        self.bridge_request = bridge_request or self._bridge_request
        self.script_path = Path(script_path) if script_path else self._asset_path()
        self.parent = None
        self.watchdog = None
        self.master = None
        self._saved_at = 0.0
        self._orphaned = False
        self.state = {
            'status': 'starting', 'server_ready': False,
            'html_responses': 0, 'script_responses': 0,
            'html_rewrites': 0, 'script_rewrites': 0,
            'track_hooks': 0, 'wrap_hooks': 0, 'detail_hooks': 0,
            'script_hook_assets': 0, 'cache_only_rewrites': 0,
            'assets_served': 0, 'pac_requests': 0, 'page_events': 0,
            'download_requests': 0, 'bridge_failures': 0,
            'tls_client_errors': 0, 'tls_server_errors': 0, 'http_errors': 0,
            'skipped_large': 0, 'rewrite_errors': 0, 'last_error': '',
            'page_path': '',
            'public_flow_script_path': '', 'public_index_script_path': '',
        }

    @staticmethod
    def _asset_path():
        if getattr(sys, 'frozen', False):
            return Path(sys._MEIPASS) / 'page_assets' / 'embed.js'
        return Path(__file__).with_name('page_assets') / 'embed.js'

    def _save(self, force=False):
        now = time.monotonic()
        if not force and now - self._saved_at < .2:
            return True
        path = Path(self.config['status_path'])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + '.tmp')
            temporary.write_text(json.dumps(self.state), encoding='utf-8')
            os.replace(temporary, path)
            self._saved_at = now
            return True
        except OSError:
            return False

    def _shutdown(self):
        if self.master is not None:
            self.master.shutdown()
        else:
            from mitmproxy import ctx
            ctx.master.shutdown()

    async def _recover_orphan(self):
        path = self.config.get('recovery_path')
        if not path:
            return
        try:
            from .page_controller import recover_session
            await asyncio.to_thread(recover_session, path, expected_token=self.config['token'])
        except Exception:
            self.state['last_error'] = 'recovery_failed'
            self._save(force=True)

    async def _watch_lifecycle(self):
        while True:
            try:
                stopped = Path(self.config['stop_path']).exists()
                orphaned = self.parent is not None and self.parent.gone()
                if stopped or orphaned:
                    self._orphaned = orphaned
                    if orphaned:
                        await self._recover_orphan()
                    self._shutdown()
                    return
            except OSError:
                self._shutdown()
                return
            await asyncio.sleep(.25)

    def running(self):
        self.parent = ParentMonitor(str(self.config['parent_pid']))
        if self.parent.gone():
            self._orphaned = True
            self.watchdog = asyncio.create_task(self._recover_then_shutdown())
            return
        self.state.update(status='listening', server_ready=True)
        if not self._save(force=True):
            self._shutdown()
            return
        self.watchdog = asyncio.create_task(self._watch_lifecycle())

    async def _recover_then_shutdown(self):
        await self._recover_orphan()
        self._shutdown()

    def done(self):
        if self.watchdog is not None:
            self.watchdog.cancel()
            self.watchdog = None
        if self.parent is not None:
            self.parent.close()
            self.parent = None
        self.state.update(status='stopped', server_ready=False)
        self._save(force=True)

    def _route(self, request):
        try:
            url = urlsplit(request.url)
            if url.username or url.password or url.fragment:
                return None
            if url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port == self.config['listen_port']:
                return 'pac' if url.path == self.base_path + '/proxy.pac' and not url.query else 'local-denied'
            if url.scheme == 'https' and url.hostname == CHANNELS_HOST and url.port in (None, 443):
                if url.path.startswith('/__clipgo/'):
                    if not url.path.startswith(self.base_path + '/'):
                        return 'denied'
                    suffix = url.path[len(self.base_path) + 1:]
                    return suffix if suffix in ('embed.js', 'event', 'download', 'status') and not url.query else 'denied'
        except (ValueError, TypeError):
            pass
        return None

    def requestheaders(self, flow):
        route = self._route(flow.request)
        if route is None:
            # Prevent a previously cached unmodified page/script being used in
            # place of a response we need to adapt. No media request is touched.
            try:
                parsed = urlsplit(flow.request.url)
                targeted = ((parsed.hostname == CHANNELS_HOST and parsed.path in PAGE_PATHS)
                            or (parsed.hostname == SCRIPT_HOST and parsed.path.endswith('.js')))
                if targeted:
                    for name in ('if-none-match', 'if-modified-since'):
                        flow.request.headers.pop(name, None)
            except ValueError:
                pass
            return
        flow.metadata['clipgo_owned'] = True
        # Our browser sends small, non-compressed JSON with a Content-Length.
        # Reject an unbounded body before mitmproxy can buffer or forward it.
        if flow.request.method in ('POST', 'PUT', 'PATCH'):
            try:
                size = int(flow.request.headers.get('content-length', '-1'))
            except ValueError:
                size = -1
            if not 0 <= size <= MAX_MESSAGE_BYTES or flow.request.headers.get('transfer-encoding'):
                flow.metadata['clipgo_rejected'] = True
                flow.kill()

    async def request(self, flow):
        route = self._route(flow.request)
        if route is None or flow.metadata.get('clipgo_rejected'):
            return
        flow.metadata['clipgo_owned'] = True
        req = flow.request
        if route == 'pac':
            if req.method not in ('GET', 'HEAD'):
                flow.response = _json_response(405, ok=False)
                return
            body = ('function FindProxyForURL(url, host) {\n'
                    '  host = host.toLowerCase();\n'
                    '  if (host === "channels.weixin.qq.com" || host === "res.wx.qq.com") '
                    f'return "PROXY 127.0.0.1:{self.config["listen_port"]}; DIRECT";\n'
                    '  return "DIRECT";\n}\n')
            flow.response = _response(200, '' if req.method == 'HEAD' else body,
                                      'application/x-ns-proxy-autoconfig')
            self.state['pac_requests'] += 1
            self._save()
            return
        if route in ('denied', 'local-denied'):
            flow.response = _json_response(404, ok=False)
            return
        origin = req.headers.get('origin')
        fetch_site = req.headers.get('sec-fetch-site')
        if (origin is not None and origin != ORIGIN) or fetch_site == 'cross-site':
            flow.response = _json_response(403, ok=False)
            return
        if route == 'embed.js':
            if req.method != 'GET':
                flow.response = _json_response(405, ok=False)
                return
            try:
                script = self.script_path.read_bytes()
                if len(script) > MAX_TEXT_BYTES:
                    raise ValueError('Asset too large')
                flow.response = _response(200, script, 'application/javascript; charset=utf-8')
                self.state['assets_served'] += 1
            except (OSError, ValueError):
                flow.response = _json_response(503, ok=False, message='页内按钮尚未准备完成。')
                self.state['last_error'] = 'asset_unavailable'
            self._save()
            return
        expected_method = 'GET' if route == 'status' else 'POST'
        if req.method != expected_method:
            flow.response = _json_response(405, ok=False)
            return
        if req.method == 'POST' and origin != ORIGIN:
            flow.response = _json_response(403, ok=False)
            return
        body = req.raw_content or b''
        if len(body) > MAX_MESSAGE_BYTES or req.headers.get('content-encoding', 'identity') not in ('', 'identity'):
            flow.response = _json_response(413, ok=False)
            return
        if req.method == 'POST':
            content_type = req.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            try:
                valid_json = content_type == 'application/json' and isinstance(json.loads(body), dict)
            except (ValueError, UnicodeError, RecursionError):
                valid_json = False
            if not valid_json:
                flow.response = _json_response(400, ok=False)
                return
        try:
            result = await asyncio.to_thread(self.bridge_request, route, req.method, body)
            flow.response = _response(200, result)
            if route == 'event':
                self.state['page_events'] += 1
            elif route == 'download':
                self.state['download_requests'] += 1
        except Exception:
            flow.response = _json_response(503, ok=False, message='ClipGo 暂时无法连接，请重新打开工具。')
            self.state['bridge_failures'] += 1
            self.state['last_error'] = 'bridge_unavailable'
        self._save()

    def _bridge_request(self, endpoint, method, body):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        req = urllib.request.Request(self.config['bridge_url'] + '/' + endpoint,
            data=body if method == 'POST' else None, method=method,
            headers={'X-ClipGo-Secret': self.config['bridge_secret'],
                     'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
        try:
            response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(MAX_REPLY_BYTES + 1)
            if len(raw) > MAX_REPLY_BYTES or not isinstance(json.loads(raw), dict):
                raise ValueError('Invalid page bridge response')
            return raw

    def responseheaders(self, flow):
        if flow.response is None or flow.metadata.get('clipgo_owned'):
            return
        response = flow.response
        response.stream = True
        try:
            url = urlsplit(flow.request.url)
        except ValueError:
            return
        if url.scheme != 'https' or url.port not in (None, 443) or response.status_code != 200:
            return
        content_type = response.headers.get('content-type', '').split(';', 1)[0].lower().strip()
        kind = None
        if url.hostname == CHANNELS_HOST and url.path in PAGE_PATHS and content_type == 'text/html':
            kind = 'html'
            self.state['html_responses'] += 1
            self.state['page_path'] = url.path
        elif url.hostname == SCRIPT_HOST and url.path.endswith('.js') and content_type in JS_TYPES:
            kind = 'script'
            self.state['script_responses'] += 1
            # Only known public CDN build assets may be named in diagnostics.
            # Queries, headers and response bodies are never saved. These paths
            # let a developer fetch the public source independently of WeChat.
            if _PUBLIC_ASSET_PATH.fullmatch(url.path) and '..' not in url.path.split('/'):
                basename = url.path.rsplit('/', 1)[-1]
                for group, pattern in _PUBLIC_ASSET_NAMES.items():
                    if pattern.fullmatch(basename):
                        self.state['public_' + group + '_script_path'] = url.path
        if kind is None or response.headers.get('content-range'):
            return
        try:
            size = int(response.headers.get('content-length', '0'))
        except ValueError:
            return
        if size > MAX_TEXT_BYTES or size < 0:
            self.state['skipped_large'] += 1
            self._save()
            return
        flow.metadata['clipgo_rewrite'] = kind
        response.stream = False
        self._save()

    def response(self, flow):
        kind = flow.metadata.pop('clipgo_rewrite', None)
        if kind is None or flow.response is None or flow.metadata.get('clipgo_owned'):
            return
        text = _bounded_text(flow.response)
        if text is None:
            self.state['skipped_large'] += 1
            self._save()
            return
        try:
            from . import page_injection
            if kind == 'html':
                rewrite = self.html_rewriter or page_injection.rewrite_html
                updated = rewrite(text, {'basePath': self.base_path})
            else:
                rewrite = self.script_rewriter or page_injection.rewrite_script
                updated = rewrite(text, flow.request.url)
            if updated is None or updated == text:
                return
            encoded = updated.encode('utf-8')
            if len(encoded) > MAX_TEXT_BYTES + 1024 * 1024:
                raise ValueError('Rewritten asset too large')
            # Store UTF-8 bytes directly; do not recompress twice or retain the
            # upstream signature/cache validators for changed content.
            for name in ('content-encoding', 'content-length', 'etag', 'last-modified',
                         'content-md5', 'digest'):
                flow.response.headers.pop(name, None)
            flow.response.raw_content = encoded
            flow.response.headers['content-length'] = str(len(encoded))
            flow.response.headers['cache-control'] = 'no-store'
            self.state[kind + '_rewrites'] += 1
            if kind == 'script':
                added = {key: max(0, updated.count(marker) - text.count(marker))
                         for key, marker in _HOOK_MARKERS.items()}
                for key, count in added.items():
                    self.state[key] += count
                self.state['script_hook_assets' if any(added.values()) else 'cache_only_rewrites'] += 1
        except Exception:
            self.state['rewrite_errors'] += 1
            self.state['last_error'] = 'rewrite_failed'
        self._save()

    def _tls_failure(self, data, side):
        conn = getattr(data, 'conn', None)
        host = getattr(conn, 'sni', None)
        if host not in (CHANNELS_HOST, SCRIPT_HOST):
            return
        self.state['tls_' + side + '_errors'] += 1
        self.state['last_error'] = tls_failure_reason(getattr(conn, 'error', None))
        self._save()

    def tls_failed_client(self, data):
        self._tls_failure(data, 'client')

    def tls_failed_server(self, data):
        self._tls_failure(data, 'server')

    def error(self, flow):
        try:
            host = urlsplit(flow.request.url).hostname
        except (AttributeError, ValueError):
            return
        if host in (CHANNELS_HOST, SCRIPT_HOST):
            self.state['http_errors'] += 1
            self.state['last_error'] = 'page_connection_failed'
            self._save()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(config_path):
    """Run a regular local proxy; ownership/recovery remain with the controller."""
    config = load_config(config_path)
    # No request bodies, signed URLs, cookies, TLS exception strings or flow
    # dumps are written to stdout/stderr or a mitmproxy log.
    logging.disable(logging.CRITICAL)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

    async def run():
        from mitmproxy.options import Options
        from mitmproxy.tools.dump import DumpMaster
        addon = PageProxy(config)
        try:
            values = build_options(config)
            initial = {key: values.pop(key) for key in ('mode', 'listen_host', 'listen_port', 'confdir', 'allow_hosts')}
            master = DumpMaster(Options(**initial), with_termlog=False, with_dumper=False)
            master.options.update(**values)
            addon.master = master
            master.addons.add(addon)
            await master.run()
        except Exception:
            addon.state.update(status='error', server_ready=False, last_error='service_failed')
            addon._save(force=True)
            raise SystemExit(2) from None
    asyncio.run(run())
