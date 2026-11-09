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

VERSION = "1.3.0"
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

ALLOWED_HOSTS = (
    "youtube.com", "youtu.be", "instagram.com", "bilibili.com", "b23.tv", "x.com", "twitter.com",
    "xiaohongshu.com", "xhslink.com", "tiktok.com", "douyin.com", "v.qq.com",
)
# Keep accepting the old unquoted protocol for compatibility with older tools.
PROGRESS_TAG = "CLIPGO_P:"
FILE_TAG = "CLIPGO_F:"
PROGRESS_JSON_TAG = "CLIPGO_PJ:"
FILE_JSON_TAG = "CLIPGO_FJ:"
TITLE_JSON_TAG = "CLIPGO_TJ:"

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


def output_text(value, json_encoded=False):
    """Decode JSON-escaped protocol text without guessing at legacy text."""
    if not json_encoded:
        return value.strip()
    decoded = json.loads(value)
    if not isinstance(decoded, str):
        raise ValueError("下载器返回了无效的文本字段")
    return decoded


def douyin_media_info(page_url, media, title):
    """Accept only a matching Douyin video and clear HTTPS video-CDN sources."""
    match = re.fullmatch(r"https://www\.douyin\.com/video/(\d+)", page_url)
    if not match or not isinstance(media, dict) or media.get("videoUrl") != page_url:
        raise ValueError("页面视频地址与下载任务不匹配")

    def source_url(raw):
        if not isinstance(raw, str) or len(raw) > 16000:
            raise ValueError("无效的抖音播放地址")
        p = urlparse(raw)
        hostname = (p.hostname or "").lower()
        if (p.scheme != "https" or p.username or p.password or p.port is not None
                or not (hostname == "douyinvod.com" or hostname.endswith(".douyinvod.com"))):
            raise ValueError("仅接受抖音视频 CDN 的 HTTPS 播放地址")
        return raw

    primary = source_url(media.get("url"))
    formats = []
    candidates = media.get("formats") if isinstance(media.get("formats"), list) else []
    for f in candidates[:16]:
        if not isinstance(f, dict):
            continue
        source = source_url(f.get("url"))
        item = {"url": source, "ext": "mp4", "protocol": "https", "format_id": str(len(formats))}
        for key in ("width", "height"):
            value = f.get(key)
            if isinstance(value, (int, float)) and 0 < value <= 20000:
                item[key] = value
        bitrate = f.get("bitrate")
        if isinstance(bitrate, (int, float)) and 0 < bitrate <= 1000000000:
            item["tbr"] = bitrate / 1000
        formats.append(item)
    if not formats:
        formats.append({"url": primary, "ext": "mp4", "protocol": "https", "format_id": "page"})
    return {
        "id": match.group(1), "title": title.strip()[:1000] if isinstance(title, str) and title.strip() else "抖音视频 " + match.group(1),
        "webpage_url": page_url, "extractor": "ClipGoDouyin", "extractor_key": "Generic",
        "formats": formats, "http_headers": {"Referer": "https://www.douyin.com/"},
    }


def build_command(url, quality, cookie_file, out_dir, info_file=None):
    cmd = [
        YTDLP,
        "--ignore-config",
        "--encoding", "utf-8",  # Match our pipe decoder, including Windows packaged yt-dlp
        "--no-playlist",  # Bilibili: selected ?p=N part, or the first part by default
        "--no-mtime",
        "--newline",
        "--progress",
        "--progress-template",
        # j escapes Unicode and newlines as ASCII JSON, independent of code page.
        "download:" + PROGRESS_JSON_TAG + "%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(info.title)j",
        "--print", "before_dl:" + TITLE_JSON_TAG + "%(title)j",
        "--print", "after_move:" + FILE_JSON_TAG + "%(filepath)j",
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
    if urlparse(url).hostname == "v.qq.com":
        cmd += ["--plugin-dirs", APP_DIR]
    if info_file:
        cmd += ["--load-info-json", info_file]
    else:
        cmd += ["--", url]
    return cmd


# ---------------------------------------------------------------- download job

def run_download(job_id, url, quality, cookies, media_info=None):
    cookie_file = None
    info_file = None
    files = []
    metadata_title = None
    last_error = None
    tail = []
    try:
        cookie_file = write_cookie_file(cookies)
        out_dir = download_dir()
        if media_info:
            fd, info_file = tempfile.mkstemp(prefix="clipgo_media_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(media_info, f, ensure_ascii=True)
        cmd = build_command(url, quality, cookie_file, out_dir, info_file)
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
            if line.startswith(TITLE_JSON_TAG):
                metadata_title = output_text(line[len(TITLE_JSON_TAG):], json_encoded=True)
                send({"type": "stage", "id": job_id, "stage": "准备下载…", "title": metadata_title})
                continue
            if line.startswith((PROGRESS_TAG, PROGRESS_JSON_TAG)):
                now = time.time()
                json_encoded = line.startswith(PROGRESS_JSON_TAG)
                tag = PROGRESS_JSON_TAG if json_encoded else PROGRESS_TAG
                parts = line[len(tag):].split("|", 3)
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
                    "title": output_text(parts[3], json_encoded) if len(parts) > 3 else "",
                })
                continue
            if line.startswith((FILE_TAG, FILE_JSON_TAG)):
                json_encoded = line.startswith(FILE_JSON_TAG)
                tag = FILE_JSON_TAG if json_encoded else FILE_TAG
                files.append(output_text(line[len(tag):], json_encoded))
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
            send({"type": "done", "id": job_id, "files": files,
                  **({"title": metadata_title} if metadata_title is not None else {})})
        elif code == 0:
            # Already downloaded before: yt-dlp skips and prints nothing after_move.
            send({"type": "done", "id": job_id, "files": [], "note": "文件已存在，已跳过",
                  **({"title": metadata_title} if metadata_title is not None else {})})
        else:
            msg = last_error or (tail[-1] if tail else "yt-dlp 退出码 %s" % code)
            log("fail", job_id, code, "\n".join(tail))
            send({"type": "error", "id": job_id, "message": msg})
    except Exception as e:
        log("exception", job_id, traceback.format_exc())
        send({"type": "error", "id": job_id, "message": "助手内部错误: %s" % e})
    finally:
        if info_file:
            try:
                os.remove(info_file)
            except OSError:
                pass
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

