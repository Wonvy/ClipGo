import json
import unittest

from tools.wechat_channels.loaded_urls import extract_loaded_urls


URL = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=sample&token=signed&X-snsvideoflag=1'


class LoadedURLTests(unittest.TestCase):
    def test_ascii_v8_string_ends_at_binary_byte(self):
        self.assertEqual(extract_loaded_urls(b'noise\0' + URL.encode() + b'\xffmore'), [URL])

    def test_utf16_string_with_odd_start_alignment(self):
        self.assertEqual(extract_loaded_urls(b'\xff' + (URL + '\0').encode('utf-16le')), [URL])

    def test_incomplete_strings_are_deferred(self):
        self.assertEqual(extract_loaded_urls(URL.encode()), [])
        self.assertEqual(extract_loaded_urls(URL.encode('utf-16le')), [])

    def test_complete_json_escapes(self):
        raw = json.dumps(URL).replace('/', '\\/').replace('&', '\\u0026')
        self.assertEqual(extract_loaded_urls(raw.encode()), [URL])
        self.assertEqual(extract_loaded_urls(raw.encode('utf-16le')), [URL])
        self.assertEqual(extract_loaded_urls(raw[:-1].encode()), [])

    def test_scope_images_and_credentials(self):
        bad_urls = [URL.replace('finder.video.qq.com', 'example.com'),
                    URL + '&picformat=200', URL + '&wxampicformat=jpg',
                    URL + '#fragment', URL.replace('token=signed', 'unused=1'),
                    URL.replace('https://', 'https://user:pass@'),
                    URL.replace('.com/', '.com:8443/')]
        for url in bad_urls:
            with self.subTest(url=url):
                self.assertEqual(extract_loaded_urls(json.dumps(url).encode()), [])

    def test_metadata_caller_can_request_unsigned_urls(self):
        url = URL.replace('&token=signed', '')
        self.assertEqual(extract_loaded_urls(url.encode() + b'\0', require_token=False), [url])

    def test_duplicate_encodings_collapse(self):
        data = URL.encode() + b'\0' + (URL + '\0').encode('utf-16le') + json.dumps(URL).encode()
        self.assertEqual(extract_loaded_urls(data), [URL])

    def test_unicode_escape_does_not_emit_truncated_signed_prefix(self):
        url = 'https://finder.video.qq.com/251/stodownload?token=signed&encfilekey=sample'
        raw = json.dumps(url).replace('&', '\\u0026')
        self.assertEqual(extract_loaded_urls(raw.encode()), [url])
        self.assertEqual(extract_loaded_urls(raw.encode('utf-16le')), [url])

    def test_url_length_bound(self):
        oversized = URL + '&padding=' + 'a' * 16384
        self.assertEqual(extract_loaded_urls(oversized.encode() + b'\0'), [])


if __name__ == '__main__':
    unittest.main()
