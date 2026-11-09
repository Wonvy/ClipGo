"""Download an observed Channels URL without exporting session credentials."""
from __future__ import annotations

from http.client import HTTPException
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .decoder import decode_prefix


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    pass


def validate_url(url: str) -> str:
    try:
        p = urlsplit(url)
        valid = (p.scheme == 'https' and p.hostname == 'finder.video.qq.com'
                 and p.port in (None, 443) and not p.username and not p.password
                 and not p.fragment and not any(ord(c) < 33 for c in url))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DownloadError('不是受支持的视频号媒体地址。请重新播放视频后捕获。')
    return url


class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title))[:90].strip(' .')
    if not name:
        name = '视频号视频'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    return name


def validate_mp4(path: Path) -> None:
    """Walk top-level boxes; reject HTML, partial ranges and missing movie data."""
    size = path.stat().st_size
    boxes = set()
    with path.open('rb') as f:
        offset = 0
        while offset < size:
            f.seek(offset)
            header = f.read(8)
            if len(header) != 8:
                raise DownloadError('视频文件不完整，请重新播放并捕获。')
            length = int.from_bytes(header[:4], 'big')
            kind = header[4:8]
            minimum = 8
            if length == 1:
                extra = f.read(8)
                if len(extra) != 8:
                    raise DownloadError('视频文件不完整。')
                length = int.from_bytes(extra, 'big')
                minimum = 16
            elif length == 0:
                length = size - offset
            if length < minimum or offset + length > size:
                raise DownloadError('视频文件校验失败，可能缺少解码信息或内容不完整。')
            boxes.add(kind)
            offset += length
    if not {b'ftyp', b'moov', b'mdat'}.issubset(boxes):
        raise DownloadError('未得到完整 MP4；当前视频格式或加密方式暂不支持。')


def download_video(candidate: dict, folder: Path, cancel: threading.Event, progress=lambda n, total: None) -> Path:
    options = [candidate]
    for option in candidate.get('media_options', [])[:3]:
        options.append(dict(candidate, url=option.get('url'), decode_key=option.get('decode_key')))
    attempted = set()
    error = None
    for option in options:
        identity = (option.get('url'), str(option.get('decode_key')))
        if identity in attempted:
            continue
        attempted.add(identity)
        try:
            return _download_one(option, folder, cancel, progress)
        except Cancelled:
            raise
        except DownloadError as exc:
            error = exc
    raise error or DownloadError('没有可用的视频地址。')


def _download_one(candidate: dict, folder: Path, cancel: threading.Event, progress=lambda n, total: None) -> Path:
    url = validate_url(candidate.get('url', ''))
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:10]
    target = folder / f"{safe_name(candidate.get('title') or '视频号视频')}_{token}.mp4"
    partial = target.with_suffix('.mp4.part')
    # Do not send browser cookies or use a system proxy for this independent transfer.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), MediaRedirect())
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity'})
    try:
        if cancel.is_set():
            raise Cancelled('已取消下载')
        with opener.open(req, timeout=20) as response, partial.open('xb') as out:
            if response.status != 200:
                raise DownloadError('服务器没有返回完整视频，请重新播放后捕获。')
            total = int(response.headers.get('Content-Length', '0'))
            prefix = response.read(131072)
            if cancel.is_set():
                raise Cancelled('已取消下载')
            if len(prefix) < 12:
                raise DownloadError('服务器未返回视频内容。')
            if prefix[4:8] != b'ftyp':
                key = candidate.get('decode_key')
                if key is None or key == '':
                    raise DownloadError('已捕获地址，但缺少视频解码信息。此条暂时不能下载。')
                try:
                    prefix = decode_prefix(prefix, str(key))
                except ValueError as exc:
                    raise DownloadError('视频解码失败，请重新播放后捕获。') from exc
                if prefix[4:8] != b'ftyp':
                    raise DownloadError('视频地址与解码信息不匹配。请重新播放，再读取并下载。')
            out.write(prefix)
            received = len(prefix)
            progress(received, total)
            while True:
                if cancel.is_set():
                    raise Cancelled('已取消下载')
                block = response.read(256 * 1024)
                if not block:
                    break
                out.write(block)
                received += len(block)
                progress(received, total)
            if total and received != total:
                raise DownloadError('视频下载不完整，请重试。')
        if cancel.is_set():
            raise Cancelled('已取消下载')
        validate_mp4(partial)
        os.replace(partial, target)
        return target
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404, 410):
            raise DownloadError('视频地址已失效或不再被服务器接受。请在对应微信窗口重新播放，再读取并下载。') from exc
        raise DownloadError('视频服务器暂时没有返回可下载文件，请稍后重试。') from exc
    except (urllib.error.URLError, HTTPException, OSError, ValueError) as exc:
        # Signed URLs may appear in exception messages; never surface them.
        raise DownloadError('下载连接失败或无法保存文件，请检查网络和保存位置后重试。') from exc
    finally:
        partial.unlink(missing_ok=True)
