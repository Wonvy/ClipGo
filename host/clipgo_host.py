#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
ClipGo native messaging host.

Chrome / Edge launch this script through Native Messaging. Messages are
JSON documents prefixed with a 4-byte length in native byte order.
Downloads are done by yt-dlp (+ ffmpeg for merging into MP4, + deno for
YouTube's JS challenges), all of which live in ./bin next to this file.
"""
import json
import os
import re
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import urlparse

VERSION = "1.0.0"
IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""
APP_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(APP_DIR, "bin")
# macOS uses the unpacked (onedir) yt-dlp build: the single-file build unpacks
# Python.framework into /tmp on every run, and when Edge/Chrome launched us those
# files get quarantined -> "Python.framework is damaged" Gatekeeper dialog.
YTDLP_MAC_DIR = os.path.join(BIN_DIR, "yt-dlp-mac")
YTDLP = os.path.join(BIN_DIR, "yt-dlp.exe") if IS_WIN else os.path.join(YTDLP_MAC_DIR, "yt-dlp_macos")
YTDLP_MAC_ZIP = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos.zip"
FFMPEG = os.path.join(BIN_DIR, "ffmpeg" + EXE)
DENO = os.path.join(BIN_DIR, "deno" + EXE)
# Windows: no console windows for child processes; own process group for cancel.
NO_WINDOW = 0x08000000 if IS_WIN else 0          # CREATE_NO_WINDOW
NEW_GROUP = 0x00000200 if IS_WIN else 0          # CREATE_NEW_PROCESS_GROUP

if IS_WIN:
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
LOG_FILE = os.path.join(APP_DIR, "host.log")
DEFAULT_DIR = os.path.expanduser("~/Downloads/ClipGo")

ALLOWED_HOSTS = ("youtube.com", "youtu.be", "instagram.com")
PROGRESS_TAG = "CLIPGO_P:"
FILE_TAG = "CLIPGO_F:"

_out_lock = threading.Lock()
_jobs = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------- utilities

def log(*parts):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.remove(LOG_FILE)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parts) + "\n")
    except Exception:
        pass


def send(msg):
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    with _out_lock:
        sys.stdout.buffer.write(struct.pack("@I", len(data)))
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    length = struct.unpack("@I", raw)[0]
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def download_dir():
    d = load_config().get("downloadDir") or DEFAULT_DIR
    os.makedirs(d, exist_ok=True)
    return d


def child_env():
    env = dict(os.environ)
    env["PATH"] = BIN_DIR + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    env["PYTHONIOENCODING"] = "utf-8"
    env["LANG"] = env.get("LANG") or "en_US.UTF-8"
    return env


def tool_version(path, args):
    if not os.path.exists(path):
        return None
    try:
        out = subprocess.run([path] + args, capture_output=True, text=True, timeout=30,
                             env=child_env(), creationflags=NO_WINDOW)
        line = (out.stdout or out.stderr).strip().splitlines()
        return line[0] if line else "unknown"
    except Exception as e:
        return "error: %s" % e


def is_allowed_url(url):
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def write_cookie_file(cookies):
    """Write browser cookies (chrome.cookies format) as a Netscape cookies.txt."""
    if not cookies:
        return None
    fd, path = tempfile.mkstemp(prefix="clipgo_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get("domain", "")
            host_only = c.get("hostOnly", False)
            if host_only:
                domain = domain.lstrip(".")
            elif not domain.startswith("."):
                domain = "." + domain
            f.write("\t".join([
                domain,
                "FALSE" if host_only else "TRUE",
                c.get("path", "/"),
                "TRUE" if c.get("secure") else "FALSE",
                str(int(c.get("expirationDate") or 0)),
                c.get("name", ""),
                c.get("value", ""),
            ]) + "\n")
    return path


def format_sort(quality):
    # Prefer H.264 + AAC so the MP4 plays everywhere (QuickTime, phones, editors).
    if quality == "best":
        return "res,ext:mp4:m4a"
    if str(quality).isdigit():
        return "res:%s,+codec:avc:m4a" % quality
    return "res:1080,+codec:avc:m4a"


def build_command(url, quality, cookie_file, out_dir):
    cmd = [
        YTDLP,
        "--ignore-config",
        "--no-playlist",
        "--no-mtime",
        "--newline",
        "--progress",
        "--progress-template",
        "download:" + PROGRESS_TAG + "%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(info.title)s",
        "--print", "after_move:" + FILE_TAG + "%(filepath)s",
        "--no-quiet",  # --print implies --quiet; keep [Merger] lines for the UI
        "--ffmpeg-location", BIN_DIR,
        "-f", "bv*+ba/b",
        "-S", format_sort(quality),
        "--merge-output-format", "mp4",
        "--remux-video", "mp4",
        "-o", os.path.join(out_dir, "%(title).120B [%(id)s].%(ext)s"),
    ]
    if os.path.exists(DENO):
        cmd += ["--js-runtimes", "deno:" + DENO]
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    cmd += ["--", url]
    return cmd


# ---------------------------------------------------------------- download job

def run_download(job_id, url, quality, cookies):
    cookie_file = None
    files = []
    last_error = None
    tail = []
    try:
        cookie_file = write_cookie_file(cookies)
        out_dir = download_dir()
        cmd = build_command(url, quality, cookie_file, out_dir)
        log("start", job_id, url, quality)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=child_env(),
            cwd=out_dir,
            start_new_session=not IS_WIN,
            creationflags=NO_WINDOW | NEW_GROUP,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with _jobs_lock:
            info = _jobs.setdefault(job_id, {"cancelled": False})
            info["proc"] = proc
            cancelled_early = info["cancelled"]
        if cancelled_early:
            kill_tree(proc)
        send({"type": "stage", "id": job_id, "stage": "解析视频信息…"})

        last_sent = 0.0
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(PROGRESS_TAG):
                now = time.time()
                parts = line[len(PROGRESS_TAG):].split("|", 3)
                pct = parts[0].strip().rstrip("%")
                is_final = pct.startswith("100")
                if now - last_sent < 0.4 and not is_final:
                    continue
                last_sent = now
                try:
                    pct_val = float(pct)
                except ValueError:
                    pct_val = None
                send({
                    "type": "progress",
                    "id": job_id,
                    "percent": pct_val,
                    "speed": parts[1].strip() if len(parts) > 1 else "",
                    "eta": parts[2].strip() if len(parts) > 2 else "",
                    "title": parts[3].strip() if len(parts) > 3 else "",
                })
                continue
            if line.startswith(FILE_TAG):
                files.append(line[len(FILE_TAG):].strip())
                continue
            if line.startswith("[Merger]"):
                send({"type": "stage", "id": job_id, "stage": "合并音视频为 MP4…"})
            elif line.startswith("[VideoRemuxer]"):
                send({"type": "stage", "id": job_id, "stage": "转封装为 MP4…"})
            if "ERROR:" in line:
                last_error = line.split("ERROR:", 1)[1].strip()
            tail.append(line)
            tail = tail[-30:]

        code = proc.wait()
        with _jobs_lock:
            info = _jobs.pop(job_id, {})
        if info.get("cancelled"):
            send({"type": "cancelled", "id": job_id})
        elif code == 0 and files:
            send({"type": "done", "id": job_id, "files": files})
        elif code == 0:
            # Already downloaded before: yt-dlp skips and prints nothing after_move.
            send({"type": "done", "id": job_id, "files": [], "note": "文件已存在，已跳过"})
        else:
            msg = last_error or (tail[-1] if tail else "yt-dlp 退出码 %s" % code)
            log("fail", job_id, code, "\n".join(tail))
            send({"type": "error", "id": job_id, "message": msg})
    except Exception as e:
        log("exception", job_id, traceback.format_exc())
        send({"type": "error", "id": job_id, "message": "助手内部错误: %s" % e})
    finally:
        if cookie_file:
            try:
                os.remove(cookie_file)
            except OSError:
                pass
        with _jobs_lock:
            _jobs.pop(job_id, None)


def kill_tree(proc):
    try:
        if IS_WIN:
            # Kill yt-dlp and its ffmpeg children.
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def cancel_job(job_id):
    with _jobs_lock:
        info = _jobs.get(job_id)
        if not info:
            return False
        info["cancelled"] = True
        proc = info.get("proc")
    if proc is not None:
        kill_tree(proc)
    return True


def cancel_all():
    with _jobs_lock:
        ids = list(_jobs.keys())
    for jid in ids:
        cancel_job(jid)


# ---------------------------------------------------------------- actions

def pick_folder_dialog(current):
    if IS_WIN:
        ps = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='选择 ClipGo 下载文件夹';"
            "$d.SelectedPath=$env:CLIPGO_CUR;"
            "if($d.ShowDialog($o) -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}"
        )
        env = dict(os.environ, CLIPGO_CUR=current)
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, env=env, creationflags=NO_WINDOW,
        )
        path = out.stdout.decode("utf-8", "replace").strip()
    else:
        script = 'POSIX path of (choose folder with prompt "选择 ClipGo 下载文件夹" default location (POSIX file "%s"))' % current.replace('"', '\\"')
        out = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
        path = out.stdout.strip()
        if path != "/":
            path = path.rstrip("/")
    if out.returncode != 0 or not path:
        return None
    return path


def choose_dir():
    path = pick_folder_dialog(download_dir())
    if not path:
        return None
    cfg = load_config()
    cfg["downloadDir"] = path
    save_config(cfg)
    return path


def reveal(path):
    if path and os.path.exists(path):
        if IS_WIN:
            subprocess.run(["explorer.exe", "/select,", os.path.normpath(path)])
        else:
            subprocess.run(["/usr/bin/open", "-R", path])
    else:
        open_dir(download_dir())


def open_dir(path):
    if IS_WIN:
        os.startfile(path)  # noqa: only exists on Windows
    else:
        subprocess.run(["/usr/bin/open", path])


def update_ytdlp():
    if IS_WIN:
        out = subprocess.run([YTDLP, "-U"], capture_output=True, text=True, env=child_env(),
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return out.returncode == 0, (out.stdout + out.stderr).strip()
    # macOS: re-download the onedir build, strip quarantine (we were spawned by the
    # browser, so everything we write is quarantined), then swap it in.
    import shutil
    work = tempfile.mkdtemp(prefix="clipgo_upd_", dir=BIN_DIR)
    try:
        zip_path = os.path.join(work, "yt-dlp.zip")
        new_dir = os.path.join(work, "yt-dlp-mac")
        steps = [
            ["/usr/bin/curl", "-fsSL", "--retry", "3", "-o", zip_path, YTDLP_MAC_ZIP],
            ["/usr/bin/ditto", "-x", "-k", zip_path, new_dir],
            ["/usr/bin/xattr", "-dr", "com.apple.quarantine", new_dir],
            [os.path.join(new_dir, "yt-dlp_macos"), "--version"],
        ]
        for cmd in steps:
            out = subprocess.run(cmd, capture_output=True, text=True, env=child_env())
            if out.returncode != 0:
                return False, "更新失败: %s" % (out.stderr.strip() or cmd[0])
        old_dir = os.path.join(work, "old")
        if os.path.exists(YTDLP_MAC_DIR):
            os.rename(YTDLP_MAC_DIR, old_dir)
        os.rename(new_dir, YTDLP_MAC_DIR)
        return True, "已更新到 %s" % out.stdout.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def handle(msg):
    action = msg.get("action")
    rid = msg.get("rid")

    def reply(payload):
        payload["rid"] = rid
        send(payload)

    if action == "ping":
        reply({
            "type": "pong",
            "version": VERSION,
            "ytdlp": tool_version(YTDLP, ["--version"]),
            "ffmpeg": bool(os.path.exists(FFMPEG)),
            "deno": bool(os.path.exists(DENO)),
            "downloadDir": download_dir(),
        })
    elif action == "download":
        url = (msg.get("url") or "").strip()
        job_id = msg.get("id")
        if not job_id:
            reply({"type": "error", "message": "缺少任务 id"})
        elif not is_allowed_url(url):
            send({"type": "error", "id": job_id, "message": "仅支持 YouTube / Instagram 链接"})
        elif not os.path.exists(YTDLP):
            send({"type": "error", "id": job_id, "message": "未找到 yt-dlp，请重新运行安装脚本"})
        else:
            with _jobs_lock:
                _jobs[job_id] = {"cancelled": False, "proc": None}
            t = threading.Thread(
                target=run_download,
                args=(job_id, url, msg.get("quality", "1080"), msg.get("cookies") or []),
                daemon=True,
            )
            t.start()
    elif action == "cancel":
        cancel_job(msg.get("id"))
    elif action == "reveal":
        reveal(msg.get("path") or "")
        reply({"type": "ok"})
    elif action == "openDir":
        open_dir(download_dir())
        reply({"type": "ok"})
    elif action == "chooseDir":
        reply({"type": "dir", "downloadDir": choose_dir() or download_dir()})
    elif action == "update":
        ok, text = update_ytdlp()
        reply({"type": "updated", "ok": ok, "output": text[-800:],
               "ytdlp": tool_version(YTDLP, ["--version"])})
    else:
        reply({"type": "error", "message": "未知操作: %s" % action})


def main():
    log("host started", VERSION)
    while True:
        try:
            msg = read_message()
        except Exception:
            log("bad message", traceback.format_exc())
            break
        if msg is None:
            break
        try:
            handle(msg)
        except Exception as e:
            log("handler error", traceback.format_exc())
            send({"type": "error", "rid": msg.get("rid"), "id": msg.get("id"), "message": str(e)})
    # Browser closed the pipe: stop running downloads, then exit.
    cancel_all()
    deadline = time.time() + 5
    while time.time() < deadline:
        with _jobs_lock:
            if not _jobs:
                break
        time.sleep(0.1)
    log("host exit")


if __name__ == "__main__":
    main()
