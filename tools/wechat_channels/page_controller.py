"""Own a reversible page-proxy session, its certificate and narrow PAC route."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import socket
import ssl
import subprocess
import sys
import threading
import time

BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ClipGoChannels' / 'page-embedding'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
PROXY_KEYS = ('ProxyEnable', 'AutoConfigURL')
_CERTIFICATE_LOCK = threading.RLock()


class PageSetupError(RuntimeError):
    pass


class PageSetupCancelled(PageSetupError):
    pass


@contextmanager
def _certificate_guard():
    """Serialize recovery with both GUI preference writes and helper recovery."""
    with _CERTIFICATE_LOCK:
        if os.name != 'nt':
            yield
            return
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        for name in ('ReleaseMutex', 'CloseHandle'):
            function = getattr(kernel32, name)
            function.argtypes = (ctypes.c_void_p,)
            function.restype = ctypes.c_int
        identity = hashlib.sha256(str(BASE.resolve()).casefold().encode()).hexdigest()
        handle = kernel32.CreateMutexW(None, False, 'Local\\ClipGoPageCertificate-' + identity)
        if not handle:
            raise PageSetupError('无法准备页内按钮证书恢复锁。')
        acquired = False
        try:
            # WAIT_ABANDONED is expected after a killed owner and grants us
            # the lock so the journal can repair its interrupted operation.
            acquired = kernel32.WaitForSingleObject(handle, 8000) in (0, 0x80)
            if not acquired:
                raise PageSetupError('证书设置正在清理，请稍后重试。')
            yield
        finally:
            if acquired:
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{secrets.token_hex(8)}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WindowsProxySettings:
    path = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'

    def read(self, name):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.path) as key:
            try:
                value, kind = winreg.QueryValueEx(key, name)
                return {'exists': True, 'kind': kind, 'value': value}
            except FileNotFoundError:
                return {'exists': False}

    def write(self, name, entry):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.path, 0, winreg.KEY_SET_VALUE) as key:
            if entry['exists']:
                winreg.SetValueEx(key, name, 0, entry['kind'], entry['value'])
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass

    def notify(self):
        # Tell existing WinINet clients to re-read their current-user proxy.
        wininet = ctypes.WinDLL('wininet', use_last_error=True)
        wininet.InternetSetOptionW(None, 39, None, 0)
        wininet.InternetSetOptionW(None, 37, None, 0)


def restore_proxy(settings, before, applied):
    """Restore only values still owned by this session, preserving user edits."""
    changed = False
    for name in PROXY_KEYS:
        if name in applied and settings.read(name) == applied[name]:
            settings.write(name, before[name])
            changed = True
    if changed:
        settings.notify()


def _thumbprint(path):
    pem = Path(path).read_text(encoding='ascii')
    return hashlib.sha1(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()


def _validate_scope(scope):
    if scope not in ('user', 'machine'):
        raise PageSetupError('页内按钮证书作用域记录无效。')
    return scope


def _record_scope(data):
    if not isinstance(data, dict) or data.get('version') not in (1, 2):
        raise PageSetupError('页内按钮证书记录版本无效。')
    return _validate_scope(data.get('scope', 'user') if data['version'] == 1 else data.get('scope'))


def _trusted(thumb, scope='user'):
    """Read one physical store; never merge machine trust into user trust."""
    import winreg
    _validate_scope(scope)
    if not isinstance(thumb, str) or not re.fullmatch(r'[0-9A-F]{40}', thumb):
        raise ValueError('Invalid certificate thumbprint')
    hive = winreg.HKEY_CURRENT_USER if scope == 'user' else winreg.HKEY_LOCAL_MACHINE
    path = 'Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\' + thumb
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False


def _is_admin():
    from .elevation import is_admin
    return is_admin()


def _require_scope_permission(scope):
    _validate_scope(scope)
    if scope == 'machine' and not _is_admin():
        raise PageSetupError('本机证书需要管理员权限，请以管理员身份重新打开工具。')


def _certutil_path():
    """Resolve the Windows system tool without searching cwd or PATH."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    kernel32.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer) or not Path(buffer.value).is_absolute():
        raise PageSetupError('无法找到 Windows 系统证书工具。')
    return str(Path(buffer.value) / 'certutil.exe')


