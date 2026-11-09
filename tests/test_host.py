"""Native-host regressions; run with python -m unittest discover -s tests."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "clipgo_host", Path(__file__).resolve().parents[1] / "host" / "clipgo_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class HostDownloadTests(unittest.TestCase):
    def tearDown(self):
        host._jobs.clear()

    def test_douyin_page_source_is_bound_to_video_and_cdn(self):
        url = "https://www.douyin.com/video/7658580633765904561"
        media = {"videoUrl": url, "url": "https://v26-web.douyinvod.com/sample/?signed=example",
                 "formats": [{"url": "https://v26-web.douyinvod.com/1080.mp4", "width": 1080, "height": 1920, "bitrate": 2559724}]}
        info = host.douyin_media_info(url, media, "中文标题 🎬")
        self.assertEqual(info["id"], "7658580633765904561")
        self.assertEqual(info["title"], "中文标题 🎬")
        self.assertEqual(info["formats"][0]["height"], 1920)
        self.assertEqual(info["http_headers"], {"Referer": "https://www.douyin.com/"})
        for bad in ("http://v26-web.douyinvod.com/video", "https://douyinvod.com.evil.test/video",
                    "https://user:pass@v26-web.douyinvod.com/video", "https://v26-web.douyinvod.com:8443/video",
                    "blob:https://www.douyin.com/id", "https://127.0.0.1/video"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                host.douyin_media_info(url, {**media, "url": bad}, "title")
        with self.assertRaises(ValueError):
            host.douyin_media_info("https://www.douyin.com/video/999", media, "title")
        with self.assertRaises(ValueError):
            host.douyin_media_info("https://v.qq.com/x/page/video.html", media, "title")

    def test_douyin_page_download_uses_ephemeral_metadata_and_removes_it(self):
        url = "https://www.douyin.com/video/7658580633765904561"
        info = host.douyin_media_info(url, {"videoUrl": url, "url": "https://v26-web.douyinvod.com/sample/"}, "中文视频")
        paths = []

        def launch(cmd, **kwargs):
            path = Path(cmd[cmd.index("--load-info-json") + 1])
            paths.append(path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), info)
            self.assertNotIn(url, cmd)
            self.assertNotIn(info["formats"][0]["url"], cmd)
            return Mock(stdout=io.StringIO('CLIPGO_TJ:"中文视频"\nCLIPGO_FJ:"video.mp4"\n'), wait=Mock(return_value=0))

        with tempfile.TemporaryDirectory() as directory, patch.object(host, "download_dir", return_value=directory), \
                patch.object(host, "log"), patch.object(host, "send") as send, \
                patch.object(host.subprocess, "Popen", side_effect=launch):
            host.run_download("page", url, "1080", [], info)
        self.assertEqual(send.call_args.args[0]["type"], "done")
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_supported_sites_remain_allowed(self):
        for url in (
            "https://www.bilibili.com/video/BV1bK411W797?p=2",
            "http://bilibili.com/video/av1074402/",
            "https://m.bilibili.com/video/BV1bK411W797",
            "https://b23.tv/AbC123",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "https://youtu.be/jNQXAC9IVRw",
            "https://www.instagram.com/reel/AbC123/",
            "https://x.com/NASA/status/623160978427936768",
            "https://twitter.com/i/web/status/623160978427936768",
            "https://mobile.twitter.com/NASA/status/623160978427936768/video/2",
            "https://m.x.com/NASA/status/623160978427936768",
            "https://www.xiaohongshu.com/explore/674051740000000007027a15?xsec_token=example-token",
            "https://xhslink.com/a/AbC123",
            "https://www.tiktok.com/@example/video/7170520270497680683",
            "https://vm.tiktok.com/ZTR45GpSF/",
            "https://vt.tiktok.com/ZTR45GpSF/",
            "https://www.douyin.com/video/6961737553342991651",
            "https://v.douyin.com/AbCd123/",
            "https://v.qq.com/x/page/q326831cny0.html",
        ):
            with self.subTest(url=url):
                self.assertTrue(host.is_allowed_url(url))

    def test_untrusted_hosts_never_start_a_job(self):
        for url in (
            "https://bilibili.com.evil.example/video/BV1bK411W797",
            "https://evilbilibili.com/video/BV1bK411W797",
            "https://b23.tv.evil.example/AbC123",
            "https://bilibili.com@evil.example/video/BV1bK411W797",
            "https://x.com.evil.example/NASA/status/623160978427936768",
            "https://evilx.com/NASA/status/623160978427936768",
            "https://twitter.com@evil.example/NASA/status/623160978427936768",
            "https://xiaohongshu.com.evil.example/explore/674051740000000007027a15",
            "https://tiktok.com.evil.example/@example/video/7170520270497680683",
            "https://douyin.com@evil.example/video/6961737553342991651",
            "https://notxhslink.com/a/AbC123",
            "https://v.qq.com.evil.example/x/page/q326831cny0.html",
            "https://qq.com/x/page/q326831cny0.html",
            "https://mail.qq.com/",
            "file:///bilibili.com/video/BV1bK411W797",
            "javascript:alert(1)",
            "--exec=malicious",
            "https://[invalid",
        ):
            with self.subTest(url=url), patch.object(host, "send") as send, \
                    patch.object(host.threading, "Thread") as thread:
                host.handle({"action": "download", "id": "rejected", "url": url})
                self.assertEqual(send.call_args.args[0]["type"], "error")
                self.assertEqual(send.call_args.args[0]["id"], "rejected")
                thread.assert_not_called()
                self.assertNotIn("rejected", host._jobs)

    def test_download_request_preserves_selected_part_and_cookies(self):
        url = "https://www.bilibili.com/video/BV1bK411W797?p=2"
        cookies = [{"domain": ".bilibili.com", "name": "SESSDATA", "value": "test-only"}]
        with patch.object(host.os.path, "exists", return_value=True), \
                patch.object(host.threading, "Thread") as thread:
            host.handle({"action": "download", "id": "p2", "url": url,
                         "quality": "720", "cookies": cookies})
            self.assertEqual(thread.call_args.kwargs["args"], ("p2", url, "720", cookies))
            thread.return_value.start.assert_called_once_with()

    def test_x_request_preserves_video_selection_and_cookie_domains(self):
        url = "https://x.com/i/web/status/623160978427936768/video/2"
        cookies = [
            {"domain": ".x.com", "name": "auth_token", "value": "test-only"},
            {"domain": ".x.com", "name": "ct0", "value": "csrf-test-only"},
            {"domain": ".twitter.com", "name": "auth_token", "value": "legacy-test-only"},
        ]
        with patch.object(host.os.path, "exists", return_value=True), \
                patch.object(host.threading, "Thread") as thread:
            host.handle({"action": "download", "id": "x-video", "url": url,
                         "quality": "1080", "cookies": cookies})
            self.assertEqual(thread.call_args.kwargs["args"], ("x-video", url, "1080", cookies))
            thread.return_value.start.assert_called_once_with()
        cookie_file = host.write_cookie_file(cookies)
        try:
            text = Path(cookie_file).read_text(encoding="utf-8")
            self.assertIn(".x.com\tTRUE\t/\tFALSE\t0\tauth_token\ttest-only", text)
            self.assertIn(".x.com\tTRUE\t/\tFALSE\t0\tct0\tcsrf-test-only", text)
            self.assertIn(".twitter.com\tTRUE\t/\tFALSE\t0\tauth_token\tlegacy-test-only", text)
        finally:
            os.remove(cookie_file)

    def test_command_preserves_media_selection_and_mp4_output(self):
        for url in (
            "https://www.bilibili.com/video/BV1bK411W797",
            "https://www.bilibili.com/video/BV1bK411W797?p=2",
            "https://b23.tv/AbC123",
            "https://x.com/NASA/status/623160978427936768",
            "https://x.com/i/web/status/623160978427936768/video/2",
            "https://twitter.com/NASA/status/623160978427936768",
            "https://www.xiaohongshu.com/explore/674051740000000007027a15?xsec_token=CBgeL8Dxd1ZWBhwqRd568gAZ_iwG-9JIf9tnApNmteU2E%3D",
            "https://www.tiktok.com/@example/video/7170520270497680683",
            "https://www.douyin.com/video/6961737553342991651",
            "https://v.qq.com/x/page/q326831cny0.html",
        ):
            with self.subTest(url=url):
                cmd = host.build_command(url, "1080", "cookies.txt", "downloads")
                self.assertEqual(cmd[-2:], ["--", url])
                self.assertIn("--no-playlist", cmd)
                self.assertNotIn("--yes-playlist", cmd)
                self.assertEqual(cmd[cmd.index("--cookies") + 1], "cookies.txt")
                self.assertEqual(cmd[cmd.index("--merge-output-format") + 1], "mp4")
                self.assertEqual(cmd[cmd.index("--remux-video") + 1], "mp4")

    def test_new_platform_requests_preserve_tokens_and_browser_cookies(self):
        samples = (
            ("https://www.xiaohongshu.com/explore/674051740000000007027a15?xsec_token=token%2Bvalue%3D",
             ".xiaohongshu.com", "web_session"),
            ("https://vm.tiktok.com/ZTR45GpSF/", ".tiktok.com", "sid_tt"),
            ("https://www.douyin.com/video/6961737553342991651", ".douyin.com", "s_v_web_id"),
            ("https://v.qq.com/x/page/q326831cny0.html", ".qq.com", "vqq_vusession"),
        )
        for url, domain, name in samples:
            with self.subTest(url=url), patch.object(host.os.path, "exists", return_value=True), \
                    patch.object(host.threading, "Thread") as thread:
                cookies = [{"domain": domain, "name": name, "value": "test-only"}]
                host.handle({"action": "download", "id": "new-site", "url": url,
                             "quality": "720", "cookies": cookies})
                self.assertEqual(thread.call_args.kwargs["args"], ("new-site", url, "720", cookies))
                thread.return_value.start.assert_called_once_with()

    def test_bilibili_job_reports_progress_and_removes_temporary_cookies(self):
        url = "https://www.bilibili.com/video/BV1bK411W797?p=2"
        cookies = [{"domain": ".bilibili.com", "path": "/", "secure": True,
                    "name": "SESSDATA", "value": "test-only", "expirationDate": 2000000000}]
        proc = Mock()
        proc.stdout = io.StringIO(
            "CLIPGO_P:100%|1MiB/s|00:00|示例视频 p02\n"
            "[Merger] Merging formats into MP4\n"
            "CLIPGO_F:example [BV1bK411W797_p2].mp4\n")
        proc.wait.return_value = 0
        cookie_paths = []

        def launch(cmd, **kwargs):
            self.assertEqual(cmd[-2:], ["--", url])
            self.assertEqual(kwargs["stdin"], host.subprocess.DEVNULL)
            cookie_path = cmd[cmd.index("--cookies") + 1]
            cookie_paths.append(cookie_path)
            cookie_text = Path(cookie_path).read_text(encoding="utf-8")
            self.assertIn(".bilibili.com\tTRUE\t/\tTRUE\t2000000000\tSESSDATA\ttest-only", cookie_text)
            return proc

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(host, "download_dir", return_value=directory), \
                patch.object(host, "log"), patch.object(host, "send") as send, \
                patch.object(host.subprocess, "Popen", side_effect=launch):
            host.run_download("p2", url, "1080", cookies)
        messages = [call.args[0] for call in send.call_args_list]
        self.assertTrue(any(msg["type"] == "progress" and msg["percent"] == 100 for msg in messages))
        self.assertEqual(messages[-1], {"type": "done", "id": "p2",
                                      "files": ["example [BV1bK411W797_p2].mp4"]})
        self.assertTrue(cookie_paths)
        self.assertTrue(all(not os.path.exists(path) for path in cookie_paths))
        self.assertNotIn("p2", host._jobs)

    def test_unicode_title_and_file_path_survive_the_download_process_pipe(self):
        # Simulate a Windows packaged downloader whose default output is CP936,
        # independent of PYTHONIOENCODING. Exercise real bytes through Popen.
        fake_downloader = """from pathlib import Path
