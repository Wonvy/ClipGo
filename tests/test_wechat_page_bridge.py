"""Bounded page-to-host metadata and actual authenticated loopback requests."""
from copy import deepcopy
from email.message import Message
import http.client
import io
import json
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from tools.wechat_channels.page_bridge import (
    MAX_BODY, MAX_REJECT_DRAIN, PageBridge, PageDataError,
    _discard_rejected_body, candidate_from_feed,
)
from tools.wechat_channels.memory import loaded_identity


def feed():
    return {'id': '18446744073709551615', 'objectDesc': {
        'description': '页面中的当前视频', 'mediaType': 4,
        'media': [{'url': 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=exact%2bValue',
                   'urlToken': '&token=signed%2fVALUE%3D&web=1',
                   'decodeKey': '18446744073709551615', 'videoPlayLen': 23.5,
                   'thumbUrl': 'https://finder.video.qq.com/cover?token=cover%2Btoken&picformat=200'}]},
        'contact': {'nickname': '作者'}, 'favCount': 4302, 'forwardCount': 2380,
        'likeCount': 3489, 'commentCount': 304}


class FeedValidationTests(unittest.TestCase):
    def test_exact_signed_pair_and_same_feed_metadata(self):
        original = feed()
        item = candidate_from_feed(original)
        media = original['objectDesc']['media'][0]
        self.assertEqual(item['url'], media['url'] + media['urlToken'])
        self.assertEqual(item['decode_key'], media['decodeKey'])
        self.assertEqual(item['thumbnail_url'], media['thumbUrl'])
        self.assertEqual((item['title'], item['author'], item['duration']), ('页面中的当前视频', '作者', 23.5))
        self.assertEqual(item['engagement'], {'recommend': 4302, 'share': 2380, 'like': 3489, 'comment': 304})
        self.assertEqual(item['page_feed_id'], '18446744073709551615')
        self.assertRegex(item['id'], r'^[a-f0-9]{24}$')
        self.assertEqual(original, feed())

    def test_page_and_memory_share_canonical_id_across_signature_refresh(self):
        original = feed()
        first = candidate_from_feed(original)
        self.assertEqual(first['id'], loaded_identity(first['url']))
        original['objectDesc']['media'][0]['urlToken'] = '&token=new&basedata=new-signature&sign=changed&idx=0'
        second = candidate_from_feed(original)
        self.assertEqual(second['id'], loaded_identity(second['url']))
        original['objectDesc']['media'][0]['urlToken'] = '&token=new&basedata=new-signature&sign=changed'
        refreshed = candidate_from_feed(original)
        self.assertEqual(first['id'], refreshed['id'])
        self.assertNotEqual(first['url'], refreshed['url'])

    def test_only_first_media_is_selected_and_no_neighbor_keys_are_borrowed(self):
        original = feed()
        original['decodeKey'] = '222'
        original['objectDesc']['media'][0].pop('decodeKey')
        original['objectDesc']['media'].append(deepcopy(feed()['objectDesc']['media'][0]))
        item = candidate_from_feed(original)
        self.assertIsNone(item['decode_key'])
        self.assertNotIn('media_options', item)
        original['objectDesc']['media'][0]['url'] = 'https://example.com/wrong.mp4'
        with self.assertRaises(PageDataError):
            candidate_from_feed(original)

    def test_bad_origins_image_urls_and_ambiguous_suffixes_are_rejected(self):
        for url, suffix in (
                ('https://finder.video.qq.com.evil.test/a.mp4', '?token=t'),
                ('http://finder.video.qq.com/a.mp4', '?token=t'),
                ('https://finder.video.qq.com:444/a.mp4', '?token=t'),
                ('https://user@finder.video.qq.com/a.mp4', '?token=t'),
                ('https://finder.video.qq.com/a.mp4', '&token=t'),
                ('https://finder.video.qq.com/a.mp4?a=b', '?token=t'),
                ('https://finder.video.qq.com/a.mp4?a=b', '&token=t#fragment'),
                ('https://finder.video.qq.com/a.mp4?token=t', '&token=other'),
                ('https://finder.video.qq.com/a.mp4?TOKEN=t', '&token=other'),
                ('https://finder.video.qq.com/cover', '?token=t'),
                ('https://finder.video.qq.com/a.mp4', '?token=t&picformat=200'),
                ('https://finder.video.qq.com/a.mp4', '?token=t\r\nHeader:v'),
                ('https://finder.video.qq.com/a.mp4', ''),
        ):
            with self.subTest(url=url, suffix=suffix):
                item = feed()
                item['objectDesc']['media'][0].update(url=url, urlToken=suffix)
                with self.assertRaises(PageDataError):
                    candidate_from_feed(item)

    def test_invalid_and_lossy_keys_rejected_but_zero_and_absent_preserved(self):
        for key in (True, 1.5, {}, '-1', '+1', '1e12', '18446744073709551616', 1 << 53):
            with self.subTest(key=key):
                item = feed()
                item['objectDesc']['media'][0]['decodeKey'] = key
                with self.assertRaises(PageDataError):
                    candidate_from_feed(item)
        for key, expected in ((None, None), ('', None), (0, '0'), ('0', '0'), (123, '123')):
            item = feed()
            item['objectDesc']['media'][0]['decodeKey'] = key
            self.assertEqual(candidate_from_feed(item)['decode_key'], expected)

    def test_optional_untrusted_fields_are_bounded_and_covers_do_not_reuse_video_token(self):
        original = feed()
        original['objectDesc']['description'] = 'a' * 5000
        media = original['objectDesc']['media'][0]
        media.update(videoPlayLen=10 ** 1000, thumbUrl='file:///secret', coverUrl='https://example.com/pic.jpg')
        original['favCount'] = False
        original['commentCount'] = 0
        original['id'] = 'https://private.example/token'
        item = candidate_from_feed(original)
        self.assertEqual(len(item['title']), 4096)
        self.assertEqual(item['thumbnail_url'], '')
        self.assertEqual(item['page_feed_id'], '')
        self.assertIsNone(item['duration'])
        self.assertIsNone(item['engagement']['recommend'])
        self.assertEqual(item['engagement']['comment'], 0)


