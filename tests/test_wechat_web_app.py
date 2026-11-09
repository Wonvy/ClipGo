"""HTML bridge integration with controlled scanner/network workers."""
import json
import http.client
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from PIL import Image
from tools.wechat_channels.web_app import Api, _assets_path, _diagnostic_result
from tools.wechat_channels.download import Cancelled, DownloadError
from tools.wechat_channels.memory import ScanError
from tools.wechat_channels.playback import PlaybackServer


def wait_until(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Worker did not complete')


def video(ident='a', **overrides):
    return dict({'id': ident, 'url': 'https://finder.video.qq.com/file?token=PRIVATE_URL',
        'decode_key': 'PRIVATE_KEY', 'title': '真实标题 <script>not executable</script>',
        'author': '作者', 'duration': 23.7, 'thumbnail_url': '',
        'media_options': [{'url': 'https://finder.video.qq.com/other?token=PRIVATE_PAIR', 'decode_key': 'OTHER_KEY'}]}, **overrides)


class WebDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.report = {'result': 'done', 'ui_rows': 2, 'titled_rows': 1, 'thumbnail_rows': 1}
        self.dom = {'ready': True, 'cards': 2, 'titled': 1, 'thumbnails': 1,
                    'renderedRevision': 5, 'horizontalOverflow': False, 'frontendErrors': 0}

    def test_waits_for_revision_and_async_image_decode(self):
        self.assertIsNone(_diagnostic_result(self.report, dict(self.dom, renderedRevision=4, cards=0), 5))
        self.assertIsNone(_diagnostic_result(self.report, dict(self.dom, thumbnails=0), 5))
        self.assertIsNone(_diagnostic_result(self.report, None, 5))
        result = _diagnostic_result(self.report, self.dom, 5)
        self.assertEqual(result['result'], 'done')
        self.assertEqual(result['frontend_errors'], 0)
        self.assertEqual((result['dom_cards'], result['dom_titled'], result['dom_images']), (2, 1, 1))

    def test_empty_dom_cannot_pass_when_covers_are_unavailable(self):
        report = dict(self.report, thumbnail_rows=0)
        result = _diagnostic_result(report, dict(self.dom, cards=0, thumbnails=0), 5)
        self.assertEqual((result['result'], result['reason']), ('error', 'card_count_mismatch'))

    def test_placeholder_titles_and_overflow_cannot_report_success(self):
        for changes, reason in (({'titled': 2}, 'title_count_mismatch'),
                                ({'horizontalOverflow': True}, 'horizontal_overflow'),
                                ({'thumbnails': 2}, 'image_count_mismatch')):
            with self.subTest(reason=reason):
                result = _diagnostic_result(self.report, dict(self.dom, **changes), 5)
                self.assertEqual((result['result'], result['reason']), ('error', reason))

    def test_frontend_exception_fails_even_before_final_render_revision(self):
        result = _diagnostic_result(self.report, dict(self.dom, frontendErrors=2, renderedRevision=1), 5)
        self.assertEqual((result['result'], result['reason'], result['frontend_errors']), ('error', 'frontend_error', 2))

    def test_bad_dom_shape_is_safe_failure_and_scan_failure_is_not_upgraded(self):
        result = _diagnostic_result(self.report, dict(self.dom, cards='not-an-int'), 5)
        self.assertEqual((result['result'], result['reason']), ('error', 'invalid_dom_report'))
        result = _diagnostic_result(dict(self.report, result='error'), self.dom, 5)
        self.assertEqual(result['result'], 'error')

    def test_optional_engagement_diagnostics_require_actual_matching_dom_counts(self):
        report = dict(self.report, engagement_rows=2, complete_engagement_rows=1)
        dom = dict(self.dom, engagementRows=2, completeEngagementRows=1)
        result = _diagnostic_result(report, dom, 5)
        self.assertEqual(result['result'], 'done')
        self.assertEqual((result['dom_engagement_rows'], result['dom_complete_engagement_rows']), (2, 1))
        for changed, reason in (({'engagementRows': 0}, 'engagement_count_mismatch'),
                                ({'completeEngagementRows': 0}, 'complete_engagement_count_mismatch'),
                                ({'completeEngagementRows': None}, 'invalid_dom_report'),
                                ({'engagementRows': True}, 'invalid_dom_report')):
            with self.subTest(changed=changed):
                result = _diagnostic_result(report, dict(dom, **changed), 5)
                self.assertEqual((result['result'], result['reason']), ('error', reason))
        # Old reports deliberately omit these counters and remain compatible.
        self.assertEqual(_diagnostic_result(self.report, self.dom, 5)['result'], 'done')


class WebBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.apis = []

    def tearDown(self):
        for api in self.apis:
            api._shutdown()
        self.temp.cleanup()

    def make_api(self, **kwargs):
        api = Api(folder=self.folder, **kwargs)
        self.apis.append(api)
        return api

    def scan(self, api, source='all'):
        self.assertTrue(api.scan(source)['ok'])
        wait_until(lambda: api.get_state()['scan']['state'] in ('done', 'error'))
        return api.get_state()

    def test_public_snapshot_and_report_never_expose_media_secrets(self):
        report = self.folder / 'report.json'
        api = self.make_api(scanner=lambda *_args, **_kwargs: [video()], report_path=report)
        state = self.scan(api)
        self.assertEqual(state['items'][0]['title'], video()['title'])
        self.assertEqual(state['items'][0]['duration'], 23.7)
        serialized = json.dumps(state) + report.read_text()
        for secret in ('PRIVATE_URL', 'PRIVATE_KEY', 'PRIVATE_PAIR', 'OTHER_KEY', 'decode_key', 'media_options', 'thumbnail_url'):
            self.assertNotIn(secret, serialized)
        state['items'][0]['title'] = 'browser mutated'
        self.assertNotEqual(api.get_state()['items'][0]['title'], 'browser mutated')
        methods = {name for name in dir(Api) if not name.startswith('_') and callable(getattr(Api, name))}
        self.assertEqual(methods, {'get_state', 'scan', 'download', 'cancel_download', 'choose_folder',
                                   'open_folder', 'open_video', 'prepare_preview', 'close_preview',
                                   'remove_video', 'clear_videos', 'set_page_button',
                                   'set_page_certificate_keep', 'remove_page_certificate'})
        self.assertNotIn('companion', state)

    def test_page_click_uses_exact_new_pair_even_if_cached_item_has_a_key(self):
        received = []
        def download(candidate, folder, cancel, progress):
            received.append(candidate)
            return folder / 'page-video.mp4'
        api = self.make_api(scanner=lambda *_a, **_kw: [video()], downloader=download)
        self.scan(api)
        new = video(url='https://finder.video.qq.com/new?token=NEW_PAGE', decode_key=None,
                    media_options=[])
        self.assertTrue(api._page_candidate(new, download=True)['ok'])
        wait_until(lambda: bool(received))
        self.assertEqual((received[0]['url'], received[0]['decode_key']), (new['url'], None))
        self.assertNotIn('NEW_PAGE', json.dumps(api.get_state()))

    def test_certificate_preference_is_explicit_and_boolean_only(self):
        with patch('tools.wechat_channels.page_controller.certificate_status',
                   return_value={'keep': False, 'installed': False}) as status, \
             patch('tools.wechat_channels.page_controller.set_certificate_preference') as choose:
            api = self.make_api()
            self.assertFalse(api.get_state()['page_certificate']['keep'])
            for value in (1, 'true', None):
                self.assertFalse(api.set_page_certificate_keep(value)['ok'])
            choose.assert_not_called()
            status.return_value = {'keep': True, 'installed': True}
            self.assertTrue(api.set_page_certificate_keep(True)['ok'])
            choose.assert_called_once_with(True)
            snapshot = api.get_state()['page_certificate']
            snapshot['keep'] = False
            self.assertTrue(api.get_state()['page_certificate']['keep'])

    def test_removing_certificate_stops_page_service_first(self):
        order = []
        with patch('tools.wechat_channels.page_controller.certificate_status',
                   return_value={'keep': False, 'installed': False}), \
             patch('tools.wechat_channels.page_controller.remove_owned_certificate',
                   side_effect=lambda: order.append('remove') or {'keep': False, 'installed': False}):
            api = self.make_api()
            api._page_controller = Mock()
            api._page_controller.stop.side_effect = lambda: order.append('stop')
            self.assertTrue(api.remove_page_certificate()['ok'])
            wait_until(lambda: not api._page_worker.is_alive())
            self.assertEqual(order, ['stop', 'remove'])
            self.assertEqual(api.get_state()['page_button']['state'], 'disabled')

    def test_certificate_removal_error_does_not_report_success(self):
        with patch('tools.wechat_channels.page_controller.certificate_status',
                   return_value={'keep': False, 'installed': True}), \
             patch('tools.wechat_channels.page_controller.recover_session'), \
             patch('tools.wechat_channels.page_controller.remove_owned_certificate', side_effect=OSError()):
            api = self.make_api()
            self.assertTrue(api.remove_page_certificate()['ok'])
            wait_until(lambda: not api._page_worker.is_alive())
            self.assertEqual(api.get_state()['page_button']['state'], 'error')
            self.assertTrue(api.get_state()['page_certificate']['installed'])

    def test_certificate_changes_rejected_during_page_transition(self):
        api = self.make_api()
        api._page_worker = Mock()
        api._page_worker.is_alive.return_value = True
        with patch('tools.wechat_channels.page_controller.set_certificate_preference') as choose:
            self.assertFalse(api.set_page_certificate_keep(True)['ok'])
            self.assertFalse(api.remove_page_certificate()['ok'])
            choose.assert_not_called()
        api._page_worker = None

    def test_page_events_keep_deleted_items_removed_until_explicit_download(self):
        api = self.make_api(scanner=lambda *_a, **_kw: [video()],
                            downloader=lambda candidate, folder, *_: folder / 'page-video.mp4')
        self.scan(api)
        api.remove_video('a')
        api._page_candidate(video())
        self.assertEqual(api.get_state()['items'], [])
        self.assertTrue(api._page_candidate(video(), download=True)['ok'])
        self.assertEqual(len(api.get_state()['items']), 1)

    def test_revision_poll_returns_only_version_until_something_changes(self):
        api = self.make_api(scanner=lambda *_a, **_kw: [video()])
        before = api.get_state()['revision']
        self.assertEqual(api.get_state(before), {'revision': before, 'unchanged': True})
        self.assertIn('items', api.get_state(str(before)))
        state = self.scan(api)
        self.assertIn('items', api.get_state(before))
        self.assertEqual(api.get_state(state['revision']), {'revision': state['revision'], 'unchanged': True})

    def test_engagement_whitelists_four_safe_numbers_and_isolates_snapshot(self):
        counts = {'recommend': 3.0, 'share': 0, 'like': 42, 'comment': 2**53 - 1,
                  'url': 'PRIVATE_COUNT_URL', 'decode_key': 'PRIVATE_COUNT_KEY'}
        api = self.make_api(scanner=lambda *_a, **_kw: [video(engagement=counts)])
        snapshot = self.scan(api)
        public = snapshot['items'][0]['engagement']
        self.assertEqual(public, {'recommend': 3, 'share': 0, 'like': 42, 'comment': 2**53 - 1})
        self.assertTrue(all(type(value) is int for value in public.values()))
        public['recommend'] = 999
        self.assertEqual(api.get_state()['items'][0]['engagement']['recommend'], 3)
        serialized = json.dumps(snapshot, allow_nan=False)
        for secret in ('PRIVATE_COUNT_URL', 'PRIVATE_COUNT_KEY', 'PRIVATE_URL', 'PRIVATE_KEY'):
            self.assertNotIn(secret, serialized)

    def test_safe_report_counts_only_visible_known_engagement_including_zero(self):
        complete = dict.fromkeys(('recommend', 'share', 'like', 'comment'), 0)
        rows = [video('complete', engagement=complete), video('partial', engagement={'share': 8}),
                video('unknown'), video('hidden', title='', decode_key=None, engagement=complete)]
        api = self.make_api(scanner=lambda *_a, **_kw: rows)
        self.scan(api)
        report = api._report()
        self.assertEqual((report['candidate_count'], report['ui_rows']), (4, 3))
        self.assertEqual((report['engagement_rows'], report['complete_engagement_rows']), (2, 1))
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_missing_and_malformed_engagement_remains_unknown_not_zero(self):
        fields = ('recommend', 'share', 'like', 'comment')
        invalid = [True, False, '12', -1, .5, float('nan'), float('inf'), float('-inf'),
                   2**53, {}, [], None]
        rows = [video(str(index), engagement=dict.fromkeys(fields, value))
                for index, value in enumerate(invalid)]
        rows.extend([video('absent'), video('bad-object', engagement=['5', 6]),
                     video('bad-string', engagement='{"recommend": 12}')])
        api = self.make_api(scanner=lambda *_a, **_kw: rows)
        snapshot = self.scan(api)
        self.assertEqual(len(snapshot['items']), len(rows))
        for item in snapshot['items']:
            self.assertEqual(item['engagement'], dict.fromkeys(fields))
        json.dumps(snapshot, allow_nan=False)

    def test_new_counts_merge_independently_of_retained_download_pair(self):
        original = video(engagement={'recommend': 10, 'share': 20, 'like': 30, 'comment': 40})
        rows = [original]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows))
        first = self.scan(api)
        rows[:] = [video(title='', url='https://finder.video.qq.com/unpaired', decode_key=None,
                         engagement={'recommend': 0, 'share': 'invalid', 'like': 25, 'comment': None})]
        second = self.scan(api)
        self.assertEqual(second['items'][0]['engagement'],
                         {'recommend': 0, 'share': 20, 'like': 25, 'comment': 40})
        self.assertGreater(second['library_revision'], first['library_revision'])
        self.assertEqual(api._candidates['a']['url'], original['url'])
        self.assertEqual(api._candidates['a']['decode_key'], original['decode_key'])
        self.assertEqual(api._candidates['a']['media_options'], original['media_options'])
        rows[:] = [video(title='', decode_key=None)]
        self.assertEqual(self.scan(api)['items'][0]['engagement'], second['items'][0]['engagement'])
        rows[:] = [video(engagement={'share': 0, 'comment': 12.0})]
        self.assertEqual(self.scan(api)['items'][0]['engagement'],
                         {'recommend': 0, 'share': 0, 'like': 25, 'comment': 12})

    def test_progressive_count_updates_survive_final_snapshot_missing_counts(self):
        calls = []
        def scanner(_cancel, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                return [video(engagement={'recommend': 7, 'share': 8})]
            kwargs['on_items']([video(engagement={'recommend': 0, 'comment': 11})])
            return [video()]
        api = self.make_api(scanner=scanner)
        self.scan(api)
        snapshot = self.scan(api)
        self.assertEqual(snapshot['items'][0]['engagement'],
                         {'recommend': 0, 'share': 8, 'like': None, 'comment': 11})

    def test_duplicate_same_feed_records_keep_independent_count_updates(self):
        rows = [video(engagement={'recommend': 7, 'share': 8})]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows))
        self.scan(api)
        rows[:] = [video(engagement={'recommend': 0}), video(engagement={'share': 3})]
        snapshot = self.scan(api)
        self.assertEqual(len(snapshot['items']), 1)
        self.assertEqual(snapshot['items'][0]['engagement'],
                         {'recommend': 0, 'share': 3, 'like': None, 'comment': None})

    def test_progressive_scan_retains_old_cards_even_after_final_snapshot(self):
        release, published = threading.Event(), threading.Event()
        calls = []
        def scanner(_cancel, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                return [video('old', duration=9000)]
            kwargs['on_items']([video('new', duration=10), video('next', duration=100)])
            published.set()
            release.wait(2)
            return [video('new', duration=10), video('next', duration=100)]
        api = self.make_api(scanner=scanner)
        self.scan(api)
        api.scan()
        self.assertTrue(published.wait(1))
        snapshot = api.get_state()
        self.assertEqual(snapshot['scan']['state'], 'scanning')
        self.assertEqual([item['id'] for item in snapshot['items']], ['new', 'next', 'old'])
        release.set()
        wait_until(lambda: api.get_state()['scan']['state'] == 'done')
        self.assertEqual([item['id'] for item in api.get_state()['items']], ['new', 'next', 'old'])

    def test_progressive_failure_keeps_published_and_old_cards(self):
        calls = []
        def scanner(_cancel, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                return [video('old')]
            kwargs['on_items']([video('new')])
            raise ScanError('读取中断')
        api = self.make_api(scanner=scanner)
        self.scan(api)
        snapshot = self.scan(api)
        self.assertEqual(snapshot['scan']['state'], 'error')
        self.assertEqual([item['id'] for item in snapshot['items']], ['new', 'old'])

    def test_raw_partial_refresh_retains_rich_card_and_whole_bound_pair(self):
        release, published = threading.Event(), threading.Event()
        original = video(thumbnail_url='https://finder.video.qq.com/old-cover')
        raw = video(title='', author='', duration=None, decode_key=None,
                    url='https://finder.video.qq.com/new?token=DIFFERENT_PAIR')
        calls = []
        def scanner(_cancel, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                return [original]
            kwargs['on_items']([raw])
            published.set()
            release.wait(2)
            return [dict(raw, title='最终标题', decode_key='FINAL_PAIRED_KEY')]
        api = self.make_api(scanner=scanner, thumbnail_loader=lambda _: Image.new('RGB', (10, 10)))
        self.scan(api)
        wait_until(lambda: api.get_state()['items'][0]['thumbnail_state'] == 'ready')
        initial = api.get_state()['items'][0]
        api.scan()
        self.assertTrue(published.wait(1))
        current = api.get_state()['items'][0]
        self.assertEqual(current, initial)
        self.assertEqual(api._candidates['a']['url'], original['url'])
        self.assertEqual(api._candidates['a']['decode_key'], original['decode_key'])
        release.set()
        wait_until(lambda: api.get_state()['scan']['state'] == 'done')
        self.assertEqual(api.get_state()['items'][0]['title'], '最终标题')
        self.assertEqual(api._candidates['a']['url'], raw['url'])
        self.assertEqual(api._candidates['a']['decode_key'], 'FINAL_PAIRED_KEY')

    def test_default_source_reads_both_and_diagnostic_can_disable_auto_refresh(self):
        sources = []
        def scanner(_cancel, **kwargs):
            sources.append(kwargs['source'])
            return []
        api = self.make_api(scanner=scanner, auto_refresh_enabled=False)
        self.assertEqual(api.get_state()['source'], 'all')
        self.assertFalse(api.get_state()['auto_refresh_enabled'])
        self.assertTrue(api.scan()['ok'])
        wait_until(lambda: api.get_state()['scan']['state'] == 'done')
        self.assertEqual(sources, ['all'])

    def test_source_change_failure_preserves_existing_library(self):
        def scanner(_cancel, **kwargs):
            if kwargs['source'] == 'main':
                raise ScanError('主界面未打开')
            return [video()]
        api = self.make_api(scanner=scanner)
        self.scan(api)
        self.assertFalse(api.scan('arbitrary')['ok'])
        state = self.scan(api, 'main')
        self.assertEqual(state['source'], 'main')
        self.assertEqual([item['id'] for item in state['items']], ['a'])
        self.assertEqual(state['scan']['state'], 'error')
        self.assertIn('a', api._candidates)

    def test_empty_scans_and_source_switches_never_remove_previous_videos(self):
        rows = [video('a')]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows))
        initial = self.scan(api)
        rows.clear()
        snapshot = self.scan(api)
        self.assertEqual(snapshot['items'], initial['items'])
        rows[:] = [video('b')]
        snapshot = self.scan(api, 'main')
        self.assertEqual([item['id'] for item in snapshot['items']], ['b', 'a'])
        rows[:] = [video('a')]
        snapshot = self.scan(api, 'standalone')
        self.assertEqual([item['id'] for item in snapshot['items']], ['b', 'a'])

    def test_final_sparse_scan_preserves_rich_metadata_and_exact_private_pair(self):
        original = video(thumbnail_url='https://finder.video.qq.com/cover')
        rows = [original]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows),
                            thumbnail_loader=lambda _: Image.new('RGB', (10, 10)))
        self.scan(api)
        wait_until(lambda: api.get_state()['items'][0]['thumbnail_state'] == 'ready')
        initial = api.get_state()['items'][0]
        rows[:] = [video(title='视频号视频', author='', duration=None, thumbnail_url='',
                         url='https://finder.video.qq.com/different', decode_key=None, media_options=[])]
        current = self.scan(api)['items'][0]
        self.assertEqual(current, initial)
        self.assertEqual(api._candidates['a'], original)

    def test_admission_limit_never_evicts_existing_and_still_updates_metadata(self):
        rows = [video('a'), video('b')]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows))
        with patch('tools.wechat_channels.web_app.MAX_ITEMS', 2):
            self.scan(api)
            rows[:] = [video('c'), video('a', title='更新的标题')]
            snapshot = self.scan(api)
        self.assertEqual([item['id'] for item in snapshot['items']], ['a', 'b'])
        self.assertEqual(snapshot['items'][0]['title'], '更新的标题')
        with patch('tools.wechat_channels.web_app.MAX_ITEMS', 1):
            rows[:] = [video('d')]
            snapshot = self.scan(api)
        self.assertEqual([item['id'] for item in snapshot['items']], ['a', 'b'])

    def test_remove_is_idempotent_and_late_scan_cannot_resurrect_deleted_id(self):
        published, release = threading.Event(), threading.Event()
        calls = []
        def scanner(_cancel, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                kwargs['on_items']([video('a'), video('b')])
                published.set()
                release.wait(2)
                kwargs['on_items']([video('a'), video('b')])
            return [video('a'), video('b')]
        api = self.make_api(scanner=scanner)
        first = self.scan(api)
        api.scan()
        self.assertTrue(published.wait(1))
        self.assertEqual(api.remove_video('a'), {'ok': True, 'removed': 1})
        self.assertGreater(api.get_state()['library_revision'], first['library_revision'])
        self.assertEqual(api.remove_video('a'), {'ok': True, 'removed': 0})
        self.assertFalse(api.remove_video({'id': 'a'})['ok'])
        release.set()
        wait_until(lambda: api.get_state()['scan']['state'] == 'done')
        self.assertEqual([item['id'] for item in api.get_state()['items']], ['b'])
        snapshot = self.scan(api)
        self.assertEqual([item['id'] for item in snapshot['items']], ['b'])

    def test_clear_invalidates_inflight_scan_and_only_unseen_ids_return(self):
        published, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def scanner(cancel, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                return [video('a')]
            if len(calls) == 2:
                kwargs['on_items']([video('a'), video('b')])
                published.set()
                release.wait(2)
                self.assertTrue(cancel.is_set())
                kwargs['on_items']([video('a'), video('b'), video('late')])
                finished.set()
                return [video('a'), video('b'), video('late')]
            return [video('a'), video('b'), video('new')]
        api = self.make_api(scanner=scanner)
        self.scan(api)
        api.scan()
        self.assertTrue(published.wait(1))
        self.assertEqual(api.clear_videos(), {'ok': True, 'removed': 2})
        revision = api.get_state()['library_revision']
        release.set()
        self.assertTrue(finished.wait(1))
        api._scan_worker.join(1)
        snapshot = api.get_state()
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(snapshot['library_revision'], revision)
        self.assertEqual(snapshot['scan']['state'], 'idle')
        snapshot = self.scan(api)
        self.assertEqual([item['id'] for item in snapshot['items']], ['new'])

    def test_remove_and_clear_preserve_completed_files_and_active_download(self):
        started, release = threading.Event(), threading.Event()
        target = self.folder / 'saved.mp4'
        events = []
        def downloader(_candidate, _folder, cancel, _progress):
            events.append(cancel)
            started.set()
            release.wait(2)
            target.write_bytes(b'complete')
            return target
        api = self.make_api(scanner=lambda *_a, **_kw: [video('a'), video('b')], downloader=downloader)
        self.scan(api)
        api.download('a')
        self.assertTrue(started.wait(1))
        self.assertEqual(api.remove_video('a')['removed'], 1)
        self.assertEqual(api.clear_videos()['removed'], 1)
        self.assertFalse(events[0].is_set())
        self.assertEqual(api.get_state()['download']['state'], 'downloading')
        release.set()
        wait_until(lambda: api.get_state()['download']['state'] == 'done')
        self.assertEqual(api.get_state()['items'], [])
        self.assertTrue(target.is_file())
        api.clear_videos()
        self.assertTrue(target.is_file())
        with patch('tools.wechat_channels.web_app.os.startfile') as open_file:
            self.assertTrue(api.open_video('a')['ok'])
            open_file.assert_called_once_with(target)

    def test_removal_and_clear_close_preview_without_deleting_saved_file(self):
        for action in ('remove', 'clear'):
            with self.subTest(action=action):
                target = self.folder / f'{action}.mp4'
                def downloader(*_args):
                    target.write_bytes(b'complete')
                    return target
                api = self.make_api(scanner=lambda *_a, **_kw: [video()], downloader=downloader)
                self.scan(api)
                api.download('a')
                wait_until(lambda: api.get_state()['download']['state'] == 'done')
                api.prepare_preview('a')
                wait_until(lambda: api.get_state()['preview']['state'] == 'ready')
                url = urlsplit(api.get_state()['preview']['url'])
                result = api.remove_video('a') if action == 'remove' else api.clear_videos()
                self.assertEqual(result, {'ok': True, 'removed': 1})
                self.assertEqual(api.get_state()['preview']['state'], 'idle')
                self.assertFalse(api.prepare_preview('a')['ok'])
                self.assertTrue(target.is_file())
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
                connection.request('GET', url.path)
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                connection.close()

    def test_clear_remembers_observed_ids_that_could_not_fit_admission_limit(self):
        rows = [video('a'), video('overflow')]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows))
        with patch('tools.wechat_channels.web_app.MAX_ITEMS', 1):
            self.scan(api)
            self.assertEqual(api.clear_videos(), {'ok': True, 'removed': 1})
            rows[:] = [video('overflow'), video('a'), video('unseen')]
            snapshot = self.scan(api)
        self.assertEqual([item['id'] for item in snapshot['items']], ['unseen'])

    def test_download_uses_private_exact_pair_and_remains_bound_during_refresh(self):
        started, release = threading.Event(), threading.Event()
        downloaded = self.folder / 'download.mp4'
        original = video()
        got = []
        def downloader(candidate, folder, cancel, progress):
            got.append(candidate)
            started.set()
            release.wait(2)
            progress(50, 100)
            downloaded.write_bytes(b'validated by mocked downloader')
            return downloaded
        rows = [original]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows), downloader=downloader)
        self.scan(api)
        self.assertFalse(api.download('https://evil.test/file')['ok'])
        self.assertTrue(api.download('a')['ok'])
        self.assertTrue(started.wait(1))
        self.assertFalse(api.download('a')['ok'])
        rows[:] = [video('b', title='另一个视频')]
        self.scan(api)
        self.assertEqual(api.get_state()['download']['id'], 'a')
        self.assertEqual(api.get_state()['download']['title'], original['title'])
        release.set()
        wait_until(lambda: api.get_state()['download']['state'] == 'done')
        self.assertEqual(got[0]['url'], original['url'])
        self.assertEqual(got[0]['decode_key'], original['decode_key'])
        self.assertEqual(got[0]['media_options'], original['media_options'])
        with patch('tools.wechat_channels.web_app.os.startfile') as startfile:
            self.assertFalse(api.open_video('b')['ok'])
            self.assertTrue(api.open_video('a')['ok'])
            startfile.assert_called_once_with(downloaded)

    def test_cancellation_and_error_reset_progress_and_allow_retry(self):
        started = threading.Event()
        attempts = []
        def downloader(_candidate, _folder, cancel, progress):
            attempts.append(True)
            progress(20, 100)
            started.set()
            if len(attempts) == 1:
                cancel.wait(2)
                raise Cancelled('已取消下载')
            raise DownloadError('无法下载')
        api = self.make_api(scanner=lambda *_a, **_kw: [video()], downloader=downloader)
        self.scan(api)
        self.assertTrue(api.download('a')['ok'])
        self.assertTrue(started.wait(1))
        self.assertTrue(api.cancel_download()['ok'])
        wait_until(lambda: api.get_state()['download']['state'] == 'cancelled')
        self.assertEqual(api.get_state()['download']['percent'], 0)
        self.assertTrue(api.download('a')['ok'])
        wait_until(lambda: api.get_state()['download']['state'] == 'error')
        self.assertEqual(api.get_state()['items'][0]['status'], 'error')
        self.assertEqual(api.get_state()['download']['percent'], 0)

    def test_thumbnail_becomes_inline_image_without_signed_url_or_title_change(self):
        release = threading.Event()
        def loader(url):
            self.assertEqual(url, 'https://finder.video.qq.com/cover?token=PRIVATE_COVER')
            release.wait(2)
            return Image.new('RGB', (32, 48), 'green')
        item = video(thumbnail_url='https://finder.video.qq.com/cover?token=PRIVATE_COVER')
        api = self.make_api(scanner=lambda *_a, **_kw: [item], thumbnail_loader=loader)
        state = self.scan(api)
        self.assertEqual(state['items'][0]['thumbnail_state'], 'loading')
        release.set()
        wait_until(lambda: api.get_state()['items'][0]['thumbnail_state'] == 'ready')
        state = api.get_state()
        self.assertTrue(state['items'][0]['thumbnail'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(state['items'][0]['title'], item['title'])
        self.assertNotIn('PRIVATE_COVER', json.dumps(state))

    def test_old_thumbnail_generation_cannot_modify_new_video(self):
        started, release = threading.Event(), threading.Event()
        rows = [video(thumbnail_url='https://finder.video.qq.com/old')]
        def loader(_url):
            started.set()
            release.wait(2)
            return Image.new('RGB', (10, 10))
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows), thumbnail_loader=loader)
        self.scan(api)
        self.assertTrue(started.wait(1))
        rows[:] = [video('b', title='新版标题')]
        self.scan(api, 'main')
        release.set()
        wait_until(lambda: not api._thumb_pending)
        item = api.get_state()['items'][0]
        self.assertEqual((item['id'], item['title'], item['thumbnail']), ('b', '新版标题', None))

    def test_failed_same_source_refresh_does_not_leave_abandoned_cover_loading(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def scanner(*_args, **_kwargs):
            calls.append(True)
            if len(calls) > 1:
                raise ScanError('本次读取失败')
            return [video(thumbnail_url='https://finder.video.qq.com/slow-cover')]
        def loader(_url):
            started.set()
            release.wait(2)
            return Image.new('RGB', (10, 10))
        api = self.make_api(scanner=scanner, thumbnail_loader=loader)
        state = self.scan(api)
        self.assertTrue(started.wait(1))
        self.assertEqual(state['items'][0]['thumbnail_state'], 'loading')
        state = self.scan(api)
        self.assertEqual(state['scan']['state'], 'error')
        self.assertEqual(state['items'][0]['id'], 'a')
        self.assertEqual(state['items'][0]['thumbnail_state'], 'unavailable')
        self.assertEqual(api._thumb_pending, set())
        self.assertEqual(api._thumb_requested, set())
        release.set()

    def test_native_folder_dialog_is_only_way_to_change_destination(self):
        api = self.make_api()
        chosen = self.folder / 'selected'
        fake_window = Mock()
        fake_window.create_file_dialog.return_value = (str(chosen),)
        api._window = fake_window
        fake_webview = types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER='native-folder'))
        with patch.dict('sys.modules', {'webview': fake_webview}):
            self.assertTrue(api.choose_folder()['ok'])
        self.assertEqual(api.get_state()['folder'], str(chosen.resolve()))
        with patch('tools.wechat_channels.web_app.os.startfile') as open_file:
            self.assertTrue(api.open_folder()['ok'])
            open_file.assert_called_once_with(chosen.resolve())

    def test_close_cancels_worker_and_waits_for_cleanup(self):
        started, cleaned = threading.Event(), threading.Event()
        def downloader(_item, _folder, cancel, _progress):
            started.set()
            cancel.wait(2)
            cleaned.set()
            raise Cancelled('已取消')
        api = self.make_api(scanner=lambda *_a, **_kw: [video()], downloader=downloader)
        api._window = Mock()
        self.scan(api)
        api.download('a')
        self.assertTrue(started.wait(1))
        self.assertFalse(api._begin_close())
        wait_until(lambda: api._window.destroy.called)
        self.assertTrue(cleaned.is_set())
        self.assertTrue(api._begin_close())
        self.assertFalse(api.scan()['ok'])

    def test_duplicate_scan_is_rejected_without_starting_another_worker(self):
        release = threading.Event()
        calls = []
        def scanner(*_args, **_kwargs):
            calls.append(True)
            release.wait(2)
            return []
        api = self.make_api(scanner=scanner)
        api.scan()
        self.assertFalse(api.scan()['ok'])
        release.set()
        wait_until(lambda: api.get_state()['scan']['state'] == 'done')
        self.assertEqual(len(calls), 1)

    def test_frozen_asset_path_matches_packaging_destination(self):
        with patch('tools.wechat_channels.web_app.sys.frozen', True, create=True), \
             patch('tools.wechat_channels.web_app.sys._MEIPASS', str(self.folder), create=True):
            self.assertEqual(_assets_path(), self.folder / 'web_assets' / 'index.html')

    def test_preview_download_preserves_exact_pair_and_exposes_only_local_token(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        def downloader(candidate, folder, cancel, progress):
            seen.append(candidate)
            started.set()
            progress(10, 20)
            release.wait(2)
            target = folder / 'prepared.mp4'
            target.write_bytes(b'0123456789')
            return target
        original = video()
        rows = [original]
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows), downloader=downloader)
        self.scan(api)
        self.assertFalse(api.prepare_preview('https://arbitrary.test')['ok'])
        self.assertFalse(api.prepare_preview({'path': 'private'})['ok'])
        self.assertTrue(api.prepare_preview('a')['ok'])
        self.assertTrue(started.wait(1))
        wait_until(lambda: api.get_state()['preview']['percent'] == 50)
        preview = api.get_state()['preview']
        self.assertEqual((preview['id'], preview['state'], preview['url']), ('a', 'preparing', None))
        rows[:] = [video('b')]
        self.scan(api)
        self.assertEqual(api.get_state()['preview']['title'], original['title'])
        release.set()
        wait_until(lambda: api.get_state()['preview']['state'] == 'ready')
        snapshot = api.get_state()
        preview = snapshot['preview']
        self.assertEqual(seen, [original])
        self.assertTrue(preview['url'].startswith('http://127.0.0.1:'))
        self.assertNotIn('prepared.mp4', preview['url'])
        self.assertEqual(snapshot['download']['state'], 'idle')
        for secret in ('PRIVATE_URL', 'PRIVATE_KEY', 'PRIVATE_PAIR', 'OTHER_KEY', 'media_options'):
            self.assertNotIn(secret, json.dumps(snapshot))
        url = urlsplit(preview['url'])
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
        connection.request('GET', url.path, headers={'Range': 'bytes=2-5'})
        response = connection.getresponse()
        self.assertEqual((response.status, response.read()), (206, b'2345'))
        connection.close()
        api.close_preview()
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=2)
        connection.request('GET', url.path)
        response = connection.getresponse()
        self.assertEqual(response.status, 404)
        response.read()
        connection.close()
        self.assertTrue(api.prepare_preview('a')['ok'])  # Cached replay after newer scans.
        wait_until(lambda: api.get_state()['preview']['state'] == 'ready')
        self.assertEqual(len(seen), 1)
        cache_root = Path(api._preview_temp.name)
        api._shutdown()
        self.assertFalse(cache_root.exists())

    def test_preview_reuses_saved_file_when_latest_scan_is_empty(self):
        target = self.folder / 'downloaded.mp4'
        original = video(title='保留的标题')
        rows = [original]
        def downloader(*_a):
            target.write_bytes(b'completed')
            return target
        api = self.make_api(scanner=lambda *_a, **_kw: list(rows), downloader=downloader)
        self.scan(api)
        api.download('a')
        wait_until(lambda: api.get_state()['download']['state'] == 'done')
        rows.clear()
        self.scan(api)
        api._downloader = Mock(side_effect=AssertionError('Must not redownload'))
        self.assertTrue(api.prepare_preview('a')['ok'])
        wait_until(lambda: api.get_state()['preview']['state'] == 'ready')
        self.assertEqual(api.get_state()['preview']['title'], '保留的标题')
        api._shutdown()
        self.assertTrue(target.is_file())

    def test_preview_reselection_serializes_transfers_and_close_cleans_partial(self):
        started = threading.Event()
        calls = []
        active = []
        maximum = []
        def downloader(candidate, folder, cancel, progress):
            calls.append(candidate['id'])
            active.append(candidate['id'])
            maximum.append(len(active))
            partial = folder / (candidate['id'] + '.mp4.part')
            partial.write_bytes(b'partial')
            started.set()
            try:
                cancel.wait(2)
                raise Cancelled('已取消')
            finally:
                active.remove(candidate['id'])
                partial.unlink()
        api = self.make_api(scanner=lambda *_a, **_kw: [video('a'), video('b')], downloader=downloader)
        self.scan(api)
        api.prepare_preview('a')
        self.assertTrue(started.wait(1))
        api.prepare_preview('b')
        wait_until(lambda: 'b' in calls)
        self.assertEqual(api.get_state()['preview']['id'], 'b')
        api.close_preview()
        wait_until(lambda: not active)
        self.assertEqual(api.get_state()['preview']['state'], 'idle')
        self.assertEqual(max(maximum), 1)
        self.assertEqual(list(Path(api._preview_temp.name).glob('*.part')), [])

    def test_preview_error_is_safe_and_retryable(self):
        downloader = Mock(side_effect=RuntimeError('PRIVATE_URL PRIVATE_KEY'))
        api = self.make_api(scanner=lambda *_a, **_kw: [video()], downloader=downloader)
        self.scan(api)
        api.prepare_preview('a')
        wait_until(lambda: api.get_state()['preview']['state'] == 'error')
        self.assertNotIn('PRIVATE', api.get_state()['preview']['message'])
        self.assertEqual(api.get_state()['preview']['percent'], 0)
        self.assertTrue(api.prepare_preview('a')['ok'])
        wait_until(lambda: downloader.call_count == 2)


class PlaybackServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'secret-local-name.mp4'
        self.path.write_bytes(b'0123456789')
        self.server = PlaybackServer()
        self.url = urlsplit(self.server.register(self.path))

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def request(self, method='GET', path=None, headers=None):
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=2)
        connection.request(method, path or self.url.path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def test_full_head_and_seek_ranges(self):
        status, headers, body = self.request()
        self.assertEqual((status, body), (200, b'0123456789'))
        self.assertEqual(headers['Content-Type'], 'video/mp4')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(self.request('HEAD')[2], b'')
        for value, expected, content_range in (
                ('bytes=2-4', b'234', 'bytes 2-4/10'),
                ('bytes=7-', b'789', 'bytes 7-9/10'),
                ('bytes=-3', b'789', 'bytes 7-9/10'),
                ('bytes=8-100', b'89', 'bytes 8-9/10')):
            with self.subTest(value=value):
                status, headers, body = self.request(headers={'Range': value})
                self.assertEqual((status, body), (206, expected))
                self.assertEqual(headers['Content-Range'], content_range)

    def test_invalid_ranges_and_unknown_paths_do_not_expose_files(self):
        for value in ('bytes=10-', 'bytes=-0', 'bytes=4-2', 'bytes=0-1,3-4', 'bogus'):
            self.assertEqual(self.request(headers={'Range': value})[0], 416)
        for path in ('/', '/secret-local-name.mp4', '/../secret-local-name.mp4', self.url.path + '?x=1'):
            self.assertEqual(self.request(path=path)[0], 404)
        self.assertEqual(self.request(headers={'Host': 'untrusted.example'})[0], 403)

    def test_revoke_and_new_registration_invalidate_previous_capability(self):
        old = self.url.path
        self.url = urlsplit(self.server.register(self.path))
        self.assertNotEqual(old, self.url.path)
        self.assertEqual(self.request(path=old)[0], 404)
        self.assertEqual(self.request()[0], 200)
        self.server.revoke_all()
        self.assertEqual(self.request()[0], 404)

    def test_revocation_interrupts_paused_reader_and_releases_file(self):
        self.path.write_bytes(b'x' * (8 * 1024 * 1024))
        entry = self.server._videos[self.url.path]
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=2)
        connection.request('GET', self.url.path)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        wait_until(lambda: bool(entry.clients))
        # Leave the body unread, like a paused video element.
        self.server.revoke_all()
        wait_until(lambda: not entry.clients)
        self.path.unlink()
        self.assertFalse(self.path.exists())
        connection.close()


if __name__ == '__main__':
    unittest.main()
