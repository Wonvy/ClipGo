"""Offline Tencent plugin regressions; requires the Python yt-dlp package.

The official yt-dlp zipapp can also be placed on PYTHONPATH for these tests.
No account credentials, network requests, media segments, or keys are used.
"""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.common import InfoExtractor
    from yt_dlp.utils import ExtractorError
except ImportError:
    YoutubeDL = None

if YoutubeDL:
    path = Path(__file__).resolve().parents[1] / 'host/plugins/yt_dlp_plugins/extractor/clipgo_tencent.py'
    spec = importlib.util.spec_from_file_location('clipgo_tencent_test', path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)


VIDEO_URL = 'https://v.qq.com/x/cover/series123/video123.html'
MANIFEST_URL = 'https://media.example/full.m3u8'


def playlist(duration, *, end=True, key=None):
    lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:10']
    if key:
        lines.append(f'#EXT-X-KEY:METHOD={key},URI="https://media.example/secret.key"')
    for index in range(10):
        lines.extend([f'#EXTINF:{duration / 10},', f'segment{index}.ts'])
    if end:
        lines.append('#EXT-X-ENDLIST')
    return '\n'.join(lines)


@unittest.skipUnless(YoutubeDL, 'Python yt-dlp package (or official zipapp on PYTHONPATH) required')
class TencentPluginTests(unittest.TestCase):
    def setUp(self):
        self.ydl = YoutubeDL({'quiet': True, 'no_warnings': True}, auto_init=False)
        self.ie = plugin._ClipGoVQQVideoIE(self.ydl)
        self.requests = []
        self.expected_duration = 1421.12
        self.api_drm = 0
        self.manifests = {MANIFEST_URL: playlist(self.expected_duration)}
        self.api_manifest_urls = [MANIFEST_URL]
        self.page = '<html><meta property="og:title" content="Generic website title"></html>'

        def webpage(extractor, url, video_id, *args, **kwargs):
            self.requests.append((url, kwargs.get('query', {}).copy()))
            if url == self.ie._API_URL:
                response = {
                    'code': '0.0',
                    'vl': {'vi': [{
                        'ti': '斗破苍穹年番_218', 'td': str(self.expected_duration),
                        'totalduration': str(self.expected_duration * 1000),
                        'vid': 'video123', 'br': 1, 'vw': 1280, 'vh': 536,
                        # A video-level DRM flag can describe the full video
                        # while a non-DRM preview is returned separately.
                        'drm': 1,
                        'ul': {'ui': [
                            {'url': item, 'hls': {'pt': ''}} for item in self.api_manifest_urls
                        ]},
                    }]},
                    'fl': {'fi': [{'name': 'hd', 'br': 1, 'drm': self.api_drm}]},
                }
                return 'QZOutputJson=' + json.dumps(response)
            if url == VIDEO_URL:
                return self.page
            if url in self.manifests:
                return self.manifests[url]
            raise AssertionError(f'Unexpected request (segment/key prohibited): {url}')

        def m3u8(extractor, url, video_id, ext=None, **kwargs):
            document = extractor._download_webpage(url, video_id)
            return extractor._parse_m3u8_formats_and_subtitles(
                document, url, ext=ext, video_id=video_id, **kwargs)

        self.enterContext(mock.patch.object(InfoExtractor, '_download_webpage', new=webpage))
        self.enterContext(mock.patch.object(InfoExtractor, '_extract_m3u8_formats_and_subtitles', new=m3u8))

    def cookie(self, name, value, domain='v.qq.com'):
        self.ie._set_cookie(domain, name, value)

    def extract(self):
        return self.ie._real_extract(VIDEO_URL)

    def test_modern_login_is_sent_only_to_official_api_with_unchanged_drm(self):
        fields = {
            'v_main_login': 'qq', 'v_t_access_token': 'test-access',
            'v_t_appid': 'test-app', 'v_vusession': 'test-session',
            'v_t_openid': 'test-open', 'v_vuserid': 'test-user', 'video_guid': 'test-guid',
        }
        for name, value in fields.items():
            self.cookie(name, value)
        self.extract()
        api_query = next(query for url, query in self.requests if url == self.ie._API_URL)
        self.assertEqual(json.loads(api_query['logintoken']), {
            'main_login': 'qq', 'access_token': 'test-access', 'appid': 'test-app',
            'vusession': 'test-session', 'openid': 'test-open',
            'vuserid': 'test-user', 'video_guid': 'test-guid',
        })
        self.assertEqual(api_query['drm'], '40')
        self.assertEqual(api_query['platform'], '10901')
        self.assertTrue(all('logintoken' not in query for url, query in self.requests
                            if url != self.ie._API_URL))
        self.assertNotIn('v_vusession', self.ie._get_cookies(self.ie._API_URL))

    def test_legacy_qq_and_modern_partial_cookie_fallback(self):
        for name, value in {
            'main_login': 'qq', 'vqq_vusession': 'old-session',
            'vusession': 'wrong-nonqq-session', 'vqq_vuserid': 'old-user',
            'v_t_openid': 'new-open',
        }.items():
            self.cookie(name, value)
        token = plugin._login_token(self.ie._get_cookies('https://v.qq.com/'))
        self.assertEqual(token['vusession'], 'old-session')
        self.assertEqual(token['vuserid'], 'old-user')
        self.assertEqual(token['openid'], 'new-open')

    def test_legacy_wechat_uses_unprefixed_cookie_names(self):
        self.cookie('main_login', 'wx')
        self.cookie('vusession', 'wechat-session')
        self.cookie('vqq_vusession', 'unrelated-qq-session')
        token = plugin._login_token(self.ie._get_cookies('https://v.qq.com/'))
        self.assertEqual(token['vusession'], 'wechat-session')

    def test_cookie_values_are_decoded_like_the_official_player(self):
        self.cookie('v_main_login', 'qq')
        self.cookie('v_t_access_token', 'test%2Btoken%2Fwith%3Dpadding')
        self.cookie('v_vusession', 'invalid%zz')
        token = plugin._login_token(self.ie._get_cookies('https://v.qq.com/'))
        self.assertEqual(token['access_token'], 'test+token/with=padding')
        self.assertEqual(token['vusession'], '')

    def test_anonymous_and_wrong_domain_cookies_do_not_create_login_token(self):
        self.cookie('v_main_login', 'qq', 'unrelated.example')
        self.cookie('v_vusession', 'foreign-session', 'unrelated.example')
        self.extract()
        self.assertTrue(all('logintoken' not in query for _, query in self.requests))

    def test_authenticated_request_errors_do_not_disclose_login_token(self):
        self.cookie('v_main_login', 'qq')
        self.cookie('v_vusession', 'private-test-session')
        with mock.patch.object(InfoExtractor, '_download_webpage', side_effect=ExtractorError(
                'Transport failed: https://h5vv6.video.qq.com/getvinfo?logintoken=private-test-session')):
            with self.assertRaisesRegex(ExtractorError, 'Tencent video API request failed') as caught:
                self.ie._get_video_api_response(VIDEO_URL, 'video123', 'series123', 'srt', 'hls', 'hd')
        self.assertNotIn('private-test-session', str(caught.exception))
        self.assertNotIn('logintoken=', str(caught.exception))
        self.assertEqual(caught.exception.exc_info, (None, None, None))

    def test_full_clear_video_gets_api_title_and_duration(self):
        result = self.extract()
        self.assertEqual(result['title'], '斗破苍穹年番_218')
        self.assertEqual(result['duration'], 1421.12)
        self.assertEqual(len(result['formats']), 1)
        self.assertFalse(result['formats'][0]['has_drm'])

    def test_api_full_duration_does_not_turn_a_preview_into_a_full_video(self):
        self.manifests[MANIFEST_URL] = playlist(209.08)
        with self.assertRaisesRegex(ExtractorError, 'only a preview.*209s of 1421s') as caught:
            self.extract()
        self.assertIn('login=absent', str(caught.exception))
        self.assertTrue(all(not url.endswith(('.ts', '.key')) for url, _ in self.requests))

    def test_preview_diagnostic_reports_only_whether_login_was_sent(self):
        self.cookie('v_main_login', 'qq')
        self.cookie('v_vusession', 'private-test-session')
        self.manifests[MANIFEST_URL] = playlist(209.08)
        with self.assertRaisesRegex(ExtractorError, 'login=present') as caught:
            self.extract()
        message = str(caught.exception)
        for secret in ('private-test-session', 'v_vusession', 'logintoken', 'https://'):
            self.assertNotIn(secret, message)

    def test_protected_diagnostic_uses_actual_login_transmission(self):
        self.api_drm = 1
        with self.assertRaisesRegex(ExtractorError, 'login=absent'):
            self.extract()
        self.cookie('v_main_login', 'qq')
        self.cookie('v_vusession', 'private-test-session')
        with self.assertRaisesRegex(ExtractorError, 'login=present') as caught:
            self.extract()
        self.assertNotIn('private-test-session', str(caught.exception))

    def test_short_format_removed_when_another_full_format_exists(self):
        short_url = 'https://media.example/preview.m3u8'
        self.manifests[short_url] = playlist(209.08)
        self.api_manifest_urls.insert(0, short_url)
        result = self.extract()
        self.assertEqual([fmt['url'] for fmt in result['formats']], [MANIFEST_URL])

    def test_master_playlist_checks_each_media_variant_and_reuses_cache(self):
        master_url = 'https://media.example/master.m3u8'
        short_url = 'https://media.example/preview.m3u8'
        self.manifests[master_url] = (
            '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nfull.m3u8\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=2000000\npreview.m3u8\n')
        self.manifests[short_url] = playlist(209.08)
        self.api_manifest_urls = [master_url]
        result = self.extract()
        self.assertEqual([fmt['url'] for fmt in result['formats']], [MANIFEST_URL])
        self.assertEqual(sum(url == MANIFEST_URL for url, _ in self.requests), 1)

    def test_manifest_encryption_is_not_erased_by_api_drm_zero(self):
        self.manifests[MANIFEST_URL] = playlist(1421.12, key='SAMPLE-AES')
        with self.assertRaisesRegex(ExtractorError, 'protected/encrypted'):
            self.extract()

    def test_standard_identity_aes128_full_video_uses_normal_engine_formats(self):
        for attributes in (
            'METHOD=NONE',
            'METHOD=AES-128,URI="https://media.example/normal.key"',
            'METHOD=AES-128,URI="http://media.example/normal.key",KEYFORMAT="identity"',
            'METHOD=AES-128,URI="normal.key"',
            'METHOD=AES-128,URI="/keys/normal.key"',
            'METHOD=AES-128,URI="//media.example/normal.key",KEYFORMAT="identity"',
        ):
            with self.subTest(attributes=attributes):
                self.manifests[MANIFEST_URL] = playlist(1421.12).replace(
                    '#EXTM3U', '#EXTM3U\n#EXT-X-KEY:' + attributes, 1)
                result = self.extract()
                self.assertEqual(len(result['formats']), 1)
                self.assertFalse(result['formats'][0]['has_drm'])
                self.assertEqual(result['formats'][0]['protocol'], 'm3u8_native')
        self.assertTrue(all(not url.endswith(('.ts', '.key')) for url, _ in self.requests))

    def test_standard_aes128_preview_is_still_rejected_as_incomplete(self):
        self.manifests[MANIFEST_URL] = playlist(209.08, key='AES-128')
        with self.assertRaisesRegex(ExtractorError, 'CLIPGO_TENCENT_PREVIEW.*209s of 1421s'):
            self.extract()

    def test_drm_and_unsupported_key_declarations_remain_blocked(self):
        for attributes in (
            'METHOD=AES-128,URI="normal.key",KEYFORMAT="com.microsoft.playready"',
            'METHOD=AES-128,URI="normal.key",KEYFORMAT="com.apple.streamingkeydelivery"',
            'METHOD=AES-128,URI="normal.key",KEYFORMAT="com.widevine"',
            'METHOD=AES-128,URI="normal.key",KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"',
            'METHOD=AES-128,URI="normal.key",KEYFORMAT="unknown-format"',
            'METHOD=SAMPLE-AES,URI="normal.key"',
            'METHOD=SAMPLE-AES-CTR,URI="normal.key"',
            'METHOD=AES-256,URI="normal.key"',
            'METHOD=AES-128,URI="skd://license"',
            'METHOD=AES-128,URI="data:application/octet-stream;base64,AAAA"',
            'METHOD=AES-128,URI="file:///local.key"',
            'METHOD=AES-128,URI="https:///missing-host"',
            'METHOD=AES-128,URI="https://user:password@media.example/normal.key"',
            'METHOD=AES-128',
        ):
            with self.subTest(attributes=attributes):
                self.manifests[MANIFEST_URL] = playlist(1421.12).replace(
                    '#EXTM3U', '#EXTM3U\n#EXT-X-KEY:' + attributes, 1)
                with self.assertRaisesRegex(ExtractorError, 'protected/encrypted'):
                    self.extract()
        self.assertTrue(all(not url.endswith(('.ts', '.key')) for url, _ in self.requests))

    def test_native_manifest_drm_marker_without_a_key_tag_is_preserved(self):
        self.manifests[MANIFEST_URL] = playlist(1421.12).replace('#EXTM3U', '#EXTM3U\n#EXT-X-FAXS-CM:test', 1)
        with self.assertRaisesRegex(ExtractorError, 'protected/encrypted'):
            self.extract()

    def test_api_drm_flag_still_blocks_an_apparently_standard_aes128_manifest(self):
        self.api_drm = 1
        self.manifests[MANIFEST_URL] = playlist(1421.12, key='AES-128')
        with self.assertRaisesRegex(ExtractorError, 'protected/encrypted'):
            self.extract()

    def test_standard_aes128_full_variant_survives_when_another_variant_is_drm(self):
        protected_url = 'https://media.example/protected.m3u8'
        self.manifests[MANIFEST_URL] = playlist(1421.12, key='AES-128')
        self.manifests[protected_url] = playlist(1421.12, key='SAMPLE-AES')
        self.api_manifest_urls.append(protected_url)
        result = self.extract()
        self.assertEqual([fmt['url'] for fmt in result['formats']], [MANIFEST_URL])

    def test_master_session_key_blocks_children_without_requesting_keys(self):
        master_url = 'https://media.example/master.m3u8'
        self.manifests[master_url] = (
            '#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI="secret.key"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1000000\nfull.m3u8\n')
        self.api_manifest_urls = [master_url]
        with self.assertRaisesRegex(ExtractorError, 'protected/encrypted'):
            self.extract()
        self.assertFalse(any(url.endswith('secret.key') for url, _ in self.requests))

    def test_api_drm_formats_are_not_downgraded(self):
        self.api_drm = 1
        with self.assertRaisesRegex(ExtractorError, 'protected/encrypted'):
            self.extract()

    def test_incomplete_manifest_is_not_reported_as_a_full_video(self):
        self.manifests[MANIFEST_URL] = playlist(1421.12, end=False)
        with self.assertRaisesRegex(ExtractorError, 'Unable to verify'):
            self.extract()

    def test_small_rounding_difference_is_accepted(self):
        self.manifests[MANIFEST_URL] = playlist(1420)
        self.assertEqual(len(self.extract()['formats']), 1)

    def test_state_is_reset_for_each_video(self):
        self.manifests[MANIFEST_URL] = playlist(209.08)
        with self.assertRaises(ExtractorError):
            self.extract()
        self.manifests[MANIFEST_URL] = playlist(1421.12)
        self.assertEqual(len(self.extract()['formats']), 1)


if __name__ == '__main__':
    unittest.main()
