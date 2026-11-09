import importlib.util
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wechat_channels.page_controller import restore_proxy
from wechat_channels import page_controller
SYSTEM_CERTUTIL = str(Path('C:/Windows/System32/certutil.exe'))


class FakeSettings:
    def __init__(self, values):
        self.values = dict(values)
        self.notifications = 0
    def read(self, name):
        return self.values.get(name, {'exists': False})
    def write(self, name, value):
        self.values[name] = value
    def notify(self):
        self.notifications += 1


class ProxyRecoveryTests(unittest.TestCase):
    def test_failed_certificate_cleanup_still_stops_helper_and_bridge(self):
        controller = page_controller.PageController(on_candidate=Mock(), get_status=Mock(),
                                                    on_event=Mock(), on_state=Mock())
        controller._token = 'a' * 64
        helper = controller._process = Mock()
        bridge = controller._bridge = Mock()
        with patch.object(page_controller, 'recover_session', side_effect=OSError('failure')):
            with self.assertRaises(page_controller.PageSetupError):
                controller.stop()
        helper.wait.assert_called_once()
        bridge.close.assert_called_once()
        self.assertIsNone(controller._process)
        self.assertIsNone(controller._bridge)

    def test_exact_original_presence_and_types_are_restored(self):
        before = {'ProxyEnable': {'exists': False}, 'AutoConfigURL': {'exists': False}}
        applied = {'ProxyEnable': {'exists': True, 'kind': 4, 'value': 0},
                   'AutoConfigURL': {'exists': True, 'kind': 1, 'value': 'our-pac'}}
        store = FakeSettings(applied)
        restore_proxy(store, before, applied)
        self.assertEqual(store.values, before)
        self.assertEqual(store.notifications, 1)
        restore_proxy(store, before, applied)
        self.assertEqual(store.notifications, 1)

    def test_new_proxy_chosen_by_user_is_preserved(self):
        before = {'ProxyEnable': {'exists': True, 'kind': 4, 'value': 0},
                  'AutoConfigURL': {'exists': False}}
        applied = {'ProxyEnable': before['ProxyEnable'],
                   'AutoConfigURL': {'exists': True, 'kind': 1, 'value': 'our-pac'}}
        user_choice = {'exists': True, 'kind': 1, 'value': 'new-user-pac'}
        store = FakeSettings(dict(applied, AutoConfigURL=user_choice))
        restore_proxy(store, before, applied)
        self.assertEqual(store.values['AutoConfigURL'], user_choice)


