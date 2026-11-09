import asyncio
import gzip
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wechat_channels import page_proxy

try:
    from mitmproxy import http
except ImportError:
    http = None


def configuration(root):
    return {'token': 'a' * 64, 'bridge_secret': 'b' * 64,
            'bridge_url': 'http://127.0.0.1:44557', 'listen_port': 44556,
            'parent_pid': 1234, 'confdir': str(root / 'cert'),
            'status_path': str(root / 'status.json'), 'stop_path': str(root / 'stop')}


@unittest.skipIf(http is None, 'mitmproxy is an optional page service dependency')
class PageProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = configuration(self.root)
        self.asset = self.root / 'embed.js'
        self.asset.write_text('window.clipgoPageLoaded = true;', encoding='utf-8')
        self.ipc = Mock(return_value=b'{"ok":true}')
        self.addon = page_proxy.PageProxy(self.config, script_path=self.asset,
            bridge_request=self.ipc, html_rewriter=lambda body, cfg: body + '<!-- injected -->',
            script_rewriter=lambda body, url: body + ';/* wrapped */')

    def flow(self, url, *, method='GET', body=b'', headers=None, response=None):
        req = http.Request.make(method, url, body, headers or {})
        return SimpleNamespace(request=req, response=response, metadata={}, kill=Mock())

    def owned(self, endpoint, **kwargs):
        return self.flow(page_proxy.ORIGIN + self.addon.base_path + '/' + endpoint, **kwargs)

    def serve(self, flow):
        self.addon.requestheaders(flow)
        asyncio.run(self.addon.request(flow))
        return flow.response

    def test_only_two_hosts_are_intercepted_and_listener_is_loopback(self):
        options = page_proxy.build_options(self.config)
        self.assertEqual(options['listen_host'], '127.0.0.1')
        self.assertEqual(options['mode'], ['regular'])
        self.assertEqual(options['connection_strategy'], 'lazy')
        self.assertFalse(options['ssl_insecure'])
        pattern = re.compile(options['allow_hosts'][0])
        for host in ('channels.weixin.qq.com', 'res.wx.qq.com:443'):
            self.assertTrue(pattern.fullmatch(host))
        for host in ('finder.video.qq.com', 'channels.weixin.qq.com.evil.test',
                     'channels.weixin.qq.com:444', 'chat.weixin.qq.com'):
            self.assertFalse(pattern.fullmatch(host))

    def test_config_rejects_remote_or_credentialled_bridge_and_unbounded_paths(self):
        for bad in ('https://127.0.0.1:44557', 'http://localhost:44557',
                    'http://127.0.0.1:44557/secret', 'http://user@127.0.0.1:44557',
                    'http://127.0.0.1:44557?x=1', 'http://example.test:44557'):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                page_proxy.validate_config(dict(self.config, bridge_url=bad))
        for change in ({'listen_port': True}, {'parent_pid': 0}, {'token': 'guess'},
                       {'status_path': 'relative.json'}, {'recovery_path': 'relative.json'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                page_proxy.validate_config(dict(self.config, **change))

    def test_pac_is_literal_capability_on_loopback_and_only_targets_page_hosts(self):
        url = f'http://127.0.0.1:{self.config["listen_port"]}{self.addon.base_path}/proxy.pac'
        response = self.serve(self.flow(url))
        self.assertEqual(response.status_code, 200)
        text = response.get_text()
        self.assertIn('host === "channels.weixin.qq.com"', text)
        self.assertIn('host === "res.wx.qq.com"', text)
        self.assertIn('PROXY 127.0.0.1:44556; DIRECT', text)
        self.assertNotIn('finder.video.qq.com', text)
        self.assertNotIn('isInNet', text)
        self.assertEqual(self.addon.state['pac_requests'], 1)
        denied = self.serve(self.flow(url.replace('a' * 64, 'c' * 64)))
        self.assertEqual(denied.status_code, 404)
        self.ipc.assert_not_called()

    def test_asset_is_same_origin_exact_token_without_ipc_or_upstream(self):
        response = self.serve(self.owned('embed.js'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.raw_content, self.asset.read_bytes())
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.addon.state['assets_served'], 1)
        self.ipc.assert_not_called()
        wrong = self.owned('embed.js')
        wrong.request.path = wrong.request.path.replace('a' * 64, 'c' * 64)
        self.assertEqual(self.serve(wrong).status_code, 404)

    def test_download_forwards_bounded_json_without_cookies(self):
        body = b'{"feed":{"id":"synthetic"}}'
        flow = self.owned('download', method='POST', body=body, headers={
            'Origin': page_proxy.ORIGIN, 'Content-Type': 'application/json',
            'Cookie': 'must-never-forward'})
        response = self.serve(flow)
        self.assertEqual(response.status_code, 200)
        self.ipc.assert_called_once_with('download', 'POST', body)
        self.assertEqual(self.addon.state['download_requests'], 1)

    def test_forbidden_origin_method_and_json_never_reach_bridge(self):
        cases = [
            self.owned('download', method='POST', body=b'{}', headers={'Content-Type': 'application/json'}),
            self.owned('download', method='POST', body=b'{}', headers={
                'Origin': 'https://evil.test', 'Content-Type': 'application/json'}),
            self.owned('download'),
            self.owned('download', method='POST', body=b'[]', headers={
                'Origin': page_proxy.ORIGIN, 'Content-Type': 'application/json'}),
            self.owned('status', headers={'Sec-Fetch-Site': 'cross-site'}),
        ]
        for flow in cases:
            self.assertIn(self.serve(flow).status_code, (400, 403, 405))
        self.ipc.assert_not_called()

    def test_oversized_or_unbounded_owned_request_is_killed_before_forwarding(self):
        for content_length in ('-1', 'bad', str(page_proxy.MAX_MESSAGE_BYTES + 1)):
            flow = self.owned('download', method='POST', body=b'{}')
            flow.request.headers['content-length'] = content_length
            self.serve(flow)
            flow.kill.assert_called_once()
        self.ipc.assert_not_called()

    def test_bridge_errors_and_tls_errors_do_not_export_private_strings(self):
        self.ipc.side_effect = RuntimeError('url?token=private Cookie=secret')
        response = self.serve(self.owned('status'))
        self.assertEqual(response.status_code, 503)
        self.addon.tls_failed_client(SimpleNamespace(conn=SimpleNamespace(
            sni=page_proxy.CHANNELS_HOST, error='unknown ca url?token=private')))
        self.addon._save(force=True)
        written = (self.root / 'status.json').read_text()
        for secret in ('private', 'Cookie', self.config['token'], self.config['bridge_secret']):
            self.assertNotIn(secret, written)
            self.assertNotIn(secret, response.get_text())
        self.assertEqual(self.addon.state['tls_client_errors'], 1)

    def test_media_and_nonpage_responses_are_streamed_unchanged(self):
        for url, mime in (
                ('https://finder.video.qq.com/x/stodownload?token=secret', 'video/mp4'),
                ('https://channels.weixin.qq.com/api', 'application/json'),
                ('https://res.wx.qq.com/a.png', 'image/png')):
            response = http.Response.make(200, b'untouched', {'Content-Type': mime})
            flow = self.flow(url, response=response)
            self.addon.responseheaders(flow)
            self.addon.response(flow)
            self.assertTrue(response.stream)
            self.assertEqual(response.raw_content, b'untouched')
            self.assertNotIn('clipgo_rewrite', flow.metadata)

    def test_gzipped_html_rewrite_corrects_headers_but_preserves_csp(self):
        body = b'<html><head></head><body>ok</body></html>'
        response = http.Response.make(200, gzip.compress(body), {
            'Content-Type': 'text/html; charset=utf-8', 'Content-Encoding': 'gzip',
            'ETag': 'old', 'Content-Security-Policy': "script-src 'self'"})
        response.raw_content = gzip.compress(body)
        response.headers['content-length'] = str(len(response.raw_content))
        flow = self.flow(page_proxy.ORIGIN + '/web/pages/feed?private-token=hidden', response=response)
        self.addon.responseheaders(flow)
        self.assertFalse(response.stream)
        with patch.dict(sys.modules, {'wechat_channels.page_injection': SimpleNamespace()}):
            self.addon.response(flow)
        self.assertEqual(response.raw_content, body + b'<!-- injected -->')
        self.assertEqual(int(response.headers['content-length']), len(response.raw_content))
        self.assertNotIn('content-encoding', response.headers)
        self.assertNotIn('etag', response.headers)
        self.assertEqual(response.headers['content-security-policy'], "script-src 'self'")
        self.assertEqual(self.addon.state['html_rewrites'], 1)
        self.assertEqual(self.addon.state['page_path'], '/web/pages/feed')

    def test_bad_compression_and_large_expansion_are_passed_through(self):
        for raw in (b'invalid gzip', gzip.compress(b'a' * 100)):
            response = http.Response.make(200, raw, {
                'Content-Type': 'text/html', 'Content-Encoding': 'gzip'})
            response.raw_content = raw
            response.headers['content-length'] = str(len(raw))
            flow = self.flow(page_proxy.ORIGIN + '/web/pages/home', response=response)
            self.addon.responseheaders(flow)
            with patch.object(page_proxy, 'MAX_TEXT_BYTES', 64):
                self.addon.response(flow)
            self.assertEqual(response.raw_content, raw)
        self.assertEqual(self.addon.state['html_rewrites'], 0)

    def test_declared_large_response_is_not_buffered(self):
        response = http.Response.make(200, b'x', {'Content-Type': 'application/javascript'})
        response.headers['content-length'] = str(page_proxy.MAX_TEXT_BYTES + 1)
        flow = self.flow('https://res.wx.qq.com/app.js', response=response)
        self.addon.responseheaders(flow)
        self.assertTrue(response.stream)
        self.assertNotIn('clipgo_rewrite', flow.metadata)

    def test_hook_counters_distinguish_cache_busting_from_actual_hook_insertion(self):
        self.addon.script_rewriter = lambda body, url: body + ';globalThis.__clipgoPage.track(x);globalThis.__clipgoPage.wrap(fn);'
        response = http.Response.make(200, b'var x=1;', {'Content-Type': 'application/javascript'})
        flow = self.flow('https://res.wx.qq.com/app.js?_clipgo=123', response=response)
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        self.assertEqual(self.addon.state['track_hooks'], 1)
        self.assertEqual(self.addon.state['wrap_hooks'], 1)
        self.assertEqual(self.addon.state['detail_hooks'], 0)
        self.assertEqual(self.addon.state['script_hook_assets'], 1)
        self.addon.script_rewriter = lambda body, url: body + ';/* cache tag only */'
        response = http.Response.make(200, b'var y=2;', {'Content-Type': 'application/javascript'})
        flow = self.flow('https://res.wx.qq.com/other.js', response=response)
        self.addon.responseheaders(flow)
        self.addon.response(flow)
        self.assertEqual(self.addon.state['script_rewrites'], 2)
        self.assertEqual(self.addon.state['cache_only_rewrites'], 1)
        self.assertEqual(self.addon.state['script_hook_assets'], 1)

    def test_only_fixed_public_build_asset_paths_can_enter_diagnostics(self):
        path = '/t/wx_fed/finder/web/js/virtual_svg-icons-register.publish.a1b2c3.js'
        response = http.Response.make(200, b'', {'Content-Type': 'application/javascript'})
        flow = self.flow('https://res.wx.qq.com' + path + '?token=must-not-be-saved', response=response)
        self.addon.responseheaders(flow)
        self.assertEqual(self.addon.state['public_flow_script_path'], path)
        self.assertNotIn('must-not-be-saved', json.dumps(self.addon.state))
        for bad in ('/private/virtual_svg-icons-register.publish.a1b2c3.js',
                    '/t/wx_fed/finder/secret-user.js',
                    '/t/wx_fed/../private/virtual_svg-icons-register.publish.a1b2c3.js'):
            response = http.Response.make(200, b'', {'Content-Type': 'application/javascript'})
            self.addon.responseheaders(self.flow('https://res.wx.qq.com' + bad, response=response))
            self.assertEqual(self.addon.state['public_flow_script_path'], path)

    def test_conditionals_only_removed_for_page_and_script_assets(self):
        for url, removed in ((page_proxy.ORIGIN + '/web/pages/home', True),
                             ('https://res.wx.qq.com/app.js', True),
                             ('https://finder.video.qq.com/file.mp4', False)):
            flow = self.flow(url, headers={'If-None-Match': 'old', 'If-Modified-Since': 'yesterday'})
            self.addon.requestheaders(flow)
            self.assertEqual('if-none-match' not in flow.request.headers, removed)

    def test_stop_marker_shuts_worker_without_attempting_orphan_recovery(self):
        (self.root / 'stop').touch()
        self.addon.parent = Mock(gone=Mock(return_value=False))
        self.addon._shutdown = Mock()
        self.addon._recover_orphan = Mock()
        asyncio.run(self.addon._watch_lifecycle())
        self.addon._shutdown.assert_called_once()
        self.addon._recover_orphan.assert_not_called()

    def test_parent_exit_recovers_before_shutdown(self):
        order = []
        self.addon.parent = Mock(gone=Mock(return_value=True))
        async def recover():
            order.append('recover')
        self.addon._recover_orphan = recover
        self.addon._shutdown = lambda: order.append('shutdown')
        asyncio.run(self.addon._watch_lifecycle())
        self.assertEqual(order, ['recover', 'shutdown'])


if __name__ == '__main__':
    unittest.main()
