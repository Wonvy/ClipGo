import json
import threading
import unittest
from unittest.mock import Mock, patch

from tools.wechat_channels import memory


URL = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=video&token=signed&idx=1'


class MemoryTests(unittest.TestCase):
    def setUp(self):
        bound = patch('tools.wechat_channels.v8_media.extract_bound_media', return_value=[])
        bound.start()
        self.addCleanup(bound.stop)

    def test_progressive_rows_arrive_before_scan_finishes_and_are_detached(self):
        events = []
        def chunks(reader, process, cancel, progress, diagnostics=None):
            diagnostics['workers_opened'] += 1
            diagnostics['bytes_read'] += 1
            yield URL.encode() + b'\0'
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][0]['url'], URL)
            events[0][0]['url'] = 'https://example.com/untrusted-ui-mutation'
            yield b'no second media\0'
        with patch.object(memory, 'discover_processes', return_value=[{}]), \
             patch.object(memory, 'WindowsMemoryReader'), \
             patch.object(memory, 'iter_scoped_chunks', side_effect=chunks):
            found = memory.scan_loaded_videos(threading.Event(), source='main', on_items=events.append)
        self.assertEqual(found[0]['url'], URL)

    def test_progressive_titled_pairs_arrive_inside_bound_scan(self):
        pair = {'id': memory.loaded_identity(URL), 'url': URL, 'decode_key': '123', 'title': '绑定标题'}
        def chunks(reader, process, cancel, progress, diagnostics=None):
            diagnostics['workers_opened'] += 1
            diagnostics['bytes_read'] += 1
            yield URL.encode() + b'\0'
        events = []
        def bound(reader, process, cancel, on_items=None, allowed_ids=None):
            on_items([pair])
            self.assertEqual(events[-1][0]['title'], '绑定标题')
            self.assertEqual(events[-1][0]['decode_key'], '123')
            return [pair]
        with patch.object(memory, 'discover_processes', return_value=[{}]), \
             patch.object(memory, 'WindowsMemoryReader'), \
             patch.object(memory, 'iter_scoped_chunks', side_effect=chunks), \
             patch('tools.wechat_channels.v8_media.extract_bound_media', side_effect=bound):
            memory.scan_loaded_videos(threading.Event(), on_items=events.append)
        self.assertEqual(len(events), 2)

    def test_only_complete_signed_video_urls(self):
        for bad in (URL + '&picformat=200', URL + '&wxampicformat=jpg',
                    URL.replace('token=signed', 'unused=1'), URL.replace('finder.video.qq.com', 'example.com')):
            self.assertEqual(memory.extract_loaded_urls(bad.encode() + b'\0'), [])
        self.assertEqual(memory.extract_loaded_urls(URL.encode()), [])
        self.assertEqual(memory.extract_loaded_urls(b'private\0' + URL.encode() + b'\0unrelated'), [URL])

    def test_dedupe_aliases_and_prefer_complete_signed_url(self):
        richer = URL + '&basedata=data&sign=sig&web=1'
        self.assertEqual(memory.loaded_identity(URL), memory.loaded_identity(richer))
        self.assertNotEqual(memory.loaded_identity(URL), memory.loaded_identity(URL.replace('video', 'other', 1)))
        self.assertGreater(memory._url_rank(richer), memory._url_rank(URL))

    def test_process_filter_excludes_main_and_other_renderers(self):
        valid = {'ExecutablePath': 'C:\\Weixin\\WeChatAppEx.exe', 'CommandLine': 'app --type=renderer --wmpf-render-type=6', 'ProcessId': 1, 'CreationTicks': 123}
        rows = [valid, dict(valid, ProcessId=2, CreationTicks=125), dict(valid, ProcessId=3, CreationTicks=124)]
        rows += [dict(valid, ExecutablePath='C:\\Weixin.exe'), dict(valid, CommandLine='app --type=renderer --wmpf-render-type=7')]
        self.assertEqual([p['pid'] for p in memory.select_processes(rows, 'main')], [2, 3])
        self.assertEqual([p['pid'] for p in memory.select_processes(rows, 'standalone')], [1])
        with self.assertRaises(ValueError):
            memory.select_processes(rows, 'unsupported')

    def test_all_players_select_two_each_and_interleave_main_first(self):
        rows = []
        for kind, first_pid in ((6, 0), (7, 10)):
            for index in range(1, 4):
                rows.append({'ExecutablePath': 'C:\\Weixin\\WeChatAppEx.exe',
                             'CommandLine': f'app --type=renderer --wmpf-render-type={kind}',
                             'ProcessId': first_pid + index, 'CreationTicks': 100 + index})
        rows.extend((dict(rows[0], ExecutablePath='C:\\Weixin.exe'),
                     dict(rows[0], CommandLine='app --type=utility --wmpf-render-type=6'),
                     dict(rows[0], CommandLine='app --type=renderer --wmpf-render-type=4')))
        selected = memory.select_processes(rows)
        self.assertEqual([p['pid'] for p in selected], [3, 13, 2, 12])
        self.assertEqual([p['source'] for p in selected], ['main', 'standalone', 'main', 'standalone'])

    def test_main_accepts_exact_bound_pair_without_flat_url_duplicate(self):
        pair = {'id': memory.loaded_identity(URL), 'url': URL, 'decode_key': '123',
                'title': '秋色银杏', 'thumbnail_url': URL + '&picformat=200'}
        def chunks(reader, process, cancel, progress, diagnostics=None):
            diagnostics['workers_opened'] += 1
            diagnostics['bytes_read'] += 1
            yield b'no materialized signed URLs in this native worker'
        events = []
        with patch.object(memory, 'discover_processes', return_value=[{'source': 'main'}]) as discover, \
             patch.object(memory, 'WindowsMemoryReader'), \
             patch.object(memory, 'iter_scoped_chunks', side_effect=chunks), \
             patch('tools.wechat_channels.v8_media.extract_bound_media', return_value=[pair]) as bound:
            found = memory.scan_loaded_videos(threading.Event(), on_items=events.append)
        discover.assert_called_once_with('all')
        self.assertIsNone(bound.call_args.kwargs['allowed_ids'])
        self.assertEqual((found[0]['url'], found[0]['decode_key']), (URL, '123'))
        self.assertEqual(events[-1][0]['title'], '秋色银杏')

    def test_standalone_gate_still_rejects_unobserved_residual_bound_video(self):
        observed = URL.replace('encfilekey=video', 'encfilekey=different-video')
        pair = {'id': memory.loaded_identity(URL), 'url': URL, 'decode_key': '123', 'title': '旧视频'}
        def chunks(reader, process, cancel, progress, diagnostics=None):
            diagnostics['workers_opened'] += 1
            diagnostics['bytes_read'] += 1
            yield observed.encode() + b'\0'
        with patch.object(memory, 'discover_processes', return_value=[{'source': 'standalone'}]), \
             patch.object(memory, 'WindowsMemoryReader'), \
             patch.object(memory, 'iter_scoped_chunks', side_effect=chunks), \
             patch('tools.wechat_channels.v8_media.extract_bound_media', return_value=[pair]) as bound:
            found = memory.scan_loaded_videos(threading.Event())
        self.assertEqual(bound.call_args.kwargs['allowed_ids'], {memory.loaded_identity(observed)})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['url'], observed)
        self.assertIsNone(found[0]['decode_key'])

    def test_overlap_byte_budget_and_close(self):
        data = b'padding' + URL.encode() + b'\0suffix'
        reader = Mock()
        reader.open.return_value = 10
        reader.query.side_effect = lambda handle, address: (0, len(data), True) if address == 0 else None
        reader.read.side_effect = lambda handle, start, amount: data[start:start+amount]
        progress = Mock()
        chunks = list(memory.iter_scoped_chunks(reader, {}, threading.Event(), progress, chunk_size=20))
        self.assertIn(URL, {url for chunk in chunks for url in memory.extract_loaded_urls(chunk)})
        reader.close.assert_called_once_with(10)
        self.assertEqual(sum(call.args[2] for call in reader.read.call_args_list), len(data))
        reader.reset_mock()
        list(memory.iter_scoped_chunks(reader, {}, threading.Event(), progress, byte_limit=10, chunk_size=20))
        self.assertEqual(reader.read.call_args.args[2], 10)
        reader.close.assert_called_once()

    def test_cancel_never_opens_process(self):
        reader = Mock(); cancel = threading.Event(); cancel.set()
        self.assertEqual(list(memory.iter_scoped_chunks(reader, {}, cancel, Mock())), [])
        reader.open.assert_not_called()

    def test_no_cross_region_pairing_after_unreadable_gap(self):
        reader = Mock(); reader.open.return_value = 1
        pieces = [(0, 30, True), (30, 30, False), (60, 30, True), None]
        reader.query.side_effect = pieces
        reader.read.side_effect = [URL.encode()[:30], URL.encode()[30:] + b'\0']
        chunks = list(memory.iter_scoped_chunks(reader, {}, threading.Event(), Mock()))
        self.assertEqual([u for chunk in chunks for u in memory.extract_loaded_urls(chunk)], [])

    def test_scan_reports_unreadable_workers_instead_of_empty_success(self):
        reader = Mock(last_error='access_denied')
        reader.open.return_value = None
        stats = {}
        with patch.object(memory, 'discover_processes', return_value=[{'pid': 123}]), \
             patch.object(memory, 'WindowsMemoryReader', return_value=reader):
            with self.assertRaisesRegex(memory.ScanError, '读取权限不足'):
                memory.scan_loaded_videos(threading.Event(), diagnostics=stats)
        self.assertEqual(stats['workers_discovered'], 1)
        self.assertEqual(stats['workers_opened'], 0)
        self.assertEqual(stats['read_failures'], {'access_denied': 1})
        self.assertNotIn('pid', str(stats))

    def test_scan_distinguishes_identity_change(self):
        reader = Mock(last_error='identity_changed')
        reader.open.return_value = None
        stats = {}
        with patch.object(memory, 'discover_processes', return_value=[{}]), \
             patch.object(memory, 'WindowsMemoryReader', return_value=reader):
            with self.assertRaisesRegex(memory.ScanError, '可能刚刚重启'):
                memory.scan_loaded_videos(threading.Event(), diagnostics=stats)
        self.assertEqual(stats['read_failures'], {'identity_changed': 1})

    def test_scan_diagnostics_contain_counts_not_urls(self):
        data = URL.encode() + b'\0'
        reader = Mock()
        reader.open.return_value = 1
        reader.query.side_effect = [(0, len(data), True), None]
        reader.read.return_value = data
        stats = {}
        with patch.object(memory, 'discover_processes', return_value=[{}]), \
             patch.object(memory, 'WindowsMemoryReader', return_value=reader):
            found = memory.scan_loaded_videos(threading.Event(), diagnostics=stats, source='main')
        self.assertEqual(len(found), 1)
        self.assertEqual(stats['workers_opened'], 1)
        self.assertEqual(stats['bytes_read'], len(data))
        self.assertEqual(stats['videos_found'], 1)
        self.assertNotIn('token', str(stats))
        self.assertNotIn(URL, str(stats))

    def test_standalone_keeps_exact_bound_url_and_key(self):
        observed = URL + '&basedata=new&sign=new'
        paired_url = URL.replace('token=signed', 'token=paired')
        pair = {'id': memory.loaded_identity(paired_url), 'url': paired_url, 'decode_key': '123', 'duration': 1407}
        data = observed.encode() + b'\0'
        reader = Mock(); reader.open.return_value = 1
        reader.query.side_effect = [(0, len(data), True), None]
        reader.read.return_value = data
        with patch.object(memory, 'discover_processes', return_value=[{}]), \
             patch.object(memory, 'WindowsMemoryReader', return_value=reader), \
             patch('tools.wechat_channels.v8_media.extract_bound_media', return_value=[pair]):
            found = memory.scan_loaded_videos(threading.Event())
        self.assertEqual(found[0]['url'], paired_url)
        self.assertEqual(found[0]['decode_key'], '123')
        self.assertEqual(found[0]['duration'], 1407)

    def test_second_renderer_keyless_metadata_preserves_complete_pair(self):
        paired_url = URL.replace('token=signed', 'token=paired')
        pair = {'id': memory.loaded_identity(URL), 'url': paired_url,
                'decode_key': '123', 'duration': 1407}
        metadata = json.dumps({'objectDesc': {'media': [{'url': URL, 'urlToken': ''}]}}).encode() + b'\0'
        def chunks(reader, process, cancel, progress, diagnostics=None):
            diagnostics['workers_opened'] += 1
            diagnostics['bytes_read'] += 1
            yield URL.encode() + b'\0' if process['pid'] == 1 else metadata
        with patch.object(memory, 'discover_processes', return_value=[{'pid': 1}, {'pid': 2}]), \
             patch.object(memory, 'WindowsMemoryReader'), \
             patch.object(memory, 'iter_scoped_chunks', side_effect=chunks), \
             patch('tools.wechat_channels.v8_media.extract_bound_media', side_effect=[[pair], []]):
            found = memory.scan_loaded_videos(threading.Event())
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]['url'], found[0]['decode_key']), (paired_url, '123'))
        self.assertEqual(found[0]['duration'], 1407)

    def test_multiple_renderers_merge_exact_pairs_for_fallback(self):
        first = {'id': memory.loaded_identity(URL), 'url': URL, 'decode_key': '11'}
        second = dict(first, url=URL.replace('token=signed', 'token=second'), decode_key='22')
        def chunks(reader, process, cancel, progress, diagnostics=None):
            diagnostics['workers_opened'] += 1
            diagnostics['bytes_read'] += 1
            yield URL.encode() + b'\0'
        with patch.object(memory, 'discover_processes', return_value=[{'pid': 1}, {'pid': 2}]), \
             patch.object(memory, 'WindowsMemoryReader'), \
             patch.object(memory, 'iter_scoped_chunks', side_effect=chunks), \
             patch('tools.wechat_channels.v8_media.extract_bound_media', side_effect=[[first], [second, first]]):
            found = memory.scan_loaded_videos(threading.Event())
        self.assertEqual((found[0]['url'], found[0]['decode_key']), (first['url'], '11'))
        self.assertEqual(found[0]['media_options'], [{'url': second['url'], 'decode_key': '22'}])

    def test_pair_merge_deduplicates_exact_pairs_and_bounds_retries(self):
        primary = {'id': memory.loaded_identity(URL), 'url': URL, 'decode_key': '0'}
        merged = primary
        # Same URL with a different explicit key is still a separate bound pair.
        for number in range(6):
            candidate = dict(primary, decode_key=str(number))
            merged = memory.merge_media_candidate(merged, candidate)
            merged = memory.merge_media_candidate(merged, candidate)
        self.assertEqual(merged['url'], URL)
        self.assertEqual(merged['decode_key'], '0')
        self.assertEqual(merged['media_options'], [
            {'url': URL, 'decode_key': str(number)} for number in (1, 2, 3)])
        keyless = dict(primary, url=URL + '&basedata=other', decode_key=None)
        self.assertEqual(memory.merge_media_candidate(merged, keyless), merged)

    def test_bound_title_enriches_placeholder_without_changing_response_pair(self):
        first = {'id': memory.loaded_identity(URL), 'url': URL, 'decode_key': '11', 'title': '视频号视频'}
        named = dict(first, url=URL + '&variant=next', decode_key='22',
                     title='真实视频标题', author='真实作者', duration=1407)
        merged = memory.merge_media_candidate(first, named)
        self.assertEqual((merged['url'], merged['decode_key']), (URL, '11'))
        self.assertEqual((merged['title'], merged['author']), ('真实视频标题', '真实作者'))
        self.assertEqual(merged['media_options'], [{'url': named['url'], 'decode_key': '22'}])

    def test_cancelled_scan_diagnostics_do_not_report_access_failure(self):
        cancel = threading.Event(); cancel.set(); stats = {}
        with patch.object(memory, 'discover_processes') as discover:
            self.assertEqual(memory.scan_loaded_videos(cancel, diagnostics=stats), [])
            discover.assert_not_called()
        self.assertEqual(stats['workers_opened'], 0)


if __name__ == '__main__':
    unittest.main()