class CertificatePreferenceTests(unittest.TestCase):
    thumb = 'A' * 40
    token = 'b' * 64

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(page_controller, 'BASE', self.base))
        self.stack.enter_context(patch.object(page_controller, '_thumbprint', return_value=self.thumb))
        (self.base / 'certificate').mkdir()
        (self.base / 'certificate' / 'mitmproxy-ca-cert.pem').write_text('mock certificate')
        self.trusted = True
        self.machine_trusted = False
        self.stack.enter_context(patch.object(page_controller, '_trusted', side_effect=
            lambda thumb, scope='user': self.trusted if scope == 'user' else self.machine_trusted))
        self.admin = self.stack.enter_context(patch.object(page_controller, '_is_admin', return_value=True))
        self.stack.enter_context(patch.object(page_controller, '_certutil_path', return_value=SYSTEM_CERTUTIL))
        self.run = self.stack.enter_context(patch.object(page_controller.subprocess, 'run', side_effect=self.remove))
        self.before = {'ProxyEnable': {'exists': False}, 'AutoConfigURL': {'exists': False}}
        self.applied = {'ProxyEnable': {'exists': True, 'kind': 4, 'value': 0},
                        'AutoConfigURL': {'exists': True, 'kind': 1,
                                          'value': f'http://127.0.0.1:7777/__clipgo/{self.token}/proxy.pac'}}
        self.settings = FakeSettings(self.applied)
        self.stack.enter_context(patch.object(page_controller, 'WindowsProxySettings', return_value=self.settings))

    def remove(self, command, **kwargs):
        scope = 'user' if '-user' in command else 'machine'
        self.assertEqual(command, [SYSTEM_CERTUTIL] + (['-user'] if scope == 'user' else [])
                         + ['-delstore', 'Root', self.thumb])
        if scope == 'user':
            self.trusted = False
        else:
            self.machine_trusted = False
        return Mock(returncode=0)

    def journal(self, *, trusted_before=False, scope=None):
        record = {
            'version': 1, 'token': self.token, 'before': self.before, 'applied': self.applied,
            'thumbprint': self.thumb, 'trusted_before': trusted_before}
        if scope is not None:
            record.update(version=2, scope=scope)
        page_controller._atomic_json(self.base / 'session.json', record)

    def test_default_and_malformed_preference_do_not_opt_in(self):
        self.assertFalse(page_controller.get_certificate_preference())
        (self.base / 'preferences.json').write_text('{bad json')
        self.assertFalse(page_controller.get_certificate_preference())
        page_controller._atomic_json(self.base / 'preferences.json', {'version': 1, 'keep_certificate': 'true'})
        self.assertFalse(page_controller.get_certificate_preference())
        with self.assertRaises(ValueError):
            page_controller.set_certificate_preference(1)
        self.run.assert_not_called()

    def test_preference_only_saves_choice_and_status_is_boolean(self):
        page_controller.set_certificate_preference(True)
        self.assertEqual(page_controller.certificate_status(),
                         {'keep': True, 'installed': True, 'scope': 'user', 'installed_scope': 'user'})
        self.run.assert_not_called()
        self.assertEqual(self.settings.notifications, 0)

    def test_opt_in_preserves_ca_but_always_restores_proxy(self):
        self.journal()
        page_controller.set_certificate_preference(True)
        page_controller.recover_session(expected_token=self.token)
        self.assertEqual(self.settings.values, self.before)
        self.assertTrue(self.trusted)
        self.run.assert_not_called()
        self.assertFalse((self.base / 'session.json').exists())
        self.assertEqual(page_controller._owned_certificate_thumbprint(), self.thumb)

    def test_default_recovery_removes_only_new_session_certificate(self):
        self.journal()
        page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.run.assert_called_once()
        self.assertFalse((self.base / 'owned-certificate.json').exists())

    def test_opt_out_removes_previous_persistent_ca_even_when_pretrusted(self):
        page_controller._record_owned_certificate(self.thumb)
        page_controller.set_certificate_preference(False)
        self.journal(trusted_before=True)
        page_controller.recover_session()
        self.run.assert_called_once()
        self.assertFalse(self.trusted)
        self.assertEqual(self.settings.values, self.before)

    def test_opt_out_cleans_persistent_ownership_without_active_journal(self):
        page_controller._record_owned_certificate(self.thumb)
        page_controller.recover_session()
        self.run.assert_called_once()
        self.assertFalse(self.trusted)
        self.assertEqual(self.settings.notifications, 0)

    def test_unknown_preexisting_trust_is_never_claimed_or_removed(self):
        self.journal(trusted_before=True)
        page_controller.recover_session()
        self.assertIsNone(page_controller._owned_certificate_thumbprint())
        page_controller.remove_owned_certificate()
        self.run.assert_not_called()
        self.assertTrue(self.trusted)

    def test_explicit_remove_disables_keep_and_removes_exact_owned_thumb(self):
        page_controller._record_owned_certificate(self.thumb)
        page_controller.set_certificate_preference(True)
        self.assertEqual(page_controller.remove_owned_certificate(),
                         {'keep': False, 'installed': False, 'scope': 'user', 'installed_scope': None})
        self.run.assert_called_once()

    def test_mismatched_marker_does_not_delete_any_certificate(self):
        page_controller._atomic_json(self.base / 'owned-certificate.json',
                                     {'version': 1, 'thumbprint': 'C' * 40})
        self.journal(trusted_before=True)
        with self.assertRaises(page_controller.PageSetupError):
            page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.run.assert_not_called()
        self.assertTrue((self.base / 'session.json').exists())

    def test_failed_deletion_preserves_ownership_and_recovery_record(self):
        self.journal()
        self.run.side_effect = None
        with self.assertRaises(page_controller.PageSetupError):
            page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.assertTrue((self.base / 'session.json').exists())
        self.assertEqual(page_controller._owned_certificate_thumbprint(), self.thumb)

    def test_scope_preference_defaults_to_user_and_preserves_keep(self):
        self.assertEqual(page_controller.get_certificate_scope(), 'user')
        page_controller._atomic_json(self.base / 'preferences.json', {'version': 1, 'keep_certificate': True})
        self.assertEqual(page_controller.get_certificate_scope(), 'user')
        page_controller.set_certificate_scope('machine')
        self.assertTrue(page_controller.get_certificate_preference())
        page_controller.set_certificate_preference(False)
        self.assertEqual(page_controller.get_certificate_scope(), 'machine')
        self.run.assert_not_called()
        with self.assertRaises(page_controller.PageSetupError):
            page_controller.set_certificate_scope('all')

    def test_machine_cleanup_uses_recorded_scope_despite_user_preference(self):
        self.machine_trusted = True
        self.journal(scope='machine')
        page_controller.recover_session()
        self.run.assert_called_once()
        self.assertNotIn('-user', self.run.call_args.args[0])
        self.assertTrue(self.trusted)
        self.assertFalse(self.machine_trusted)
        self.assertEqual(self.settings.values, self.before)

    def test_legacy_user_cleanup_never_deletes_same_certificate_from_machine(self):
        self.machine_trusted = True
        page_controller.set_certificate_scope('machine')
        page_controller._atomic_json(self.base / 'owned-certificate.json',
                                     {'version': 1, 'thumbprint': self.thumb})
        self.journal(trusted_before=True)
        page_controller.recover_session()
        self.assertIn('-user', self.run.call_args.args[0])
        self.assertFalse(self.trusted)
        self.assertTrue(self.machine_trusted)

    def test_scope_conflict_restores_network_but_never_deletes_certificate(self):
        self.journal(scope='machine')
        page_controller._record_owned_certificate(self.thumb, 'user')
        with self.assertRaises(page_controller.PageSetupError):
            page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.run.assert_not_called()
        self.assertTrue((self.base / 'session.json').exists())

    def test_invalid_session_scope_restores_network_without_certificate_command(self):
        self.journal(scope='all')
        with self.assertRaises(page_controller.PageSetupError):
            page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.run.assert_not_called()

    def test_new_owned_record_requires_valid_scope(self):
        for record in ({'version': 2, 'thumbprint': self.thumb},
                       {'version': 2, 'thumbprint': self.thumb, 'scope': 'all'}):
            page_controller._atomic_json(self.base / 'owned-certificate.json', record)
            with self.assertRaises(page_controller.PageSetupError):
                page_controller.remove_owned_certificate()
        self.run.assert_not_called()

    def test_unprivileged_machine_cleanup_restores_proxy_and_keeps_journal(self):
        self.machine_trusted = True
        self.admin.return_value = False
        self.journal(scope='machine')
        with self.assertRaisesRegex(page_controller.PageSetupError, '管理员'):
            page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.run.assert_not_called()
        self.assertEqual(page_controller._owned_certificate()['scope'], 'machine')
        self.assertTrue((self.base / 'session.json').exists())

    def test_admin_requirement_accounts_for_preference_and_recorded_recovery_scope(self):
        self.assertFalse(page_controller.certificate_requires_admin())
        self.journal()
        self.assertFalse(page_controller.certificate_requires_admin())
        self.journal(scope='machine')
        self.assertTrue(page_controller.certificate_requires_admin())
        (self.base / 'session.json').unlink()
        page_controller._record_owned_certificate(self.thumb, 'machine')
        self.assertTrue(page_controller.certificate_requires_admin())
        (self.base / 'owned-certificate.json').unlink()
        page_controller.set_certificate_scope('machine')
        self.assertTrue(page_controller.certificate_requires_admin())

    def test_status_reports_recorded_installed_scope_after_preference_changes(self):
        self.machine_trusted = True
        self.trusted = False
        self.journal(scope='machine')
        status = page_controller.certificate_status()
        self.assertEqual(status, {'keep': False, 'installed': True,
                                  'scope': 'user', 'installed_scope': 'machine'})

    def test_invalid_preference_scope_defaults_to_temporary_user_certificate(self):
        page_controller._atomic_json(self.base / 'preferences.json',
                                     {'version': 2, 'scope': 'invalid', 'keep_certificate': True})
        self.assertEqual(page_controller.get_certificate_scope(), 'user')
        self.assertFalse(page_controller.get_certificate_preference())
        self.assertFalse(page_controller.certificate_requires_admin())
        self.journal()
        page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.assertFalse(self.trusted)

    def test_bad_ownership_is_safe_for_status_but_recovery_still_restores_pac(self):
        page_controller._atomic_json(self.base / 'owned-certificate.json',
                                     {'version': 2, 'thumbprint': 'invalid', 'scope': 'machine'})
        self.assertTrue(page_controller.certificate_requires_admin())
        self.assertEqual(page_controller.certificate_status(),
                         {'keep': False, 'installed': False, 'scope': 'user', 'installed_scope': None})
        self.journal(scope='machine')
        with self.assertRaises(page_controller.PageSetupError):
            page_controller.recover_session()
        self.assertEqual(self.settings.values, self.before)
        self.run.assert_not_called()

    def test_startup_hints_tolerate_damaged_records_without_deleting_them(self):
        page_controller._atomic_json(self.base / 'owned-certificate.json', ['invalid'])
        (self.base / 'session.json').write_text('{bad json')
        self.assertFalse(page_controller.certificate_requires_admin())
        self.assertFalse(page_controller.certificate_status()['installed'])
        self.assertTrue((self.base / 'owned-certificate.json').exists())
        self.assertTrue((self.base / 'session.json').exists())
        self.run.assert_not_called()

    def test_machine_recovery_hint_does_not_depend_on_certificate_file_integrity(self):
        self.journal(scope='machine')
        (self.base / 'certificate' / 'mitmproxy-ca-cert.pem').unlink()
        self.assertTrue(page_controller.certificate_requires_admin())
        self.assertFalse(page_controller.certificate_status()['installed'])