class PageBridgeTests(unittest.TestCase):
    def setUp(self):
        self.candidate = Mock(return_value={'ok': True})
        self.event = Mock()
        self.status = Mock(return_value={'ok': True, 'download': {'state': 'idle', 'percent': 0}})
        self.bridge = PageBridge(on_candidate=self.candidate, get_status=self.status, on_event=self.event)
        self.url = self.bridge.start()
        self.port = urlsplit(self.url).port
        self.addCleanup(self.bridge.close)

    def request(self, method, path, data=None, *, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        values = {'X-ClipGo-Secret': self.bridge.secret}
        if raw is not None:
            body = raw
            values['Content-Type'] = 'application/json'
        elif data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            values['Content-Type'] = 'application/json'
        else:
            body = None
        values.update(headers or {})
        conn.request(method, path, body=body, headers=values)
        response = conn.getresponse()
        payload = json.loads(response.read())
        result = response.status, payload, dict(response.getheaders())
        conn.close()
        return result

    def test_current_event_download_and_safe_status_reach_callbacks(self):
        status, response, _ = self.request('POST', '/event', {'kind': 'current', 'feed': feed()})
        self.assertEqual(status, 200)
        self.assertTrue(response['ok'])
        self.assertEqual(self.candidate.call_args.kwargs, {'download': False})
        self.assertEqual(self.event.call_args.args[0], {'kind': 'current', 'has_feed': True, 'id': response['id']})
        status, response, _ = self.request('POST', '/download', {'feed': feed()})
        self.assertEqual(status, 200)
        self.assertEqual(self.candidate.call_args.kwargs, {'download': True})
        self.status.return_value = {'ok': True, 'url': 'PRIVATE_URL', 'decode_key': 'PRIVATE_KEY',
            'folder': 'C:\\PRIVATE', 'download': {'state': 'done', 'percent': 100, 'id': response['id'],
                'title': 'PRIVATE_TITLE', 'message': '下载完成', 'url': 'PRIVATE_URL', 'path': 'C:\\PRIVATE'}}
        status, response, headers = self.request('GET', '/status')
        self.assertEqual(status, 200)
        self.assertNotIn('PRIVATE', json.dumps(response))
        self.assertEqual(response['download']['state'], 'done')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_secret_origin_host_and_browser_preflight_are_rejected(self):
        for headers in ({'X-ClipGo-Secret': ''}, {'X-ClipGo-Secret': '0' * 64},
                        {'X-ClipGo-Secret': '\u00e9'}, {'Host': 'evil.test'},
                        {'Origin': 'https://channels.weixin.qq.com'}, {'Origin': 'null'}):
            with self.subTest(headers=headers):
                status, response, _ = self.request('POST', '/download', {'feed': feed()}, headers=headers)
                self.assertEqual(status, 403)
                self.assertFalse(response['ok'])
        self.assertEqual(self.request('OPTIONS', '/download')[0], 403)
        self.candidate.assert_not_called()

    def test_duplicate_auth_and_host_headers_rejected(self):
        for header in ('Host', 'X-ClipGo-Secret'):
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
            conn.putrequest('GET', '/status', skip_host=True)
            conn.putheader('Host', f'127.0.0.1:{self.port}')
            conn.putheader('X-ClipGo-Secret', self.bridge.secret)
            conn.putheader(header, self.bridge.secret if header == 'X-ClipGo-Secret' else f'127.0.0.1:{self.port}')
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            conn.close()
        self.status.assert_not_called()

    def test_narrow_paths_body_limits_duplicate_json_and_no_client_folder(self):
        self.assertEqual(self.request('GET', '/status?token=wrong')[0], 404)
        self.assertEqual(self.request('POST', '/arbitrary', {})[0], 404)
        for name, raw in (('duplicate properties', b'{"kind":"ready","kind":"current"}'),
                          ('array', b'[]'), ('nonfinite number', b'{"kind":NaN}'),
                          ('oversized', b'{"kind":"ready","data":"' + b'x' * MAX_BODY + b'"}')):
            with self.subTest(body=name):
                self.assertEqual(self.request('POST', '/event', raw=raw)[0], 400)
        self.assertEqual(self.request('POST', '/download', {'feed': feed(), 'folder': 'C:\\elsewhere'})[0], 400)
        self.assertEqual(self.request('POST', '/event', {'kind': 'ready'}, headers={'Content-Type': 'text/plain'})[0], 400)
        self.candidate.assert_not_called()
        self.event.assert_not_called()

    def test_oversized_body_arriving_in_parts_receives_http_rejection(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        self.addCleanup(conn.close)
        body = b'x' * (MAX_BODY + 32)
        conn.putrequest('POST', '/event')
        conn.putheader('X-ClipGo-Secret', self.bridge.secret)
        conn.putheader('Content-Type', 'application/json')
        conn.putheader('Content-Length', str(len(body)))
        conn.endheaders(body[:8192])
        # Exercise header rejection while the sender still has body to send.
        time.sleep(.02)
        conn.send(body[8192:])
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        self.assertFalse(json.loads(response.read())['ok'])
        self.candidate.assert_not_called()
        self.event.assert_not_called()

    def test_current_null_and_diagnostics_are_safe_and_callback_errors_do_not_leak(self):
        self.assertEqual(self.request('POST', '/event', {'kind': 'current', 'feed': None})[0], 200)
        self.event.assert_called_with({'kind': 'current', 'has_feed': False})
        self.assertEqual(self.request('POST', '/event', {'kind': 'diagnostic', 'buttons': 1,
                                                       'active': True, 'position': 'toolbar'})[0], 200)
        self.event.assert_called_with({'kind': 'diagnostic', 'has_feed': False, 'buttons': 1,
                                      'active': True, 'position': 'toolbar'})
        self.assertEqual(self.request('POST', '/event', {'kind': 'diagnostic', 'buttons': 1,
                'page': '/web/pages/home', 'placement': 'inline'})[0], 200)
        self.event.assert_called_with({'kind': 'diagnostic', 'has_feed': False, 'buttons': 1,
                                      'page': '/web/pages/home', 'placement': 'inline'})
        self.assertEqual(self.request('POST', '/event', {'kind': 'ready',
                'page': 'https://channels.weixin.qq.com/web/pages/home?private'})[0], 200)
        self.event.assert_called_with({'kind': 'ready', 'has_feed': False})
        self.candidate.side_effect = RuntimeError('PRIVATE_URL?token=PRIVATE_KEY')
        status, response, _ = self.request('POST', '/download', {'feed': feed()})
        self.assertEqual(status, 500)
        self.assertNotIn('PRIVATE', json.dumps(response))
        self.status.return_value = {'ok': True, 'download': {'state': 'error',
            'message': 'https://finder.video.qq.com/private?token=PRIVATE', 'percent': float('nan')}}
        status, response, _ = self.request('GET', '/status')
        self.assertEqual(status, 200)
        self.assertNotIn('PRIVATE', json.dumps(response))
        self.assertEqual(response['download']['percent'], 0)

    def test_start_is_idempotent_close_releases_listener(self):
        self.assertEqual(self.bridge.start(), self.url)
        self.bridge.close()
        self.bridge.close()
        self.assertIsNone(self.bridge.base_url)
        with self.assertRaises(OSError):
            self.request('GET', '/status')

    def test_diagnostic_hook_fields_are_counts_and_flags_only(self):
        fields = {'source_count': 2, 'candidate_count': 4, 'hook_count': 20, 'video_count': 1, 'vue_components': 3,
                  'selected_index_valid': True, 'vue_component': True,
                  'vue_feed': False, 'detail_seen': False}
        status, response, _ = self.request('POST', '/event', dict(kind='diagnostic', **fields))
        self.assertEqual(status, 200)
        self.event.assert_called_with(dict(kind='diagnostic', has_feed=False, **fields))
        invalid = {'source_count': True, 'candidate_count': -1, 'hook_count': 1_000_001,
                   'selected_index_valid': 'private data', 'vue_component': 1,
                   'vue_feed': [], 'detail_seen': 'false'}
        status, response, _ = self.request('POST', '/event', dict(kind='diagnostic', **invalid))
        self.assertEqual(status, 200)
        self.event.assert_called_with({'kind': 'diagnostic', 'has_feed': False})
        self.assertEqual(self.request('POST', '/event', {
            'kind': 'diagnostic', 'script_url': 'https://example.test/private'})[0], 400)


class RejectedBodyDrainTests(unittest.TestCase):
    def test_discard_has_byte_limit_and_restores_socket_timeout(self):
        headers = Message()
        headers['Content-Length'] = str(MAX_REJECT_DRAIN + 8192)
        stream = io.BytesIO(b'x' * (MAX_REJECT_DRAIN + 8192))
        connection = Mock()
        connection.gettimeout.return_value = 3
        _discard_rejected_body(headers, stream, connection)
        self.assertEqual(stream.tell(), MAX_REJECT_DRAIN)
        connection.settimeout.assert_called_with(3)

    def test_discard_deadline_does_not_renew_after_each_chunk(self):
        headers = Message()
        headers['Content-Length'] = str(MAX_BODY)
        stream = io.BytesIO(b'x' * MAX_BODY)
        connection = Mock()
        connection.gettimeout.return_value = 3
        with patch('tools.wechat_channels.page_bridge.time.monotonic', side_effect=[0, .01, .3]):
            _discard_rejected_body(headers, stream, connection)
        self.assertEqual(stream.tell(), 8192)
        connection.settimeout.assert_called_with(3)

    def test_unknown_ambiguous_or_already_consumed_body_is_not_read(self):
        for lengths, consumed in (([], 0), (['10', '10'], 0), (['invalid'], 0), (['10'], 10)):
            with self.subTest(lengths=lengths, consumed=consumed):
                headers = Message()
                for length in lengths:
                    headers['Content-Length'] = length
                stream, connection = Mock(), Mock()
                _discard_rejected_body(headers, stream, connection, consumed)
                stream.read1.assert_not_called()
                connection.settimeout.assert_not_called()


if __name__ == '__main__':
    unittest.main()
