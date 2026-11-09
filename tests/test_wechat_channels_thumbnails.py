"""Thumbnail transport and decoding boundaries with generated image fixtures."""
import io
import struct
import unittest
from unittest.mock import patch, Mock
import zlib

from PIL import Image

from tools.wechat_channels import thumbnails as thumbs


URL = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=cover&token=signed&picformat=200'


class Response(io.BytesIO):
    def __init__(self, data, headers=None, status=200):
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Length': str(len(data))} if headers is None else headers


def png(size=(80, 120), color=(15, 100, 50)):
    out = io.BytesIO()
    with Image.new('RGB', size, color) as image:
        image.save(out, format='PNG')
    return out.getvalue()


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        thumbs.clear_thumbnail_cache()

    def load(self, data, *, headers=None, status=200, size=(224, 288)):
        opener = Mock()
        opener.open.return_value = Response(data, headers, status)
        with patch.object(thumbs.urllib.request, 'build_opener', return_value=opener):
            image = thumbs.load_thumbnail(URL, size)
        return image, opener

    def test_accepts_only_exact_observed_https_cover_host(self):
        self.assertTrue(thumbs.valid_thumbnail_url(URL))
        bad = (URL.replace('https:', 'http:'), URL.replace('finder.video.qq.com', 'finder.video.qq.com.evil.test'),
               URL.replace('finder.video.qq.com', '127.0.0.1'), URL.replace('finder.video.qq.com', 'qpic.cn'),
               URL.replace('https://', 'https://user:password@'), URL.replace('.com/', '.com:8443/'),
               URL + '#fragment', URL + '\r\nInjected:1', URL + ' ', None)
        with patch.object(thumbs.urllib.request, 'build_opener') as build:
            for value in bad:
                with self.subTest(value=value), self.assertRaises(thumbs.ThumbnailError):
                    thumbs.load_thumbnail(value)
            build.assert_not_called()

    def test_transport_has_no_proxy_or_cookie_and_rejects_redirects(self):
        opener = Mock()
        opener.open.return_value = Response(png())
        with patch.object(thumbs.urllib.request, 'build_opener', return_value=opener) as build:
            thumbs.load_thumbnail(URL)
        self.assertEqual(build.call_args.args[0].proxies, {})
        request = opener.open.call_args.args[0]
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 8)
        self.assertEqual(request.full_url, URL)
        self.assertNotIn('Cookie', dict(request.header_items()))
        redirect = build.call_args.args[1]
        for target in ('https://example.com/cover.jpg', URL):
            with self.assertRaises(thumbs.ThumbnailError):
                redirect.redirect_request(request, None, 302, '', {}, target)

    def test_image_fits_without_distortion_or_upscaling(self):
        image, _ = self.load(png((400, 600)))
        self.assertEqual(image.size, (192, 288))
        self.assertEqual(image.mode, 'RGB')
        thumbs.clear_thumbnail_cache()
        small, _ = self.load(png((40, 60)))
        self.assertEqual(small.size, (40, 60))

    def test_transfer_limit_covers_declared_and_streamed_sizes(self):
        with patch.object(thumbs, 'MAX_BYTES', 32):
            for headers in ({'Content-Length': '33'}, {}):
                with self.subTest(headers=headers), self.assertRaisesRegex(thumbs.ThumbnailError, '文件过大'):
                    self.load(b'x' * 33, headers=headers)

    def test_partial_malformed_and_encoded_responses_rejected(self):
        for headers, status in (({'Content-Length': '999'}, 200), ({}, 206),
                                ({'Content-Length': 'not-a-number'}, 200),
                                ({'Content-Encoding': 'gzip'}, 200)):
            with self.subTest(headers=headers, status=status), self.assertRaises(thumbs.ThumbnailError):
                self.load(png(), headers=headers, status=status)
        with self.assertRaisesRegex(thumbs.ThumbnailError, '无效'):
            self.load(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')

    def test_pixel_limit_rejects_large_declared_image_before_pixel_decode(self):
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        data = b'\x89PNG\r\n\x1a\n'
        data += chunk(b'IHDR', struct.pack('>IIBBBBB', 4000, 3000, 8, 2, 0, 0, 0))
        data += chunk(b'IDAT', zlib.compress(b'\0')) + chunk(b'IEND', b'')
        with self.assertRaisesRegex(thumbs.ThumbnailError, '分辨率过大'):
            self.load(data)

    def test_cache_returns_independent_image_and_is_bounded(self):
        data = png((40, 60))
        image, opener = self.load(data)
        image.putpixel((0, 0), (255, 0, 0))
        with patch.object(thumbs.urllib.request, 'build_opener') as build:
            second = thumbs.load_thumbnail(URL)
        build.assert_not_called()
        self.assertEqual(second.getpixel((0, 0)), (15, 100, 50))
        self.assertEqual(opener.open.call_count, 1)
        with patch.object(thumbs, 'MAX_CACHE_ITEMS', 2):
            for suffix in ('a', 'b', 'c'):
                source = Mock()
                source.open.return_value = Response(data)
                with patch.object(thumbs.urllib.request, 'build_opener', return_value=source):
                    thumbs.load_thumbnail(URL + '&variant=' + suffix)
            self.assertEqual(len(thumbs._cache), 2)

    def test_total_read_deadline_and_size_parameter_bounds(self):
        with patch.object(thumbs.time, 'monotonic', side_effect=[10, 19]):
            with self.assertRaisesRegex(thumbs.ThumbnailError, '超时'):
                self.load(png())
        for size in ((225, 288), (224, 289), (0, 10), (True, 20), (20.5, 50)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                thumbs.load_thumbnail(URL, size)

    def test_animated_image_uses_only_first_frame(self):
        out = io.BytesIO()
        with Image.new('RGB', (20, 20), 'red') as first, Image.new('RGB', (20, 20), 'blue') as second:
            first.save(out, format='GIF', save_all=True, append_images=[second])
        image, _ = self.load(out.getvalue())
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))


if __name__ == '__main__':
    unittest.main()
