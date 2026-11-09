"""Synthetic transport checks; no personal WeChat content or network required."""
from http.client import IncompleteRead
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request

from tools.wechat_channels import download
from tools.wechat_channels.decoder import decrypt_prefix


def box(kind, payload=b''):
    return (8 + len(payload)).to_bytes(4, 'big') + kind + payload


def mp4():
    # Container fixture, intentionally not represented as a playable video.
    return box(b'ftyp', b'isom\0\0\0\0isom') + box(b'mdat', b'\0' * 150000) + box(b'moov')


class Response(io.BytesIO):
    def __init__(self, body, status=200, length=None):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Length': str(len(body) if length is None else length)}


class PairedFallbackTests(unittest.TestCase):
    def test_retry_keeps_each_response_pair_together(self):
        item = {'url': 'https://finder.video.qq.com/a', 'decode_key': '11',
                'media_options': [{'url': 'https://finder.video.qq.com/b', 'decode_key': '22'}]}
        result = Path('completed.mp4')
        with patch.object(download, '_download_one', side_effect=[download.DownloadError('expired'), result]) as one:
            self.assertEqual(download.download_video(item, Path('.'), threading.Event()), result)
        pairs = [(c.args[0]['url'], c.args[0]['decode_key']) for c in one.call_args_list]
        self.assertEqual(pairs, [(item['url'], '11'), ('https://finder.video.qq.com/b', '22')])

    def test_cancel_does_not_try_another_url(self):
        item = {'url': 'https://finder.video.qq.com/a', 'decode_key': '11',
                'media_options': [{'url': 'https://finder.video.qq.com/b', 'decode_key': '22'}]}
        with patch.object(download, '_download_one', side_effect=download.Cancelled('cancelled')) as one:
            with self.assertRaises(download.Cancelled):
                download.download_video(item, Path('.'), threading.Event())
            one.assert_called_once()

    def test_incomplete_http_response_retries_next_exact_pair(self):
        expected = mp4()
        item = {'url': 'https://finder.video.qq.com/first', 'decode_key': '11',
                'media_options': [{'url': 'https://finder.video.qq.com/second', 'decode_key': '22'}]}
        for failure_read in (1, 2):
            with self.subTest(failure_read=failure_read), tempfile.TemporaryDirectory() as folder:
                broken = Response(decrypt_prefix(expected, 11))
                original_read = broken.read
                reads = []
                def interrupted(amount):
                    reads.append(amount)
                    if len(reads) == failure_read:
                        raise IncompleteRead(b'partial response', 200000)
                    return original_read(amount)
                broken.read = interrupted
                complete = Response(decrypt_prefix(expected, 22))
                opener = Mock()
                opener.open.side_effect = [broken, complete]
                with patch.object(download.urllib.request, 'build_opener', return_value=opener):
                    result = download.download_video(item, Path(folder), threading.Event())
                self.assertEqual(result.read_bytes(), expected)
                self.assertEqual([call.args[0].full_url for call in opener.open.call_args_list],
                                 [item['url'], item['media_options'][0]['url']])
                self.assertEqual(list(Path(folder).iterdir()), [result])


class DownloadTests(unittest.TestCase):
    url = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=synthetic'

    def run_download(self, body, folder, *, cancel=None, progress=lambda *_: None,
                     key=None, status=200, length=None):
        response = Response(body, status, length)
        opener = Mock()
        opener.open.return_value = response
        candidate = {'url': self.url, 'title': '../test\\bad:title', 'decode_key': key,
                     'headers': {'Cookie': 'private_cookie_not_to_forward'}}
        with patch.object(download.urllib.request, 'build_opener', return_value=opener):
            path = download.download_video(candidate, Path(folder), cancel or threading.Event(), progress)
        return path, opener

    def test_full_file_and_no_session_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            expected = mp4()
            path, opener = self.run_download(expected, folder)
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(path.parent, Path(folder).resolve())
            request = opener.open.call_args.args[0]
            self.assertIsNone(request.get_header('Cookie'))
            self.assertIsNone(request.get_header('Authorization'))
            self.assertFalse(list(Path(folder).glob('*.part')))

    def test_exact_zero_key_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            expected = mp4()
            path, _ = self.run_download(decrypt_prefix(expected, 0), folder, key=0)
            self.assertEqual(path.read_bytes(), expected)

    def test_missing_key_never_publishes_encrypted_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(download.DownloadError):
                self.run_download(decrypt_prefix(mp4(), 10), folder)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_wrong_key_rejects_prefix_before_downloading_remainder(self):
        response = Response(decrypt_prefix(mp4(), 10))
        response.read = Mock(wraps=response.read)
        opener = Mock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(download.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(download.DownloadError):
                download.download_video({'url': self.url, 'decode_key': 11}, Path(folder), threading.Event())
            response.read.assert_called_once_with(131072)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_cancel_after_first_chunk_removes_partial(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(download.Cancelled):
                self.run_download(mp4(), folder, cancel=cancel,
                                  progress=lambda *_: cancel.set())
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_incomplete_or_range_response_does_not_publish(self):
        for body, status, length in ((mp4()[:-3], 200, None),
                                     (mp4(), 206, None),
                                     (mp4(), 200, len(mp4()) + 1),
                                     (b'<html>login</html>', 200, None)):
            with self.subTest(status=status, length=length), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(download.DownloadError):
                    self.run_download(body, folder, status=status, length=length)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_urls_and_redirects_cannot_escape_media_host(self):
        for bad in ('http://finder.video.qq.com/file', 'https://127.0.0.1/file',
                    'https://finder.video.qq.com.evil.test/file',
                    'https://user:password@finder.video.qq.com/file',
                    'https://finder.video.qq.com:8443/file',
                    'https://finder.video.qq.com/file#fragment',
                    'https://finder.video.qq.com/file\n'):
            with self.subTest(url=bad):
                with self.assertRaises(download.DownloadError):
                    download.validate_url(bad)
                with self.assertRaises(download.DownloadError):
                    download.MediaRedirect().redirect_request(Request(self.url), None, 302, '', {}, bad)

    def test_filename_blocks_windows_reserved_names(self):
        for bad in ('CON', 'prn.mp4', 'AUX', 'LPT1', 'com9.anything'):
            self.assertTrue(download.safe_name(bad).startswith('_'))


if __name__ == '__main__':
    unittest.main()
