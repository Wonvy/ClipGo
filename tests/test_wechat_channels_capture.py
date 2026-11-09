import asyncio
import gzip
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "tools" / "wechat_channels" / "capture.py"
spec = importlib.util.spec_from_file_location("wechat_capture", PATH)
capture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = capture
spec.loader.exec_module(capture)


URL = "https://finder.video.qq.com/251/20302/stodownload?encfilekey=example&bizid=1023"


def payload(url=URL):
    return {"data": {"object": {"id": "feed1", "contact": {"nickname": "作者"},
            "objectDesc": {"description": "测试视频", "media": [{"mediaType": 4,
            "url": url, "urlToken": "&token=secret&web=1", "decodeKey": "12345678901234567890"}]}}}}


def flow(host="channels.weixin.qq.com", url=None, content_type="application/json", status=200):
    return SimpleNamespace(request=SimpleNamespace(host=host, url=url or "https://channels.weixin.qq.com/api"),
                           response=SimpleNamespace(headers={"content-type": content_type},
                                                    status_code=status, stream=False), metadata={})


def tls_data(sni="finder.video.qq.com", error="handshake failed", address=("203.0.113.1", 443)):
    return SimpleNamespace(conn=SimpleNamespace(sni=sni, error=error),
                           context=SimpleNamespace(client=SimpleNamespace(sni=sni),
                                                   server=SimpleNamespace(sni=None, address=address)))