def _certificate_preferences():
    try:
        data = json.loads((BASE / 'preferences.json').read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('version') in (1, 2):
            return {'keep_certificate': data.get('keep_certificate') is True,
                    'scope': _record_scope(data)}
    except (OSError, ValueError, PageSetupError):
        pass
    return {'keep_certificate': False, 'scope': 'user'}


def get_certificate_preference():
    """Keep trust only after an explicit local opt-in; malformed data opts out."""
    return _certificate_preferences()['keep_certificate']


def get_certificate_scope():
    return _certificate_preferences()['scope']


def set_certificate_scope(scope):
    _validate_scope(scope)
    with _certificate_guard():
        preferences = _certificate_preferences()
        _atomic_json(BASE / 'preferences.json', dict(preferences, version=2, scope=scope))


def set_certificate_preference(keep):
    """Persist a choice only. This never installs a CA or changes networking."""
    if type(keep) is not bool:
        raise ValueError('Certificate preference must be a boolean')
    with _certificate_guard():
        preferences = _certificate_preferences()
        _atomic_json(BASE / 'preferences.json', dict(preferences, version=2, keep_certificate=keep))


def _check_certificate_thumbprint(thumb):
    certificate = BASE / 'certificate' / 'mitmproxy-ca-cert.pem'
    if (not isinstance(thumb, str) or not re.fullmatch(r'[0-9A-F]{40}', thumb)
            or _thumbprint(certificate) != thumb):
        raise PageSetupError('页内按钮证书记录不匹配。')
    return thumb


def _owned_certificate():
    marker = BASE / 'owned-certificate.json'
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        scope = _record_scope(data)
        return {'thumbprint': _check_certificate_thumbprint(data.get('thumbprint')), 'scope': scope}
    except (OSError, ValueError) as exc:
        raise PageSetupError('页内按钮证书归属记录无效。') from exc


def _owned_certificate_thumbprint():
    owned = _owned_certificate()
    return owned['thumbprint'] if owned else None


def _record_owned_certificate(thumb, scope='user'):
    with _certificate_guard():
        _check_certificate_thumbprint(thumb)
        _validate_scope(scope)
        record = {'thumbprint': thumb, 'scope': scope}
        previous = _owned_certificate()
        if previous is not None and previous != record:
            raise PageSetupError('页内按钮证书归属记录不匹配。')
        _atomic_json(BASE / 'owned-certificate.json', dict(record, version=2))


def _remove_certificate(thumb, scope='user'):
    _check_certificate_thumbprint(thumb)
    _validate_scope(scope)
    if _trusted(thumb, scope):
        _require_scope_permission(scope)
        command = [_certutil_path()] + (['-user'] if scope == 'user' else [])
        subprocess.run(command + ['-delstore', 'Root', thumb],
            capture_output=True, creationflags=NO_WINDOW, timeout=10)
    if _trusted(thumb, scope):
        raise PageSetupError('网络设置已恢复，临时证书尚未清理完成。')
    (BASE / 'owned-certificate.json').unlink(missing_ok=True)


def certificate_status():
    """Return safe display state; strict validation belongs to PAC-first recovery."""
    scope = get_certificate_scope()
    installed_scope = None
    try:
        certificate = BASE / 'certificate' / 'mitmproxy-ca-cert.pem'
        if certificate.is_file():
            thumb = _thumbprint(certificate)
            owned = _owned_certificate()
            journal = BASE / 'session.json'
            if owned is None and journal.exists():
                session = json.loads(journal.read_text(encoding='utf-8'))
                owned = {'thumbprint': _check_certificate_thumbprint(session.get('thumbprint')),
                         'scope': _record_scope(session)}
            if owned is not None and _trusted(thumb, owned['scope']):
                installed_scope = owned['scope']
            elif _trusted(thumb, scope):
                installed_scope = scope
    except (OSError, ValueError, TypeError, AttributeError, PageSetupError):
        installed_scope = None
    return {'keep': get_certificate_preference(), 'installed': installed_scope is not None,
            'scope': scope, 'installed_scope': installed_scope}


def certificate_requires_admin():
    """Give a best-effort elevation hint without blocking PAC-first recovery."""
    if get_certificate_scope() == 'machine':
        return True
    for name in ('owned-certificate.json', 'session.json'):
        try:
            data = json.loads((BASE / name).read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('scope') == 'machine':
                return True
        except (OSError, ValueError):
            continue
    return False


def remove_owned_certificate():
    """Called after stopping the controller; never remove pre-existing trust."""
    with _certificate_guard():
        set_certificate_preference(False)
        owned = _owned_certificate()
        if owned is not None:
            _remove_certificate(owned['thumbprint'], owned['scope'])
        return certificate_status()


def _valid_entry(entry):
    return isinstance(entry, dict) and (entry == {'exists': False} or
        entry.get('exists') is True and
        ((entry.get('kind') == 4 and type(entry.get('value')) is int) or
         (entry.get('kind') == 1 and isinstance(entry.get('value'), str))))


def recover_session(journal_path=None, expected_token=None):
    """Idempotent recovery used on normal exit, next launch and parent loss."""
    with _certificate_guard():
        return _recover_session(journal_path, expected_token)


def _recover_session(journal_path=None, expected_token=None):
    journal = Path(journal_path or BASE / 'session.json').resolve()
    if journal != (BASE / 'session.json').resolve():
        raise PageSetupError('无法识别页内按钮恢复记录。')
    if not journal.exists():
        with _CERTIFICATE_LOCK:
            if not get_certificate_preference():
                owned = _owned_certificate()
                if owned is not None:
                    _remove_certificate(owned['thumbprint'], owned['scope'])
        return
    data = json.loads(journal.read_text(encoding='utf-8'))
    token = data.get('token', '')
    if (data.get('version') not in (1, 2) or not isinstance(token, str) or len(token) != 64
            or any(c not in '0123456789abcdef' for c in token)
            or expected_token is not None and token != expected_token):
        raise PageSetupError('页内按钮恢复记录无效。')
    before, applied = data.get('before', {}), data.get('applied', {})
    if set(before) != set(PROXY_KEYS) or set(applied) != set(PROXY_KEYS):
        raise PageSetupError('页内按钮网络恢复记录无效。')
    if not all(_valid_entry(entry) for entry in [*before.values(), *applied.values()]):
        raise PageSetupError('页内按钮网络恢复记录无效。')
    pac = applied['AutoConfigURL'].get('value', '')
    from urllib.parse import urlsplit
    parsed = urlsplit(pac)
    if (applied['ProxyEnable'] != {'exists': True, 'kind': 4, 'value': 0}
            or parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != f'/__clipgo/{token}/proxy.pac'):
        raise PageSetupError('页内按钮网络恢复目标无效。')
    # Restore networking first, even if certificate cleanup needs another retry.
    restore_proxy(WindowsProxySettings(), before, applied)
    thumb = data.get('thumbprint', '')
    _check_certificate_thumbprint(thumb)
    scope = _record_scope(data)
    with _CERTIFICATE_LOCK:
        owned = _owned_certificate()
        record = {'thumbprint': thumb, 'scope': scope}
        if owned is not None and owned != record:
            raise PageSetupError('页内按钮证书归属与会话作用域不匹配。')
        # Also adopt an interrupted session created before ownership markers
        # were introduced, but never claim a certificate already trusted then.
        if data.get('trusted_before') is False and owned is None:
            _record_owned_certificate(thumb, scope)
            owned = record
        if not get_certificate_preference() and owned == record:
            _remove_certificate(thumb, scope)
    journal.unlink(missing_ok=True)


class PageController:
    def __init__(self, *, on_candidate, get_status, on_event, on_state):
        self._on_candidate, self._get_status = on_candidate, get_status
        self._on_event, self._on_state = on_event, on_state
        self._process = None
        self._certificate_process = None
        self._bridge = None
        self._stop = threading.Event()
        self._mutex = threading.RLock()
        self._token = None
        self._status_path = None
        self._stop_path = None
        self._config_path = None
        self._watcher = None

    def start(self, cancelled=lambda: False):
        try:
            self._start(cancelled)
        except Exception:
            self.stop()
            raise

    def request_stop(self):
        self._stop.set()

    def _install_certificate(self, certificate, thumb, check_cancelled, timeout=120, scope='user'):
        """Keep Windows' confirmation, but never outlive cancellation/cleanup."""
        check_cancelled()
        _require_scope_permission(scope)
        command = [_certutil_path()] + (['-user'] if scope == 'user' else [])
        command += ['-addstore', 'Root', str(certificate)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self._certificate_process = process
        try:
            deadline = time.monotonic() + timeout
            while True:
                check_cancelled()
                result = process.poll()
                if result is not None:
                    if result or not _trusted(thumb, scope):
                        raise PageSetupError('临时证书尚未启用，页内按钮没有启动。')
                    return
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                self._stop.wait(.1)
        finally:
            # Reap the confirmation process before recovery checks the trust
            # store, preventing a late confirmation from reinstalling the CA.
            self._finish_certificate_process()

    def _finish_certificate_process(self):
        process = self._certificate_process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                if process.poll() is None:
                    process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        self._certificate_process = None

    def _start(self, cancelled):
        with self._mutex:
            if self._process:
                return
            self._stop = threading.Event()
            def check_cancelled():
                if self._stop.is_set() or cancelled():
                    raise PageSetupCancelled('已取消开启微信内按钮。')
            check_cancelled()
            try:
                from mitmproxy.certs import CertStore
            except ImportError as exc:
                raise PageSetupError('当前版本未包含页内按钮组件，请使用完整新版。') from exc
            recover_session()
            scope = get_certificate_scope()
            _require_scope_permission(scope)
            owned = _owned_certificate()
            if owned is not None and owned['scope'] != scope:
                raise PageSetupError('已有另一作用域的证书，请先移除后再切换。')
            settings = WindowsProxySettings()
            before = {name: settings.read(name) for name in PROXY_KEYS}
            if before['ProxyEnable'].get('value') or before['AutoConfigURL'].get('value'):
                raise PageSetupError('检测到已有代理设置。请先停用其他代理，再开启微信内下载按钮。')
            from .page_bridge import PageBridge
            self._bridge = PageBridge(on_candidate=self._on_candidate,
                                      get_status=self._get_status, on_event=self._on_event)
            bridge_url = self._bridge.start()
            self._token = secrets.token_hex(32)
            BASE.mkdir(parents=True, exist_ok=True)
            session_dir = BASE / self._token[:16]
            session_dir.mkdir()
            self._status_path, self._stop_path = session_dir / 'status.json', session_dir / 'stop'
            self._config_path = session_dir / 'config.json'
            confdir = BASE / 'certificate'
            confdir.mkdir(exist_ok=True)
            CertStore.from_store(confdir, 'mitmproxy', 2048)
            certificate = confdir / 'mitmproxy-ca-cert.pem'
            thumb = _thumbprint(certificate)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            pac = f'http://127.0.0.1:{port}/__clipgo/{self._token}/proxy.pac'
            applied = {'ProxyEnable': {'exists': True, 'kind': 4, 'value': 0},
                       'AutoConfigURL': {'exists': True, 'kind': 1, 'value': pac}}
            journal = {'version': 2, 'token': self._token, 'before': before, 'applied': applied,
                       'thumbprint': thumb, 'scope': scope, 'trusted_before': _trusted(thumb, scope)}
            _atomic_json(BASE / 'session.json', journal)
            try:
                if not journal['trusted_before']:
                    # Persist ownership before showing Windows' confirmation:
                    # a crash immediately after confirmation remains recoverable.
                    _record_owned_certificate(thumb, scope)
                    message = ('正在启用管理员证书…' if scope == 'machine'
                               else '请完成 Windows 弹出的本机证书确认')
                    self._on_state({'state': 'starting', 'message': message})
                    self._install_certificate(certificate, thumb, check_cancelled, scope=scope)
                check_cancelled()
                config = {'token': self._token, 'bridge_url': bridge_url,
                          'bridge_secret': self._bridge.secret, 'listen_port': port,
                          'confdir': str(confdir), 'status_path': str(self._status_path),
                          'stop_path': str(self._stop_path), 'parent_pid': os.getpid(),
                          'recovery_path': str(BASE / 'session.json')}
                _atomic_json(self._config_path, config)
                command = [sys.executable]
                if not getattr(sys, 'frozen', False):
                    command.append(str(Path(__file__).with_name('run.py')))
                command += ['--page-proxy', str(self._config_path)]
                self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    check_cancelled()
                    if self._process.poll() is not None:
                        raise PageSetupError('页内按钮服务未能启动，网络设置已恢复。')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.3):
                            ready = json.loads(self._status_path.read_text(encoding='utf-8'))
                            if ready.get('server_ready') is True:
                                break
                    except (OSError, ValueError):
                        pass
                    finally:
                        time.sleep(.1)
                else:
                    raise PageSetupError('页内按钮服务启动超时。')
                # The helper is already accepting requests before routing changes.
                import urllib.request
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(pac, timeout=3) as response:
                    body = response.read(4096)
                    if response.status != 200 or b'FindProxyForURL' not in body or b'channels.weixin.qq.com' not in body:
                        raise PageSetupError('页面路由未准备完成，网络设置没有变更。')
                if any(settings.read(name) != before[name] for name in PROXY_KEYS):
                    raise PageSetupError('启动期间代理设置发生变化，已取消开启页内按钮。')
                check_cancelled()
                for name in PROXY_KEYS:
                    settings.write(name, applied[name])
                settings.notify()
                self._on_state({'state': 'enabled', 'message': '已开启，请重新打开微信视频号页面'})
                self._watcher = threading.Thread(target=self._watch,
                    args=(self._process, self._stop, self._token, self._status_path),
                    daemon=True, name='ClipGo page monitor')
                self._watcher.start()
            except subprocess.TimeoutExpired as exc:
                self.stop()
                raise PageSetupError('Windows 证书确认未完成，已取消开启并恢复设置。') from exc
            except Exception:
                self.stop()
                raise

    def _watch(self, process, stop, token, status_path):
        previous = None
        while not stop.wait(1):
            with self._mutex:
                if self._process is not process or self._token != token:
                    return
                if process.poll() is not None:
                    try:
                        self.stop()
                        self._on_state({'state': 'error', 'message': '页内服务已退出，网络设置已恢复'})
                    except Exception:
                        self._on_state({'state': 'error', 'message': '页内服务退出，请关闭后重开工具完成清理'})
                    return
            try:
                safe = json.loads(status_path.read_text(encoding='utf-8'))
                # Only aggregate counts leave the private process status file.
                counts = {key: value for key, value in safe.items()
                          if type(value) is int and 0 <= value < 1_000_000}
                if counts != previous:
                    previous = counts
                    with self._mutex:
                        if self._process is process and self._token == token and not stop.is_set():
                            self._on_state({'state': 'enabled', 'counters': counts})
            except (OSError, ValueError):
                pass

    def stop(self):
        self.request_stop()
        with self._mutex:
            self._stop.set()
            cleanup_error = None
            try:
                # Retry a failed process reap before touching the trust store.
                # Keep its recovery journal if Windows cannot stop it yet.
                self._finish_certificate_process()
                if self._token:
                    recover_session(expected_token=self._token)
            except Exception as exc:
                cleanup_error = exc
            process, self._process = self._process, None
            try:
                if self._stop_path:
                    try:
                        self._stop_path.touch(exist_ok=True)
                    except OSError:
                        # The process handle still provides a reliable stop if
                        # disk writes fail. Its config is removed below.
                        if process and process.poll() is None:
                            process.terminate()
                if process:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
            finally:
                try:
                    if self._bridge:
                        self._bridge.close()
                        self._bridge = None
                finally:
                    if self._config_path:
                        self._config_path.unlink(missing_ok=True)
            if cleanup_error:
                raise PageSetupError('网络恢复或证书清理尚未完成，请关闭后重新打开工具重试。') from cleanup_error
            self._on_state({'state': 'disabled', 'message': '微信内下载按钮已关闭'})
