"""Local desktop UI. Merely opening it never enables interception."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import ssl
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .download import Cancelled, DownloadError, download_video
from .view import build_view, duration_text

BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ClipGoChannels'
CONF = BASE / 'certificate'
STATE = BASE / 'session.json'
MARKER = BASE / 'trusted-certificate.json'
SCAN_REPORT = BASE / 'last-scan.json'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def certificate_thumbprint() -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate((CONF / 'mitmproxy-ca-cert.pem').read_bytes())
    return cert.fingerprint(hashes.SHA1()).hex().upper()


def certificate_is_trusted(thumb: str) -> bool:
    """Check actual certificate bytes instead of localized certutil errors."""
    return any(encoding == 'x509_asn' and hashlib.sha1(data).hexdigest().upper() == thumb
               for data, encoding, _trust in ssl.enum_certificates('ROOT'))


def trust_certificate():
    from mitmproxy.certs import CertStore
    CONF.mkdir(parents=True, exist_ok=True)
    CertStore.from_store(CONF, 'mitmproxy', 2048)
    thumb = certificate_thumbprint()
    # Record intent before installation so a crash can be recovered on next launch.
    pending = MARKER.with_suffix('.tmp')
    pending.write_text(json.dumps({'thumbprint': thumb}), encoding='utf-8')
    os.replace(pending, MARKER)
    result = subprocess.run(['certutil.exe', '-user', '-addstore', 'Root', str(CONF / 'mitmproxy-ca-cert.pem')],
                            capture_output=True, creationflags=NO_WINDOW, timeout=30)
    if result.returncode or not certificate_is_trusted(thumb):
        raise RuntimeError('证书安装没有完成；监听尚未启动。')


def untrust_certificate():
    if not MARKER.exists():
        return
    thumb = json.loads(MARKER.read_text(encoding='utf-8'))['thumbprint']
    if len(thumb) != 40 or any(c not in '0123456789ABCDEF' for c in thumb):
        raise RuntimeError('证书记录无效，无法自动清理。')
    # The marker may only remove the exact CA generated in our isolated directory.
    if certificate_thumbprint() != thumb:
        raise RuntimeError('证书记录不匹配，请保留此目录以便检查。')
    result = subprocess.run(['certutil.exe', '-user', '-delstore', 'Root', thumb],
                            capture_output=True, creationflags=NO_WINDOW, timeout=30)
    # A nonzero certutil result alone is not proof of absence: it can also mean
    # access denied or another error. Keep recovery metadata until verified.
    if certificate_is_trusted(thumb):
        raise RuntimeError('临时证书移除失败，请点“清理临时证书”重试。')
    MARKER.unlink(missing_ok=True)


def capture_command():
    from .capture import ALLOW_HOSTS_PATTERN, LOCAL_CAPTURE_MODE
    if getattr(sys, 'frozen', False):
        command = [sys.executable, '--capture']
        addon = Path(sys._MEIPASS) / 'capture.py'
    else:
        command = [sys.executable, str(Path(__file__).with_name('run.py')), '--capture']
        addon = Path(__file__).with_name('capture.py')
    return command + ['--mode', LOCAL_CAPTURE_MODE,
                      '--allow-hosts', ALLOW_HOSTS_PATTERN,
                      '--set', f'confdir={CONF}', '--set', 'block_global=true', '--set', 'flow_detail=0',
                      '--set', 'termlog_verbosity=error', '-s', str(addon)]


class App:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.candidates = {}
        self.cancel = threading.Event()
        self.worker = None
        self.scan_worker = None
        self.scan_cancel = threading.Event()
        self.scan_result = None
        self.source = tk.StringVar(value='独立播放窗口')
        self.events = queue.Queue()
        self.last_state = 0
        self.closing = False
        self.folder = tk.StringVar(value=str(Path.home() / 'Downloads' / 'ClipGo视频号'))
        self.status = tk.StringVar(value='请在微信独立视频号窗口播放，再读取视频')
        self.detail = tk.StringVar(value='直接读取视频号已加载的地址，不修改微信，不接管网络。')
        self.search = tk.StringVar()
        self.list_count = tk.StringVar(value='0 个视频')
        self.empty_title = tk.StringVar(value='还没有视频')
        self.empty_hint = tk.StringVar(value='在微信中播放视频，然后点击「读取视频」')
        self.selection_state = tk.StringVar(value='等待选择')
        self.selected_author = tk.StringVar(value='—')
        self.selected_duration = tk.StringVar(value='—')
        self.selected_title = tk.StringVar(value='选择一个视频')
        self.show_all = False
        self.transfer_state = tk.StringVar(value='等待下载')
        self.task_title = tk.StringVar(value='选中视频后，点击右侧下载按钮')
        self.selected_id = None
        self.active_download_id = None
        self.download_states = {}
        self.saved_files = {}
        self.thumbnail_stop = threading.Event()
        self.thumbnail_jobs = queue.Queue()
        self.thumbnail_pending = set()
        self.thumbnail_workers_started = False
        self.detail.set('文件将保存到下方文件夹。')
        build_view(self)
        root.bind('<Destroy>', lambda event: self.thumbnail_stop.set() if event.widget is root else None, add='+')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(500, self.poll)
        if MARKER.exists():
            self.status.set('上次会话留下临时证书，请先点“清理临时证书”。')
        root.after(700, self.scan)

    def scan(self):
        if self.closing:
            return
        if self.scan_worker and self.scan_worker.is_alive():
            return
        if self.process:
            self.detail.set('请先停止实验网络监听，再读取已加载视频。')
            return
        self.scan_cancel.clear()
        source = 'standalone' if self.source.get() == '独立播放窗口' else 'main'
        self.scan_result = None
        self.scan_button.configure(state='disabled')
        self.source_picker.configure(state='disabled')
        self.scan_indicator.pack(side='right')
        self.scan_indicator.start(14)
        self.empty_title.set('正在读取视频')
        self.empty_hint.set('正在查找标题和播放信息，请稍候…')
        self.status.set('正在读取视频标题与播放信息…')
        def work():
            diagnostics = {}
            try:
                from .memory import scan_loaded_videos
                items = scan_loaded_videos(self.scan_cancel, diagnostics=diagnostics, source=source)
                self.events.put(('scan_done', items, diagnostics))
            except Exception as exc:
                from .memory import ScanError
                self.events.put(('scan_error', str(exc) if isinstance(exc, ScanError) else '无法读取播放数据。请先打开微信视频号并播放，再重试。', diagnostics))
        self.scan_worker = threading.Thread(target=work, daemon=True)
        self.scan_worker.start()

    def start(self):
        if self.process:
            return
        if self.active_download_id or (self.scan_worker and self.scan_worker.is_alive()):
            self.status.set('请等待当前读取或下载完成后，再使用实验监听。')
            return
        if not messagebox.askyesno('启用微信视频号监听',
            '本机实测此实验模式会影响微信播放，推荐使用“读取视频”。\n\n'
            '将安装本工具独立生成的临时 HTTPS 根证书，并启动按微信进程捕获的服务。\n\n'
            'Windows 可能要求管理员授权；证书受当前 Windows 用户信任。'
            '仅解析 channels.weixin.qq.com 和 finder.video.qq.com，不保存聊天内容或 Cookie，不修改系统代理。\n\n'
            '停止监听时移除临时证书。异常退出后可点“清理临时证书”。'
            '新版微信的原生协议可能无法取得解码信息，实验版不能保证每条视频可下载。\n\n现在启用？', parent=self.root):
            return
        try:
            BASE.mkdir(parents=True, exist_ok=True)
            STATE.unlink(missing_ok=True)
            STATE.with_suffix('.stop').unlink(missing_ok=True)
            self.candidates.clear()
            self.render_candidates()
            self.last_state = 0
            trust_certificate()
            env = dict(os.environ, CLIPGO_CHANNELS_STATE=str(STATE),
                       CLIPGO_PARENT_PID=str(os.getpid()))
            # No HTTP request logs are retained. The addon writes sanitized status only.
            self.process = subprocess.Popen(capture_command(), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            self.start_button.configure(state='disabled')
            self.stop_button.configure(state='normal')
            self.status.set('捕获服务启动中… 如出现系统授权窗口，请自行确认。')
        except Exception:
            cleaned = self.stop()
            self.status.set('启动失败。请检查依赖和权限；未确认启动成功。' +
                            ('' if cleaned else ' 请重试停止服务和清理临时证书。'))

    def stop(self, update_ui=True):
        process, self.process = self.process, None
        stopped = True
        if process:
            try:
                if process.poll() is None:
                    try:
                        STATE.with_suffix('.stop').write_text('stop', encoding='ascii')
                    except OSError:
                        process.terminate()
                    else:
                        try:
                            # Let mitmproxy stop its privileged redirector first.
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    self.process = process
                    stopped = False
        message = '监听已停止' if stopped else '捕获服务尚未退出，请重试停止监听。'
        try:
            untrust_certificate()
        except Exception:
            message += ' 临时证书清理失败，请点“清理临时证书”重试。'
            stopped = False
        try:
            STATE.unlink(missing_ok=True)
            if self.process is None:
                STATE.with_suffix('.stop').unlink(missing_ok=True)
        except OSError:
            message += ' 临时会话文件尚未清理。'
        if update_ui:
            self.start_button.configure(state='disabled' if self.process else 'normal')
            self.stop_button.configure(state='normal' if self.process else 'disabled')
            self.status.set(message)
        return stopped

    def cleanup(self):
        if self.process:
            self.stop()
            return
        try:
            untrust_certificate()
            self.status.set('临时证书已清理 · 未在监听')
        except Exception as exc:
            messagebox.showerror('清理未完成', str(exc), parent=self.root)

    def choose_folder(self):
        result = filedialog.askdirectory(parent=self.root)
        if result:
            self.folder.set(result)

    def open_folder(self):
        path = Path(self.folder.get()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError:
            messagebox.showerror('无法打开', '保存目录不可用，请更改保存位置。', parent=self.root)

    def source_changed(self, _event=None):
        self.candidates.clear()
        self.selected_id = None
        self.scan_result = None
        self.show_all = False
        self.search.set('')
        self.render_candidates()
        self.status.set(f'已切换到{self.source.get()}，点击「刷新视频」。')

    def toggle_all(self):
        self.show_all = not self.show_all
        self.render_candidates()

    def entry_title(self, entry):
        return str(entry.get('title') or '').strip() or '暂未读取到标题'

    def entry_state(self, ident, entry):
        return self.download_states.get(ident) or ('已识别' if entry.get('decode_key') not in (None, '') else '待校验')

    def render_candidates(self, entries=None):
        selection = self.tree.selection()
        previous = selection[0] if selection else self.selected_id
        if entries is not None:
            self.candidates = {str(entry['id']): entry for entry in entries}
        query = self.search.get().strip().casefold()
        def order(entry):
            try:
                duration = float(entry.get('duration') or 0)
            except (ValueError, TypeError):
                duration = 0
            return (entry.get('decode_key') not in (None, ''), duration)
        all_entries = list(self.candidates.values())
        recognized = [entry for entry in all_entries if entry.get('title') or
                      entry.get('decode_key') not in (None, '') or entry.get('thumbnail_url')]
        eligible = all_entries if self.show_all or not recognized else recognized
        visible = [entry for entry in sorted(eligible, key=order, reverse=True)
                   if not query or query in (self.entry_title(entry) + ' ' + str(entry.get('author') or '')).casefold()]
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(visible):
            ident = str(entry['id'])
            title = ' '.join(self.entry_title(entry).split())
            self.tree.insert('', 'end', iid=ident, values=(title, entry.get('author') or '—',
                duration_text(entry.get('duration')), self.entry_state(ident, entry)),
                tags=('alternate',) if index % 2 else ())
        count = len(self.candidates)
        self.list_count.set(f'{len(visible)} / {len(eligible)} 个视频' if query else f'{len(visible)} 个视频')
        self.show_all_button.configure(text='收起待识别视频' if self.show_all else f'显示全部 ({count})',
            state='normal' if recognized and len(recognized) < count else 'disabled')
        children = self.tree.get_children()
        if children:
            self.empty_panel.place_forget()
            ident = previous if previous in children else children[0]
            self.tree.selection_set(ident)
            self.tree.focus(ident)
            self.tree.see(ident)
        else:
            self.empty_title.set('没有匹配的视频' if query else '还没有视频')
            self.empty_hint.set('换个标题或作者关键词试试' if query else '在微信中播放视频，然后点击「刷新视频」')
            self.empty_panel.place(relx=.5, rely=.48, anchor='center')
        self.selection_changed()
        self.queue_thumbnails()

    def selection_changed(self, _event=None):
        selected = self.tree.selection()
        self.selected_id = selected[0] if selected else None
        entry = self.candidates.get(self.selected_id)
        self.selected_title.set(self.entry_title(entry) if entry else '选择一个视频')
        self.selected_author.set(str(entry.get('author') or '暂未读取到作者') if entry else '—')
        self.selected_duration.set(duration_text(entry.get('duration')) if entry else '—')
        self.selection_state.set(self.entry_state(self.selected_id, entry) if entry else '等待选择')
        self.tree.update_actions()

    def card_action(self, ident):
        if ident not in self.candidates:
            return
        self.tree.selection_set(ident)
        self.selection_changed()
        if ident in self.saved_files:
            self.open_video()
        else:
            self.download()

    def show_details(self, ident):
        entry = self.candidates.get(ident)
        if not entry:
            return
        self.tree.selection_set(ident)
        popup = tk.Toplevel(self.root)
        popup.title('视频标题')
        popup.transient(self.root)
        popup.geometry('480x320')
        popup.minsize(360, 240)
        body = ttk.Frame(popup, padding=22)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f"{entry.get('author') or '作者未知'}  ·  {duration_text(entry.get('duration'))}",
                  foreground='#74827C').pack(anchor='w', pady=(0, 14))
        title = tk.Text(body, wrap='word', relief='flat', padx=12, pady=12,
                        font=('Microsoft YaHei UI', 12), height=5)
        title.insert('1.0', self.entry_title(entry))
        title.configure(state='disabled')
        title.pack(fill='both', expand=True)
        ttk.Button(body, text='关闭', command=popup.destroy).pack(anchor='e', pady=(12, 0))

    def queue_thumbnails(self):
        queued = False
        for ident in self.tree.get_children()[:64]:
            url = self.candidates[ident].get('thumbnail_url')
            if not url or url in self.thumbnail_pending or url in self.tree.images or url in self.tree.failed_images:
                continue
            self.thumbnail_pending.add(url)
            self.thumbnail_jobs.put(url)
            queued = True
        if not queued or self.thumbnail_workers_started:
            return
        self.thumbnail_workers_started = True
        def work():
            from .thumbnails import load_thumbnail
            while not self.thumbnail_stop.is_set():
                try:
                    url = self.thumbnail_jobs.get(timeout=.3)
                except queue.Empty:
                    continue
                try:
                    image = load_thumbnail(url)
                except Exception:
                    image = None
                self.events.put(('thumbnail', url, image))
        for _ in range(3):
            threading.Thread(target=work, daemon=True).start()

    def open_video(self):
        path = self.saved_files.get(self.selected_id)
        if path:
            try:
                os.startfile(path)
            except OSError:
                messagebox.showerror('无法打开视频', '文件可能已移动，请打开保存文件夹查看。', parent=self.root)

    def cancel_download(self):
        if self.active_download_id:
            self.cancel.set()
            self.transfer_state.set('正在取消')
            self.detail.set('正在结束请求并清理未完成的文件…')
            self.cancel_button.configure(state='disabled')

    def download(self):
        selected = self.tree.selection()
        if not selected or self.active_download_id:
            return
        candidate = dict(self.candidates[selected[0]])
        folder = Path(self.folder.get())
        self.cancel.clear()
        self.active_download_id = str(candidate['id'])
        self.download_states[self.active_download_id] = '下载中'
        self.transfer_panel.grid()
        self.task_title.set(self.entry_title(candidate)[:90])
        self.transfer_state.set('正在下载')
        self.cancel_button.configure(state='normal')
        self.detail.set('正在连接视频并检查文件格式…')
        self.progress['value'] = 0
        self.render_candidates()
        def work():
            try:
                path = download_video(candidate, folder, self.cancel,
                    lambda received, total: self.events.put(('progress', received, total)))
                self.events.put(('done', path))
            except Cancelled as exc:
                self.events.put(('cancelled', str(exc)))
            except DownloadError as exc:
                self.events.put(('error', str(exc)))
            except Exception:
                self.events.put(('error', '下载失败。请重新播放目标视频后重试。'))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def finish_scan(self):
        self.scan_button.configure(state='normal')
        self.source_picker.configure(state='readonly')
        self.scan_indicator.stop()
        self.scan_indicator.pack_forget()

    def poll(self):
        if self.process and self.process.poll() is not None:
            cleaned = self.stop()
            self.status.set('监听已退出，请重新读取视频。' + ('' if cleaned else ' 临时证书未清理，请重试。'))
        try:
            stamp = STATE.stat().st_mtime_ns
            if self.process and stamp != self.last_state:
                state = json.loads(STATE.read_text(encoding='utf-8'))
                self.last_state = stamp
                entries = state.get('media', [])
                self.render_candidates(entries)
                errors = int(state.get('error_count', 0))
                self.status.set(f'监听中 · 已发现 {len(entries)} 个视频' + (f' · {errors} 次连接失败' if errors else ''))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        while not self.events.empty():
            event = self.events.get_nowait()
            if event[0] == 'scan_done':
                self.finish_scan()
                self.render_candidates(event[1])
                titled = sum(bool(str(entry.get('title') or '').strip()) for entry in event[1])
                self.status.set(f'已读取 {len(event[1])} 个视频 · {titled} 个已取得标题' if event[1]
                                else '未找到视频，请在微信中重新播放后再读取。')
                self.record_scan(event[2], 'ok')
            elif event[0] == 'scan_error':
                self.finish_scan()
                self.status.set(event[1])
                self.empty_title.set('暂时无法读取')
                self.empty_hint.set('请确认已在对应的微信窗口播放视频')
                self.record_scan(event[2], 'error')
            elif event[0] == 'thumbnail':
                url, image = event[1:]
                self.thumbnail_pending.discard(url)
                if any(entry.get('thumbnail_url') == url for entry in self.candidates.values()):
                    self.tree.set_thumbnail(url, image)
                    if self.scan_result is not None:
                        self.record_scan(self.scan_result, self.scan_result.get('result', 'ok'))
            elif event[0] == 'progress':
                received, total = event[1:]
                self.progress['value'] = min(100, received / total * 100) if total else 0
                if not self.cancel.is_set():
                    self.detail.set(f'{received / 1048576:.1f} MB' +
                        (f' / {total / 1048576:.1f} MB  ·  {received / total:.0%}' if total else ' · 正在接收视频'))
            elif event[0] in ('done', 'error', 'cancelled'):
                ident = self.active_download_id
                self.cancel_button.configure(state='disabled')
                if event[0] == 'done':
                    self.progress['value'] = 100
                    self.saved_files[ident] = event[1]
                    self.download_states[ident] = '已完成'
                    self.transfer_state.set('下载完成')
                    self.detail.set(f'已保存：{event[1].name}')
                else:
                    self.progress['value'] = 0
                    label = '已取消' if event[0] == 'cancelled' else '下载失败'
                    self.download_states[ident] = label
                    self.transfer_state.set(label)
                    self.detail.set(event[1])
                self.active_download_id = None
                self.render_candidates()
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(150, self.poll)

    def record_scan(self, diagnostics, result):
        # Record counts only, never media URLs, keys or surrounding process data.
        self.scan_result = dict(diagnostics, result=result, ui_rows=len(self.tree.get_children()),
            titled_rows=sum(bool(str(item.get('title') or '').strip()) for item in self.candidates.values()),
            thumbnail_rows=sum(bool(self.candidates[ident].get('thumbnail_url') in self.tree.images) for ident in self.tree.get_children()),
            thumbnail_expected=sum(bool(self.candidates[ident].get('thumbnail_url')) for ident in self.tree.get_children()))
        try:
            BASE.mkdir(parents=True, exist_ok=True)
            temporary = SCAN_REPORT.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.scan_result), encoding='utf-8')
            os.replace(temporary, SCAN_REPORT)
        except OSError:
            pass

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.scan_cancel.set()
        self.cancel.set()
        self.thumbnail_stop.set()
        if not self.stop():
            self.closing = False
            self.detail.set('结束会话未完成，请重试停止监听或清理临时证书。')
            return
        self.detail.set('正在结束下载并清理临时文件…')
        if not self.worker or not self.worker.is_alive():
            self.root.destroy()


class InstanceLock:
    """One UI/certificate owner per Windows user; OS releases this on crash."""
    def __init__(self, path: Path):
        self.path = path
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


def diagnose_ui(destination):
    """Exercise the real Tk queue/render path without displaying a second UI."""
    root = tk.Tk()
    root.withdraw()
    app = App(root)
    deadline = time.monotonic() + 110
    try:
        while (app.scan_result is None or app.thumbnail_pending) and time.monotonic() < deadline:
            root.update()
            time.sleep(.05)
        report = app.scan_result or {'result': 'timeout', 'ui_rows': len(app.tree.get_children())}
        Path(destination).write_text(json.dumps(report), encoding='utf-8')
    finally:
        app.scan_cancel.set()
        root.destroy()


def main():
    if os.name != 'nt':
        raise SystemExit('本工具目前仅支持 Windows。')
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    lock = InstanceLock(BASE / 'instance.lock')
    if not lock.acquire():
        ctypes.windll.user32.MessageBoxW(None, '视频号助手已经打开，请使用现有窗口。', 'ClipGo 视频号', 0x40)
        return
    app = None
    try:
        root = tk.Tk()
        app = App(root)
        root.mainloop()
    finally:
        if app:
            app.scan_cancel.set()
            app.cancel.set()
            app.stop(update_ui=False)
        lock.close()