class CertificateInstallTests(unittest.TestCase):
    def setUp(self):
        self.controller = page_controller.PageController(on_candidate=Mock(), get_status=Mock(),
                                                         on_event=Mock(), on_state=Mock())
        self.child = Mock()
        self.child.poll.return_value = None
        self.child.wait.return_value = 0
        self.popen = patch.object(page_controller.subprocess, 'Popen', return_value=self.child)
        self.launch = self.popen.start()
        self.addCleanup(self.popen.stop)
        self.trust = patch.object(page_controller, '_trusted', return_value=True)
        self.trusted_call = self.trust.start()
        self.addCleanup(self.trust.stop)
        system_path = patch.object(page_controller, '_certutil_path', return_value=SYSTEM_CERTUTIL)
        system_path.start()
        self.addCleanup(system_path.stop)

    def test_already_cancelled_never_launches_confirmation(self):
        cancelled = Mock(side_effect=page_controller.PageSetupCancelled('cancelled'))
        with self.assertRaises(page_controller.PageSetupCancelled):
            self.controller._install_certificate(Path('mock.pem'), 'A' * 40, cancelled)
        self.launch.assert_not_called()

    def test_cancel_during_confirmation_reaps_before_cleanup(self):
        cancelled = Mock(side_effect=[None, page_controller.PageSetupCancelled('cancelled')])
        with self.assertRaises(page_controller.PageSetupCancelled):
            self.controller._install_certificate(Path('mock.pem'), 'A' * 40, cancelled)
        self.child.terminate.assert_called_once()
        self.child.wait.assert_called_once_with(timeout=2)
        self.assertIsNone(self.controller._certificate_process)

    def test_confirmation_timeout_terminates_and_reaps_process(self):
        with patch.object(page_controller.time, 'monotonic', side_effect=[0, 120]):
            with self.assertRaises(page_controller.subprocess.TimeoutExpired):
                self.controller._install_certificate(Path('mock.pem'), 'A' * 40, Mock())
        self.child.terminate.assert_called_once()
        self.child.wait.assert_called_once_with(timeout=2)

    def test_waiting_checks_stop_every_tenth_second(self):
        cancelled = Mock(side_effect=[None, None, page_controller.PageSetupCancelled('cancelled')])
        with patch.object(self.controller._stop, 'wait') as wait:
            with self.assertRaises(page_controller.PageSetupCancelled):
                self.controller._install_certificate(Path('mock.pem'), 'A' * 40, cancelled)
        wait.assert_called_once_with(.1)
        self.child.terminate.assert_called_once()

    def test_termination_timeout_forces_kill_before_return(self):
        self.child.wait.side_effect = [page_controller.subprocess.TimeoutExpired('certutil', 2), 0]
        cancelled = Mock(side_effect=[None, page_controller.PageSetupCancelled('cancelled')])
        with self.assertRaises(page_controller.PageSetupCancelled):
            self.controller._install_certificate(Path('mock.pem'), 'A' * 40, cancelled)
        self.child.kill.assert_called_once()
        self.assertEqual(self.child.wait.call_count, 2)
        self.assertIsNone(self.controller._certificate_process)

    def test_success_uses_original_confirmation_and_does_not_terminate(self):
        self.child.poll.return_value = 0
        self.controller._install_certificate(Path('mock.pem'), 'A' * 40, Mock())
        self.assertEqual(self.launch.call_args.args[0],
                         [SYSTEM_CERTUTIL, '-user', '-addstore', 'Root', 'mock.pem'])
        self.child.terminate.assert_not_called()
        self.child.wait.assert_called_once_with(timeout=2)

    def test_cannot_reap_installer_keeps_journal_for_later_recovery(self):
        self.controller._certificate_process = self.child
        self.controller._token = 'b' * 64
        self.child.terminate.side_effect = OSError('cannot terminate')
        self.child.kill.side_effect = OSError('cannot kill')
        bridge = self.controller._bridge = Mock()
        with patch.object(page_controller, 'recover_session') as recover:
            with self.assertRaises(page_controller.PageSetupError):
                self.controller.stop()
        recover.assert_not_called()
        self.assertIs(self.controller._certificate_process, self.child)
        bridge.close.assert_called_once()

    def test_machine_install_requires_admin_before_launching_certutil(self):
        with patch.object(page_controller, '_is_admin', return_value=False):
            with self.assertRaisesRegex(page_controller.PageSetupError, '管理员'):
                self.controller._install_certificate(Path('mock.pem'), 'A' * 40, Mock(), scope='machine')
        self.launch.assert_not_called()

    def test_machine_install_targets_only_machine_store(self):
        self.child.poll.return_value = 0
        with patch.object(page_controller, '_is_admin', return_value=True):
            self.controller._install_certificate(Path('mock.pem'), 'A' * 40, Mock(), scope='machine')
        self.assertEqual(self.launch.call_args.args[0],
                         [SYSTEM_CERTUTIL, '-addstore', 'Root', 'mock.pem'])
        self.trusted_call.assert_called_once_with('A' * 40, 'machine')