class CaptureTests(unittest.TestCase):
    def test_tls_failures_are_counted_without_http_or_private_error_text(self):
        addon = capture.ChannelsCapture()
        secret = "unknown ca https://finder.video.qq.com/?token=private Cookie=session-secret"
        addon.tls_failed_client(tls_data(error=secret))
        addon.tls_failed_server(tls_data(error="certificate verify failed for 203.0.113.1"))
        snapshot = addon.state.snapshot()
        self.assertEqual(snapshot["http_requests"], 0)
        self.assertEqual(snapshot["tls_failed_client"], 1)
        self.assertEqual(snapshot["tls_failed_server"], 1)
        self.assertEqual(snapshot["last_diagnostic"]["reason"], "certificate_validation_failed")
        encoded = json.dumps(snapshot)
        for private in ("private", "session-secret", "203.0.113.1", "finder.video.qq.com"):
            self.assertNotIn(private, encoded)

    def test_foreign_tls_ignored_and_unknown_only_aggregated(self):
        addon = capture.ChannelsCapture()
        addon.tls_failed_client(tls_data("private-chat.example", "sensitive failure"))
        self.assertIsNone(addon.state.last_diagnostic)
        addon.tls_failed_client(tls_data(None, "connection reset by peer"))
        self.assertEqual(addon.state.tls_unknown_failures, 1)
        self.assertEqual(addon.state.tls_failed_client, 0)
        self.assertEqual(addon.state.last_diagnostic["scope"], "unknown")
        self.assertEqual(addon.state.last_diagnostic["reason"], "connection_closed")

    def test_tls_success_and_http_counters_are_scoped(self):
        addon = capture.ChannelsCapture()
        addon.tls_established_client(tls_data())
        addon.tls_established_server(tls_data())
        addon.tls_established_client(tls_data("unrelated.example"))
        addon.requestheaders(flow())
        addon.requestheaders(flow("unrelated.example"))
        self.assertEqual(addon.state.tls_established_client, 1)
        self.assertEqual(addon.state.tls_established_server, 1)
        self.assertEqual(addon.state.http_requests, 1)

    def test_tls_reason_uses_closed_categories(self):
        cases = {"tlsv1 alert unknown ca": "certificate_rejected",
                 "operation timed out URL with token": "timeout",
                 "wrong version number": "protocol_mismatch",
                 "arbitrary secret": "handshake_failed"}
        for raw, expected in cases.items():
            self.assertEqual(capture.tls_failure_reason(raw), expected)

    def test_rejects_foreign_credentials_ports_and_control_characters(self):
        for bad in ("http://finder.video.qq.com/x", "https://finder.video.qq.com.evil.test/x",
                    "https://user:secret@finder.video.qq.com/x", "https://finder.video.qq.com:444/x",
                    "https://127.0.0.1/x", "https://finder.video.qq.com/x\n", URL + "#secret"):
            self.assertFalse(capture.valid_media_url(bad), bad)
        self.assertTrue(capture.valid_media_url(URL))

    def test_extracts_only_explicit_video_metadata(self):
        items = capture.extract_media(payload())
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "测试视频")
        self.assertEqual(item["author"], "作者")
        self.assertEqual(item["decode_key"], "12345678901234567890")
        self.assertIn("token=secret", item["url"])
        self.assertEqual(capture.extract_media(payload("https://evil.test/video.mp4")), [])
        self.assertEqual(capture.extract_media({"url": URL, "cookie": "not captured"}), [])

    def test_uses_signatures_as_queries_never_urls(self):
        for suffix in ("https://evil.test/x", "&token=x#fragment"):
            data = payload()
            data["data"]["object"]["objectDesc"]["media"][0]["urlToken"] = suffix
            self.assertEqual(capture.extract_media(data), [])

    def test_media_headers_cannot_erase_metadata(self):
        state = capture.CaptureState()
        item = capture.extract_media(payload())[0]
        state.put({"url": URL + "&token=old", "source": "media"})
        state.put(item)
        state.put({"url": URL + "&token=new&range=100", "source": "media"})
        self.assertEqual(len(state.items), 1)
        merged = next(iter(state.items.values()))
        self.assertEqual(merged["title"], "测试视频")
        self.assertEqual(merged["decode_key"], "12345678901234567890")
        self.assertEqual(merged['url'], item['url'])
        # A later metadata response without a key cannot borrow the previous one.
        state.put(dict(item, url=URL + '&token=other', decode_key=None))
        self.assertIsNone(next(iter(state.items.values()))['decode_key'])

    def test_streams_media_and_ignores_other_hosts(self):
        addon = capture.ChannelsCapture()
        media = flow("finder.video.qq.com", URL, "video/mp4", 206)
        addon.responseheaders(media)
        self.assertIs(media.response.stream, True)
        self.assertEqual(len(addon.state.items), 1)
        unrelated = flow("chat.example", "https://chat.example/private")
        addon.responseheaders(unrelated)
        self.assertIs(unrelated.response.stream, True)
        self.assertNotIn("clipgo_body", unrelated.metadata)

    def test_json_stream_passthrough_and_gzip_parse(self):
        addon = capture.ChannelsCapture()
        data = flow()
        data.response.headers["content-encoding"] = "gzip"
        addon.responseheaders(data)
        body = gzip.compress(json.dumps(payload()).encode())
        self.assertEqual(data.response.stream(body), body)
        addon.response(data)
        self.assertEqual(len(addon.state.items), 1)
        self.assertNotIn("clipgo_body", data.metadata)

    def test_body_limit_and_decompression_limit(self):
        addon = capture.ChannelsCapture()
        oversized = flow()
        addon.responseheaders(oversized)
        oversized.response.stream(b"a" * (capture.MAX_BODY + 1))
        addon.response(oversized)
        self.assertEqual(addon.state.metadata_responses, 0)
        bomb = flow()
        bomb.response.headers["content-encoding"] = "gzip"
        addon.responseheaders(bomb)
        bomb.response.stream(gzip.compress(b" " * (capture.MAX_BODY + 1)))
        addon.response(bomb)
        self.assertEqual(addon.state.metadata_responses, 0)

    def test_brotli_metadata_and_expansion_limit(self):
        try:
            import brotli
        except ImportError:
            self.skipTest("Brotli is supplied by the capture runtime")
        try:
            brotli.Decompressor().process(b"", output_buffer_limit=1)
        except TypeError:
            self.skipTest("Brotli 1.2+ is required")
        addon = capture.ChannelsCapture()
        data = flow()
        data.response.headers["content-encoding"] = "br"
        addon.responseheaders(data)
        data.response.stream(brotli.compress(json.dumps(payload()).encode()))
        addon.response(data)
        self.assertEqual(len(addon.state.items), 1)
        bomb = flow()
        bomb.response.headers["content-encoding"] = "br"
        addon.responseheaders(bomb)
        bomb.response.stream(brotli.compress(b" " * (capture.MAX_BODY + 100000)))
        addon.response(bomb)
        self.assertEqual(addon.state.metadata_responses, 1)

    def test_state_is_bounded_and_excludes_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            state = capture.CaptureState(path)
            for index in range(capture.MAX_ITEMS + 5):
                state.put({"url": URL + f"&clip={index}", "cookies": "sensitive", "headers": {"x": "secret"}})
            state.save()
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["media"]), capture.MAX_ITEMS)
            self.assertNotIn("sensitive", path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            state.prune(time.time() + capture.MAX_AGE + 1)
            self.assertEqual(state.items, {})


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_marker_shuts_down_and_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            addon = capture.ChannelsCapture(path)
            addon._shutdown = Mock()
            with patch.dict(capture.os.environ, {}, clear=True):
                addon.running()
            path.with_suffix(".stop").write_text("stop")
            await asyncio.wait_for(addon.watchdog, 1)
            addon._shutdown.assert_called_once_with()
            self.assertFalse(path.with_suffix(".stop").exists())
            addon.done()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "stopped")

    async def test_parent_exit_requests_graceful_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            addon = capture.ChannelsCapture(Path(directory) / "session.json")
            addon._shutdown = Mock()
            with patch.dict(capture.os.environ, {}, clear=True):
                addon.running()
            addon.parent = Mock()
            addon.parent.gone.return_value = True
            parent = addon.parent
            await asyncio.wait_for(addon.watchdog, 1)
            addon._shutdown.assert_called_once_with()
            addon.done()
            parent.close.assert_called_once_with()

    async def test_normal_done_cancels_watcher_and_closes_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            addon = capture.ChannelsCapture(Path(directory) / "session.json")
            with patch.dict(capture.os.environ, {}, clear=True):
                addon.running()
            task = addon.watchdog
            addon.done()
            with self.assertRaises(asyncio.CancelledError):
                await task

    def test_parent_monitor_fails_closed_and_closes_exact_handle(self):
        parent = capture.ParentMonitor(None)
        self.assertFalse(parent.gone())
        parent.enabled = True
        self.assertTrue(parent.gone())
        parent.handle = 0x123456789ABC
        parent.kernel = Mock()
        parent.kernel.WaitForSingleObject.return_value = 258
        self.assertFalse(parent.gone())
        parent.kernel.WaitForSingleObject.return_value = 0
        self.assertTrue(parent.gone())
        parent.close()
        parent.kernel.CloseHandle.assert_called_once_with(0x123456789ABC)
        self.assertIsNone(parent.handle)


if __name__ == "__main__":
    unittest.main()
