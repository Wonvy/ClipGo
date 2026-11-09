"""Desktop startup helpers without the optional capture or legacy UI stack."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess

BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ClipGoChannels'
CONF = BASE / 'certificate'
MARKER = BASE / 'trusted-certificate.json'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class InstanceLock:
    """Keep a single normal window per user; Windows releases this on exit."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self.file = handle
        return True

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


def certificate_thumbprint():
    # The old experimental build wrote this certificate-only PEM. Hash the
    # exact DER bytes with stdlib SSL so cleanup needs no interception packages.
    pem = (CONF / 'mitmproxy-ca-cert.pem').read_text(encoding='ascii')
    return hashlib.sha1(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()


def certificate_is_trusted(thumb):
    return any(encoding == 'x509_asn' and hashlib.sha1(data).hexdigest().upper() == thumb
               for data, encoding, _trust in ssl.enum_certificates('ROOT'))


def untrust_certificate():
    """Recover only a precisely recorded certificate left by an older build."""
    if not MARKER.exists():
        return
    thumb = json.loads(MARKER.read_text(encoding='utf-8')).get('thumbprint')
    if not isinstance(thumb, str) or len(thumb) != 40 or any(c not in '0123456789ABCDEF' for c in thumb):
        raise RuntimeError('证书记录无效，无法自动清理。')
    if certificate_thumbprint() != thumb:
        raise RuntimeError('证书记录不匹配，请保留此目录以便检查。')
    subprocess.run(['certutil.exe', '-user', '-delstore', 'Root', thumb],
                   capture_output=True, creationflags=NO_WINDOW, timeout=30)
    if certificate_is_trusted(thumb):
        raise RuntimeError('临时证书移除失败，请先使用旧版工具完成清理。')
    MARKER.unlink(missing_ok=True)