class CertificateStoreQueryTests(unittest.TestCase):
    def test_certutil_uses_system_directory_api_instead_of_environment_search(self):
        kernel = Mock()
        directory = str(Path('C:/Windows/System32'))
        def get_directory(buffer, capacity):
            buffer.value = directory
            return len(directory)
        kernel.GetSystemDirectoryW.side_effect = get_directory
        with patch.object(page_controller.ctypes, 'WinDLL', return_value=kernel, create=True), \
             patch.dict(page_controller.os.environ, {'SystemRoot': 'C:/untrusted', 'PATH': 'C:/untrusted'}):
            self.assertEqual(page_controller._certutil_path(), SYSTEM_CERTUTIL)
        kernel.GetSystemDirectoryW.assert_called_once()

    def test_reads_exact_user_and_machine_registry_stores_separately(self):
        opened = Mock()
        opened.return_value.__enter__ = Mock()
        opened.return_value.__exit__ = Mock()
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2,
                                   KEY_READ=4, KEY_WOW64_64KEY=8, OpenKey=opened)
        with patch.dict(sys.modules, {'winreg': registry}):
            self.assertTrue(page_controller._trusted('A' * 40, 'user'))
            self.assertTrue(page_controller._trusted('A' * 40, 'machine'))
            calls = opened.call_args_list
            self.assertEqual([call.args[0] for call in calls], [1, 2])
            self.assertTrue(all(call.args[1].endswith('Root\\Certificates\\' + 'A' * 40) for call in calls))
            opened.side_effect = FileNotFoundError
            self.assertFalse(page_controller._trusted('A' * 40, 'user'))
