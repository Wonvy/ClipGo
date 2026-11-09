"""Administrator startup contract without opening UAC or touching trust stores."""
import ctypes
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tools.wechat_channels import elevation


class ElevationTests(unittest.TestCase):
    def test_cancelled_uac_returns_without_launch_success(self):
        shell = Mock()
        shell.ShellExecuteExW.return_value = False
        with patch.object(elevation, '_launch_command', return_value=('ClipGo.exe', '--elevated-user S-1-5-21-123')), \
             patch.object(ctypes, 'WinDLL', return_value=shell), \
             patch.object(ctypes, 'get_last_error', return_value=1223):
            self.assertFalse(elevation.request_elevated_start(True))
        self.assertEqual(shell.ShellExecuteExW.call_count, 1)

    def test_frozen_relaunch_uses_fixed_entrypoint_and_preserves_button_flag(self):
        with patch.object(elevation.sys, 'frozen', True, create=True), \
             patch.object(elevation.sys, 'executable', r'D:\Program Files\ClipGo\ClipGo.exe'), \
             patch.object(elevation, 'current_user_sid', return_value='S-1-5-21-123'):
            executable, arguments = elevation._launch_command(True)
        self.assertEqual(executable, str(Path(r'D:\Program Files\ClipGo\ClipGo.exe').resolve()))
        self.assertEqual(arguments, '--elevated-user S-1-5-21-123 --page-button')

    def test_source_relaunch_uses_absolute_script_without_shell_commands(self):
        with patch.object(elevation.sys, 'frozen', False, create=True), \
             patch.object(elevation, 'current_user_sid', return_value='S-1-5-21-123'):
            executable, arguments = elevation._launch_command()
        self.assertTrue(Path(executable).is_absolute())
        self.assertIn(str(Path(elevation.__file__).with_name('run.py').resolve()), arguments)
        self.assertNotIn('--page-button', arguments)

    def test_invalid_handoff_arguments_never_open_application(self):
        with patch('tools.wechat_channels.web_app.main') as main:
            for args in ([], ['bad'], ['S-1-5-21-123', '--capture'], ['S-1-5-21-123', '--page-button', 'extra']):
                self.assertEqual(elevation.elevated_main(args), 2)
            main.assert_not_called()

    def test_wrong_admin_account_cannot_take_over_user_network_settings(self):
        with patch.object(elevation, 'is_admin', return_value=True), \
             patch.object(elevation, 'current_user_sid', return_value='S-1-5-21-999'), \
             patch.object(ctypes.windll.user32, 'MessageBoxW'), \
             patch('tools.wechat_channels.web_app.main') as main:
            self.assertEqual(elevation.elevated_main(['S-1-5-21-123']), 2)
            main.assert_not_called()

    def test_unelevated_handoff_does_not_repeatedly_request_uac(self):
        with patch.object(elevation, 'is_admin', return_value=False), \
             patch.object(ctypes.windll.user32, 'MessageBoxW'), \
             patch.object(elevation, 'request_elevated_start') as relaunch, \
             patch('tools.wechat_channels.web_app.main') as main:
            self.assertEqual(elevation.elevated_main(['S-1-5-21-123']), 2)
            relaunch.assert_not_called()
            main.assert_not_called()

    def test_authorized_same_user_handoff_enables_requested_page_button(self):
        with patch.object(elevation, 'is_admin', return_value=True), \
             patch.object(elevation, 'current_user_sid', return_value='S-1-5-21-123'), \
             patch('tools.wechat_channels.web_app.main') as main:
            self.assertEqual(elevation.elevated_main(['S-1-5-21-123', '--page-button']), 0)
            main.assert_called_once_with(enable_page_button=True)


if __name__ == '__main__':
    unittest.main()
