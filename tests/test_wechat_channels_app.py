"""Certificate lifecycle and capture scope checks; never change certificate stores."""
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.wechat_channels import app, capture


class AppLifecycleTests(unittest.TestCase):
    thumb = 'A' * 40

    def test_capture_is_process_and_host_scoped(self):
        command = app.capture_command()
        self.assertEqual(command[command.index('--mode') + 1], capture.LOCAL_CAPTURE_MODE)
        self.assertEqual(command[command.index('--allow-hosts') + 1], capture.ALLOW_HOSTS_PATTERN)
        self.assertIn('block_global=true', command)
        self.assertIn('flow_detail=0', command)
        self.assertIn(f'confdir={app.CONF}', command)
        self.assertTrue(command[-1].endswith('capture.py'))
        self.assertNotIn('--set', command[:2])

    def test_frozen_addon_matches_bundled_file(self):
        with patch.object(app.sys, 'frozen', True, create=True), patch.object(app.sys, '_MEIPASS', 'bundle', create=True):
            command = app.capture_command()
        self.assertEqual(command[:2], [app.sys.executable, '--capture'])
        self.assertEqual(Path(command[-1]), Path('bundle') / 'capture.py')

    def test_trust_records_recovery_before_store_change(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'recovery.json'
            cert_store = Mock()
            def install(args, **kwargs):
                self.assertEqual(json.loads(marker.read_text())['thumbprint'], self.thumb)
                self.assertEqual(args[:4], ['certutil.exe', '-user', '-addstore', 'Root'])
                return types.SimpleNamespace(returncode=0)
            with patch.object(app, 'MARKER', marker), patch.object(app, 'CONF', Path(folder) / 'cert'), \
                 patch.object(app, 'certificate_thumbprint', return_value=self.thumb), \
                 patch.object(app, 'certificate_is_trusted', return_value=True), \
                 patch.object(app.subprocess, 'run', side_effect=install), \
                 patch.dict('sys.modules', {'mitmproxy.certs': types.SimpleNamespace(CertStore=cert_store)}):
                app.trust_certificate()
                cert_store.from_store.assert_called_once()
                self.assertTrue(marker.exists())

    def test_untrust_preserves_marker_until_absence_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'recovery.json'
            marker.write_text(json.dumps({'thumbprint': self.thumb}))
            with patch.object(app, 'MARKER', marker), patch.object(app, 'certificate_thumbprint', return_value=self.thumb), \
                 patch.object(app.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)), \
                 patch.object(app, 'certificate_is_trusted', return_value=True):
                with self.assertRaises(RuntimeError):
                    app.untrust_certificate()
                self.assertTrue(marker.exists())
            with patch.object(app, 'MARKER', marker), patch.object(app, 'certificate_thumbprint', return_value=self.thumb), \
                 patch.object(app.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)), \
                 patch.object(app, 'certificate_is_trusted', return_value=False):
                app.untrust_certificate()
                self.assertFalse(marker.exists())
                app.untrust_certificate()  # Idempotent recovery.

    def test_untrust_refuses_other_certificate(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'recovery.json'
            marker.write_text(json.dumps({'thumbprint': self.thumb}))
            with patch.object(app, 'MARKER', marker), patch.object(app, 'certificate_thumbprint', return_value='B' * 40), \
                 patch.object(app.subprocess, 'run') as run:
                with self.assertRaises(RuntimeError):
                    app.untrust_certificate()
                run.assert_not_called()

    def test_stop_reaps_child_and_cleanup_can_repeat(self):
        ui = app.App.__new__(app.App)
        ui.process = process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('capture', 5),
                                    subprocess.TimeoutExpired('capture', 3), 0]
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'STATE', Path(folder) / 'state.json'), \
             patch.object(app, 'untrust_certificate') as untrust:
            self.assertTrue(ui.stop(update_ui=False))
            self.assertIsNone(ui.process)
            process.terminate.assert_called_once()
            process.kill.assert_called_once()
            self.assertTrue(ui.stop(update_ui=False))
            self.assertEqual(untrust.call_count, 2)

    def test_stop_prefers_graceful_control_marker(self):
        ui = app.App.__new__(app.App)
        ui.process = process = Mock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state.json'
            def wait(timeout):
                self.assertEqual(state.with_suffix('.stop').read_text(), 'stop')
                return 0
            process.wait.side_effect = wait
            with patch.object(app, 'STATE', state), patch.object(app, 'untrust_certificate'):
                self.assertTrue(ui.stop(update_ui=False))
                process.terminate.assert_not_called()
                process.kill.assert_not_called()
                self.assertFalse(state.with_suffix('.stop').exists())

    def test_stop_retains_process_when_termination_failed(self):
        ui = app.App.__new__(app.App)
        ui.process = process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('capture', 5)
        process.terminate.side_effect = OSError('cannot terminate')
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'STATE', Path(folder) / 'state.json'), \
             patch.object(app, 'untrust_certificate'):
            self.assertFalse(ui.stop(update_ui=False))
            self.assertIs(ui.process, process)


if __name__ == '__main__':
    unittest.main()
