"""Release startup remains independent from interception and legacy UI."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.wechat_channels import runtime


class ReleaseRuntimeTests(unittest.TestCase):
    def test_lock_excludes_second_window_and_releases_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.lock'
            one, two = runtime.InstanceLock(path), runtime.InstanceLock(path)
            try:
                self.assertTrue(one.acquire())
                self.assertFalse(two.acquire())
                one.close()
                self.assertTrue(two.acquire())
            finally:
                one.close()
                two.close()

    def test_absent_marker_does_not_touch_certificate_store(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime, 'MARKER', Path(directory)/'missing'):
            with patch.object(runtime.subprocess, 'run') as run:
                runtime.untrust_certificate()
                run.assert_not_called()

    def test_only_matching_legacy_certificate_can_be_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            marker = folder / 'marker.json'
            der = b'test-only-public-certificate-bytes'
            thumb = hashlib.sha1(der).hexdigest().upper()
            (folder/'mitmproxy-ca-cert.pem').write_text(
                '-----BEGIN CERTIFICATE-----\n'+base64.b64encode(der).decode()+'\n-----END CERTIFICATE-----\n',
                encoding='ascii')
            marker.write_text(json.dumps({'thumbprint': thumb}))
            with patch.object(runtime, 'MARKER', marker), patch.object(runtime, 'CONF', folder), \
                 patch.object(runtime.subprocess, 'run') as run, \
                 patch.object(runtime, 'certificate_is_trusted', return_value=True):
                self.assertEqual(runtime.certificate_thumbprint(), thumb)
                with self.assertRaises(RuntimeError):
                    runtime.untrust_certificate()
                self.assertTrue(marker.exists())
                self.assertEqual(run.call_args.args[0], ['certutil.exe','-user','-delstore','Root',thumb])
            with patch.object(runtime, 'MARKER', marker), patch.object(runtime, 'CONF', folder), \
                 patch.object(runtime.subprocess, 'run') as run, \
                 patch.object(runtime, 'certificate_is_trusted', return_value=False):
                marker.write_text(json.dumps({'thumbprint':'A'*40}))
                with self.assertRaises(RuntimeError):
                    runtime.untrust_certificate()
                run.assert_not_called()
                marker.write_text(json.dumps({'thumbprint':thumb}))
                runtime.untrust_certificate()
                self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