import sys
encoding = sys.argv[sys.argv.index('--encoding') + 1] if '--encoding' in sys.argv else 'cp936'
title = '中文标题 · 分P二'
output = Path.cwd() / (title + ' [BV1bK411W797_p2].mp4')
output.write_bytes(b'fixture')
messages = 'CLIPGO_P:100%|1MiB/s|00:00|' + title + '\\nCLIPGO_F:' + str(output) + '\\n'
sys.stdout.buffer.write(messages.encode(encoding, errors='replace'))
"""
        real_popen = host.subprocess.Popen
        children = []
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake_downloader.py"
            script.write_text(fake_downloader, encoding="utf-8")

            def launch(cmd, **kwargs):
                child = real_popen([sys.executable, str(script)] + cmd[1:], **kwargs)
                children.append(child)
                return child

            try:
                with patch.object(host, "download_dir", return_value=directory), \
                        patch.object(host, "log"), patch.object(host, "send") as send, \
                        patch.object(host.subprocess, "Popen", side_effect=launch):
                    host.run_download("unicode", "https://www.bilibili.com/video/BV1bK411W797?p=2", "1080", [])
                messages = [call.args[0] for call in send.call_args_list]
                progress = next(msg for msg in messages if msg["type"] == "progress")
                self.assertEqual(progress["title"], "中文标题 · 分P二")
                self.assertEqual(messages[-1]["type"], "done")
                expected_path = Path(directory) / "中文标题 · 分P二 [BV1bK411W797_p2].mp4"
                self.assertEqual(messages[-1]["files"], [str(expected_path)])
                self.assertEqual(Path(messages[-1]["files"][0]).read_bytes(), b"fixture")
            finally:
                for child in children:
                    child.stdout.close()

    def test_json_protocol_preserves_multiline_unicode_even_with_cp936_output(self):
        # Render the command's actual templates, then deliberately encode as CP936.
        # JSON escaping must preserve non-CP936 characters, pipes and newlines.
        fake_downloader = r'''from pathlib import Path
import json
import sys
title = '中文标题 🎬 | "引号"\n第二行 \\ 尾部'
output = Path.cwd() / '中文视频 🎬 [123].mp4'
output.write_bytes(b'fixture')
values = {
    'info.title': title, 'title': title, 'filepath': str(output),
    'progress._percent_str': '100%', 'progress._speed_str': '1MiB/s', 'progress._eta_str': '00:00',
}
def render(template):
    text = template.partition(':')[2]
    for key, value in values.items():
        text = text.replace('%(' + key + ')j', json.dumps(value, ensure_ascii=True))
        text = text.replace('%(' + key + ')s', value)
    return text + '\n'
prints = [sys.argv[i + 1] for i, value in enumerate(sys.argv[:-1]) if value == '--print']
messages = ''.join(render(value) for value in prints if value.startswith('before_dl:'))
messages += render(sys.argv[sys.argv.index('--progress-template') + 1])
messages += ''.join(render(value) for value in prints if value.startswith('after_move:'))
sys.stdout.buffer.write(messages.encode('cp936', errors='replace'))
'''
        real_popen = host.subprocess.Popen
        children = []
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake_downloader.py"
            script.write_text(fake_downloader, encoding="utf-8")

            def launch(cmd, **kwargs):
                child = real_popen([sys.executable, str(script)] + cmd[1:], **kwargs)
                children.append(child)
                return child

            try:
                with patch.object(host, "download_dir", return_value=directory), \
                        patch.object(host, "log"), patch.object(host, "send") as send, \
                        patch.object(host.subprocess, "Popen", side_effect=launch):
                    host.run_download("json", "https://www.douyin.com/video/123", "1080", [])
                messages = [call.args[0] for call in send.call_args_list]
                expected_title = '中文标题 🎬 | "引号"\n第二行 \\ 尾部'
                title_stage = next(msg for msg in messages if msg["type"] == "stage" and "title" in msg)
                self.assertEqual(title_stage["title"], expected_title)
                progress = next(msg for msg in messages if msg["type"] == "progress")
                self.assertEqual(progress["title"], expected_title)
                self.assertEqual(messages[-1]["type"], "done")
                self.assertEqual(messages[-1]["title"], expected_title)
                expected_path = Path(directory) / "中文视频 🎬 [123].mp4"
                self.assertEqual(messages[-1]["files"], [str(expected_path)])
                self.assertEqual(Path(messages[-1]["files"][0]).read_bytes(), b"fixture")
            finally:
                for child in children:
                    child.stdout.close()

    def test_legacy_protocol_keeps_literal_quotes_while_json_decodes_escapes(self):
        self.assertEqual(host.output_text('"literal title"'), '"literal title"')
        title = ' 中文 🎬 | "quoted"\nline two '
        self.assertEqual(host.output_text(json.dumps(title), json_encoded=True), title)
        path = 'C:\\下载\\中文视频 [123].mp4'
        self.assertEqual(host.output_text(json.dumps(path), json_encoded=True), path)

    def test_repair_paths_recovers_only_a_unique_file_in_the_download_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "正确的中文标题 [BV1bK411W797_p2].mp4"
            valid.write_bytes(b"video")
            broken = str(Path(directory) / "乱码\ufffd [BV1bK411W797_p2].mp4")
            with patch.object(host, "load_config", return_value={"downloadDir": directory}), \
                    patch.object(host, "send") as send:
                host.handle({"action": "repairPaths", "rid": "repair", "paths": [broken]})
            self.assertEqual(send.call_args.args[0], {
                "type": "repairedPaths", "rid": "repair", "paths": {broken: str(valid)},
            })
            self.assertEqual(valid.read_bytes(), b"video")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), [valid.name])

    def test_repair_paths_rejects_outside_relative_and_ambiguous_paths(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "downloads"
            directory.mkdir()
            outside = Path(root) / "outside"
            outside.mkdir()
            (directory / "视频甲 [123].mp4").touch()
            (directory / "视频乙 [123].mp4").touch()
            (directory / "普通视频 [456].mp4").touch()
            (outside / "目录外视频 [789].mp4").touch()
            paths = [
                str(directory / "乱码\ufffd [123].mp4"),  # Two candidate files
                str(outside / "乱码\ufffd [789].mp4"),
                str(directory / ".." / "outside" / "乱码\ufffd [789].mp4"),
                "乱码\ufffd [456].mp4",
                str(directory / "本来正常 [456].mp4"),
                str(directory / "乱码\ufffd [bad id].mp4"),
                str(directory / "乱码\ufffd [456].exe"),
                str(directory / "乱码\ufffd [missing].mp4"),
                None,
            ]
            with patch.object(host, "load_config", return_value={"downloadDir": str(directory)}):
                self.assertEqual(host.repair_paths(paths), {})
                self.assertEqual(host.repair_paths("not a list"), {})

    def test_repair_paths_limits_the_number_of_requested_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "视频 [123].mp4"
            valid.touch()
            broken = str(Path(directory) / "乱码\ufffd [123].mp4")
            with patch.object(host, "load_config", return_value={"downloadDir": directory}):
                self.assertEqual(host.repair_paths([None] * 30 + [broken]), {})
                self.assertEqual(host.repair_paths([broken] + [None] * 30), {broken: str(valid)})


if __name__ == "__main__":
    unittest.main()
