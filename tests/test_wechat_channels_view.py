"""Real Tk state transitions with synthetic videos; no process reads/network."""
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from tools.wechat_channels import app
from tools.wechat_channels.download import Cancelled, DownloadError


class DeferredWorker:
    """Let a test change selection while the actual worker closure is pending."""
    def __init__(self, target, daemon):
        self.target = target
        self.running = False

    def start(self):
        self.running = True

    def is_alive(self):
        return self.running

    def run(self):
        try:
            self.target()
        finally:
            self.running = False


class VideoViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.scan_patch = patch.object(app.App, 'scan')
        self.scan_patch.start()
        self.thumbnail_patch = patch.object(app.App, 'queue_thumbnails')
        self.thumbnail_patch.start()
        self.path_patches = [patch.object(app, name, self.folder / value) for name, value in (
            ('BASE', 'state'), ('STATE', 'state/session.json'),
            ('MARKER', 'state/marker.json'), ('SCAN_REPORT', 'state/report.json'))]
        for patcher in self.path_patches:
            patcher.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = app.App(self.root)
        self.ui.folder.set(str(self.folder))
        self.long_title = '标题需要完整显示：' + '旅行、城市与沿途风景。' * 30 + '\n第二行介绍'
        self.a = {'id': 'a', 'title': self.long_title, 'author': '远方旅行者',
                  'duration': 126.9, 'decode_key': '123', 'url': 'https://finder.video.qq.com/a'}
        self.b = {'id': 'b', 'title': 'Basketball 教学', 'author': '运动记录',
                  'duration': 44, 'decode_key': '456', 'url': 'https://finder.video.qq.com/b'}
        self.root.update()

    def tearDown(self):
        # Do not run App.close(), which also owns the real certificate lifecycle.
        self.ui.scan_cancel.set()
        self.ui.cancel.set()
        for timer in self.root.tk.call('after', 'info'):
            # Timers may be registered by child widgets; let each owning widget
            # destroy its Tcl callback instead of deleting it through root.
            self.root.tk.call('after', 'cancel', timer)
        self.root.destroy()
        for patcher in reversed(self.path_patches):
            patcher.stop()
        self.scan_patch.stop()
        self.thumbnail_patch.stop()
        self.temp.cleanup()

    def select(self, ident):
        self.ui.tree.selection_set(ident)
        self.ui.tree.event_generate('<<TreeviewSelect>>')
        self.root.update()

    def render(self):
        self.ui.render_candidates([self.a, self.b])
        self.root.update()

    def start_download(self, ident='a'):
        self.select(ident)
        with patch.object(app.threading, 'Thread', DeferredWorker):
            self.ui.download()
        return self.ui.worker

    def test_initial_empty_selection_disables_download_actions(self):
        self.assertEqual(self.ui.tree.get_children(), ())
        self.assertIsNone(self.ui.selected_id)
        self.assertEqual(self.ui.tree.widgets, {})
        with patch.object(app.threading, 'Thread') as worker:
            self.ui.download()
            worker.assert_not_called()
        self.assertEqual(self.ui.selected_author.get(), '—')
        self.assertEqual(self.ui.selected_duration.get(), '—')

    def test_selection_details_keep_full_long_title_and_separate_duration(self):
        self.render()
        self.select('a')
        self.assertEqual(self.ui.selected_title.get(), self.long_title)
        self.assertEqual(self.ui.selected_author.get(), '远方旅行者')
        self.assertEqual(self.ui.selected_duration.get(), '2:06')
        self.assertEqual(self.ui.tree.item('a', 'values')[1:3], ('远方旅行者', '2:06'))
        self.assertTrue(self.ui.tree.widgets['a']['button'].instate(['!disabled']))

    def test_search_filters_by_title_and_author_and_clears_stale_details(self):
        self.render()
        self.ui.search.set('basketBALL')
        self.root.update()
        self.assertEqual(self.ui.tree.get_children(), ('b',))
        self.assertEqual(self.ui.selected_id, 'b')
        self.ui.search.set('远方')
        self.root.update()
        self.assertEqual(self.ui.tree.get_children(), ('a',))
        self.ui.search.set('完全不存在的关键词')
        self.root.update()
        self.assertEqual(self.ui.tree.get_children(), ())
        self.assertIsNone(self.ui.selected_id)
        self.assertEqual(self.ui.tree.widgets, {})
        self.assertEqual(self.ui.selected_title.get(), '选择一个视频')
        self.assertIn('0 / 2', self.ui.list_count.get())

    def test_search_repeated_input_never_requests_canvas_keyboard_focus(self):
        self.render()
        with patch.object(self.ui.tree.canvas, 'focus_set') as take_focus:
            for query in ('b', 'ba', 'bas', 'basket'):
                self.ui.search.set(query)
                self.root.update()
            take_focus.assert_not_called()
        self.assertEqual(self.ui.search.get(), 'basket')
        self.assertEqual(self.ui.tree.get_children(), ('b',))

    def test_refresh_preserves_selection_and_updates_metadata(self):
        self.render()
        self.select('b')
        updated = dict(self.b, title='更新后的标题', duration=3665)
        self.ui.events.put(('scan_done', [updated, self.a], {'videos_found': 2}))
        self.ui.poll()
        self.root.update()
        self.assertEqual(self.ui.selected_id, 'b')
        self.assertEqual(self.ui.selected_title.get(), '更新后的标题')
        self.assertEqual(self.ui.selected_duration.get(), '1:01:05')
        self.assertEqual(self.ui.scan_result['ui_rows'], 2)

    def test_source_switch_clears_previous_source_even_when_next_scan_fails(self):
        self.render()
        self.ui.source.set('微信主界面')
        self.ui.source_picker.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertEqual(self.ui.candidates, {})
        self.assertEqual(self.ui.tree.get_children(), ())
        self.assertEqual(self.ui.tree.widgets, {})
        self.ui.events.put(('scan_error', '当前来源暂时不可读取', {}))
        self.ui.poll()
        self.assertEqual(self.ui.candidates, {})
        self.assertEqual(self.ui.scan_result['result'], 'error')
        self.assertEqual(self.ui.selected_author.get(), '—')

    def test_active_download_identity_survives_selection_and_refresh(self):
        self.render()
        worker = self.start_download('a')
        task_title = self.ui.task_title.get()
        self.select('b')
        self.ui.render_candidates([dict(self.a, title='读取到的新标题'), self.b])
        self.root.update()
        self.assertEqual(self.ui.active_download_id, 'a')
        self.assertEqual(self.ui.task_title.get(), task_title)
        self.assertEqual(self.ui.selected_id, 'b')
        self.assertTrue(all(parts['button'].instate(['disabled']) for parts in self.ui.tree.widgets.values()))
        saved = self.folder / 'completed.mp4'
        with patch.object(app, 'download_video', return_value=saved) as download:
            worker.run()
        self.ui.poll()
        self.root.update()
        self.assertEqual(download.call_args.args[0]['title'], self.long_title)
        self.assertEqual(self.ui.saved_files, {'a': saved})
        self.assertEqual(self.ui.download_states['a'], '已完成')
        self.assertEqual(self.ui.transfer_state.get(), '下载完成')
        self.assertIsNone(self.ui.active_download_id)
        self.assertEqual(self.ui.tree.widgets['b']['button'].cget('text'), '下载视频')
        self.assertEqual(self.ui.tree.widgets['a']['button'].cget('text'), '打开视频')
        with patch.object(app.os, 'startfile') as open_file:
            self.ui.tree.widgets['a']['button'].invoke()
        open_file.assert_called_once_with(saved)

    def test_cancel_stops_progress_and_does_not_create_saved_file(self):
        self.render()
        worker = self.start_download()
        self.ui.events.put(('progress', 40, 100))
        self.ui.poll()
        self.root.update()
        self.assertEqual(float(self.ui.progress['value']), 40)
        self.ui.cancel_download()
        self.assertTrue(self.ui.cancel.is_set())
        self.assertEqual(self.ui.transfer_state.get(), '正在取消')
        self.assertTrue(self.ui.cancel_button.instate(['disabled']))
        with patch.object(app, 'download_video', side_effect=Cancelled('已取消下载')):
            worker.run()
        self.ui.poll()
        self.assertEqual(self.ui.transfer_state.get(), '已取消')
        self.assertEqual(self.ui.download_states['a'], '已取消')
        self.assertEqual(float(self.ui.progress['value']), 0)
        self.assertEqual(self.ui.saved_files, {})
        self.root.update()
        self.assertTrue(self.ui.tree.widgets['a']['button'].instate(['!disabled']))

    def test_failed_download_resets_progress_and_enables_retry(self):
        self.render()
        worker = self.start_download()
        self.ui.events.put(('progress', 80, 100))
        self.ui.poll()
        with patch.object(app, 'download_video', side_effect=DownloadError('地址已经过期')):
            worker.run()
        self.ui.poll()
        self.assertEqual(self.ui.transfer_state.get(), '下载失败')
        self.assertEqual(self.ui.detail.get(), '地址已经过期')
        self.assertEqual(float(self.ui.progress['value']), 0)
        self.assertIsNone(self.ui.active_download_id)
        self.assertEqual(self.ui.saved_files, {})
        self.root.update()
        self.assertTrue(self.ui.tree.widgets['a']['button'].instate(['!disabled']))

    def test_card_button_downloads_its_own_video_not_previous_selection(self):
        self.render()
        self.select('b')
        with patch.object(app.threading, 'Thread', DeferredWorker):
            self.ui.tree.widgets['a']['button'].invoke()
        self.assertEqual(self.ui.active_download_id, 'a')
        self.assertEqual(self.ui.task_title.get(), self.long_title[:90])

    def test_thumbnail_results_do_not_change_title_or_selected_video(self):
        url = 'https://finder.video.qq.com/cover?token=synthetic'
        self.a['thumbnail_url'] = url
        self.render()
        self.select('a')
        title = self.ui.selected_title.get()
        records = dict(self.ui.tree.records)
        self.ui.events.put(('thumbnail', url, Image.new('RGB', (70, 100), 'blue')))
        self.ui.poll()
        self.root.update()
        self.assertEqual(self.ui.selected_title.get(), title)
        self.assertEqual(self.ui.selected_id, 'a')
        self.assertEqual(self.ui.tree.records, records)
        self.assertTrue(self.ui.tree.widgets['a']['image'].cget('image'))
        self.ui.events.put(('thumbnail', 'https://finder.video.qq.com/stale', None))
        self.ui.poll()
        self.assertNotIn('https://finder.video.qq.com/stale', self.ui.tree.failed_images)

    def test_keyboard_selection_and_cover_scroll_work_on_canvas(self):
        self.render()
        self.select('a')
        self.assertTrue(self.ui.tree.canvas.bind('<Down>'))
        self.assertTrue(self.ui.tree.canvas.bind('<Return>'))
        with patch.object(self.ui.tree, 'see') as see:
            self.ui.tree._navigate(1)
        self.assertEqual(self.ui.tree.selection(), ('b',))
        see.assert_called_once_with('b')
        with patch.object(self.ui, 'card_action') as action:
            self.ui.tree._activate_selected()
        action.assert_called_once_with('b')
        self.assertTrue(self.ui.tree.widgets['b']['button'].bind('<MouseWheel>'))
        self.assertTrue(self.ui.tree.widgets['b']['state'].bind('<MouseWheel>'))
        with patch.object(self.ui.tree.inner, 'winfo_reqheight', return_value=1000), \
             patch.object(self.ui.tree.canvas, 'winfo_height', return_value=200), \
             patch.object(self.ui.tree.canvas, 'yview_scroll') as scroll:
            for _ in range(4):
                self.ui.tree._wheel(SimpleNamespace(delta=-30))
        scroll.assert_called_once_with(1, 'units')

    def test_see_brings_offscreen_card_into_view_and_prunes_stale_images(self):
        self.a['thumbnail_url'] = 'https://finder.video.qq.com/coverA'
        self.render()
        self.ui.tree.set_thumbnail(self.a['thumbnail_url'], Image.new('RGB', (10, 10)))
        with patch.object(self.ui.tree.inner, 'winfo_reqheight', return_value=1000), \
             patch.object(self.ui.tree.canvas, 'winfo_height', return_value=200), \
             patch.object(self.ui.tree.canvas, 'canvasy', return_value=0), \
             patch.object(self.ui.tree.widgets['b']['border'], 'winfo_y', return_value=700), \
             patch.object(self.ui.tree.widgets['b']['border'], 'winfo_height', return_value=188), \
             patch.object(self.ui.tree.canvas, 'yview_moveto') as move:
            self.ui.tree.see('b')
        self.assertAlmostEqual(move.call_args.args[0], .688)
        self.ui.render_candidates([self.b])
        self.root.update()
        self.assertEqual(self.ui.tree.images, {})
        self.assertNotIn('a', self.ui.tree.photos)

    def test_default_filter_can_reveal_unidentified_videos(self):
        self.render()
        unknown = {'id': 'unknown', 'url': 'https://finder.video.qq.com/unknown'}
        self.ui.render_candidates([self.a, self.b, unknown])
        self.root.update()
        self.assertNotIn('unknown', self.ui.tree.records)
        self.ui.toggle_all()
        self.root.update()
        self.assertIn('unknown', self.ui.tree.records)


if __name__ == '__main__':
    unittest.main()