def folder_dialog_title(locale):
    locale = str(locale or "zh-CN").lower().replace("_", "-")
    if locale.startswith("ja"):
        return "ClipGo のダウンロード先を選択"
    if locale.startswith("ko"):
        return "ClipGo 다운로드 폴더 선택"
    if locale in ("zh-tw", "zh-hk", "zh-mo") or locale.startswith("zh-hant"):
        return "選擇 ClipGo 下載資料夾"
    if locale.startswith("zh"):
        return "选择 ClipGo 下载文件夹"
    return "Choose the ClipGo download folder"


def pick_folder_dialog(current, locale=None):
    title = folder_dialog_title(locale)
    if IS_WIN:
        ps = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description=$env:CLIPGO_DIALOG_TITLE;"
            "$d.SelectedPath=$env:CLIPGO_CUR;"
            "if($d.ShowDialog($o) -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}"
        )
        env = dict(os.environ, CLIPGO_CUR=current, CLIPGO_DIALOG_TITLE=title)
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, env=env, creationflags=NO_WINDOW,
        )
        path = out.stdout.decode("utf-8", "replace").strip()
    else:
        script = 'POSIX path of (choose folder with prompt "%s" default location (POSIX file "%s"))' % (title, current.replace('"', '\\"'))
        out = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
        path = out.stdout.strip()
        if path != "/":
            path = path.rstrip("/")
    if out.returncode != 0 or not path:
        return None
    return path


def choose_dir(locale=None):
    path = pick_folder_dialog(download_dir(), locale)
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


def repair_paths(paths):
    """Find uniquely identifiable legacy filenames without changing any files."""
    if not isinstance(paths, list):
        return {}
    directory = os.path.abspath(load_config().get("downloadDir") or DEFAULT_DIR)
    candidates = {}
    for path in paths[:30]:
        if not isinstance(path, str) or "\ufffd" not in path or not os.path.isabs(path):
            continue
        if os.path.normcase(os.path.abspath(os.path.dirname(path))) != os.path.normcase(directory):
            continue
        match = re.search(r" \[([A-Za-z0-9_-]+)\]\.mp4$", os.path.basename(path))
        if match:
            candidates[path] = match.group(0)
    if not candidates:
        return {}
    try:
        with os.scandir(directory) as entries:
            filenames = [entry.name for entry in entries
                         if "\ufffd" not in entry.name and entry.is_file(follow_symlinks=False)]
    except OSError:
        return {}
    repaired = {}
    for path, suffix in candidates.items():
        matches = [name for name in filenames if name.endswith(suffix)]
        if len(matches) == 1:
            repaired[path] = os.path.join(directory, matches[0])
    return repaired


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
            send({"type": "error", "id": job_id, "message": "仅支持 YouTube / Instagram / 哔哩哔哩 / X / 小红书 / TikTok / 抖音 / 腾讯视频链接"})
        elif not os.path.exists(YTDLP):
            send({"type": "error", "id": job_id, "message": "未找到 yt-dlp，请重新运行安装脚本"})
        else:
            media_info = douyin_media_info(url, msg["media"], msg.get("title")) if msg.get("media") else None
            with _jobs_lock:
                _jobs[job_id] = {"cancelled": False, "proc": None}
            t = threading.Thread(
                target=run_download,
                args=(job_id, url, msg.get("quality", "1080"), msg.get("cookies") or []) + ((media_info,) if media_info else ()),
                daemon=True,
            )
            t.start()
    elif action == "cancel":
        cancel_job(msg.get("id"))
    elif action == "reveal":
        reveal(msg.get("path") or "")
        reply({"type": "ok"})
    elif action == "repairPaths":
        reply({"type": "repairedPaths", "paths": repair_paths(msg.get("paths"))})
    elif action == "openDir":
        open_dir(download_dir())
        reply({"type": "ok"})
    elif action == "chooseDir":
        reply({"type": "dir", "downloadDir": choose_dir(msg.get("locale")) or download_dir()})
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
