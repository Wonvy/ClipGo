"""Load already-paired Channels covers into bounded in-memory Pillow images.

Only finder.video.qq.com has been observed in the paired media metadata. No
cookies, system proxy, arbitrary image URLs, redirects or disk cache are used.
"""
from __future__ import annotations

from collections import OrderedDict
import io
import threading
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


THUMBNAIL_HOSTS = frozenset({'finder.video.qq.com'})
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 10_000_000
TIMEOUT_SECONDS = 8
MAX_SIZE = (224, 288)
MAX_CACHE_ITEMS = 32
_FORMATS = frozenset({'JPEG', 'PNG', 'WEBP', 'GIF'})
_cache: OrderedDict[tuple[str, tuple[int, int]], Image.Image] = OrderedDict()
_cache_lock = threading.Lock()


class ThumbnailError(Exception):
    """A user-safe failure; caller may show its normal placeholder."""


def valid_thumbnail_url(url: object) -> bool:
    if not isinstance(url, str) or not url or len(url) > 16384:
        return False
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname in THUMBNAIL_HOSTS
                and parsed.port in (None, 443) and parsed.username is None
                and parsed.password is None and not parsed.fragment
                and bool(parsed.path))
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ThumbnailError('封面地址发生跳转，已跳过。')


def clear_thumbnail_cache():
    with _cache_lock:
        _cache.clear()


def _read_image_bytes(response, deadline: float) -> bytes:
    if response.status != 200:
        raise ThumbnailError('封面响应不完整。')
    try:
        declared = response.headers.get('Content-Length')
        length = int(declared) if declared is not None else None
    except (TypeError, ValueError):
        raise ThumbnailError('封面响应无效。') from None
    if length is not None and (length < 0 or length > MAX_BYTES):
        raise ThumbnailError('封面文件过大，已跳过。')
    # Identity avoids a small transfer expanding beyond the input bound before
    # Pillow's own compressed-image dimensions can be checked.
    encoding = response.headers.get('Content-Encoding', 'identity').strip().lower()
    if encoding not in ('', 'identity'):
        raise ThumbnailError('封面传输格式暂不支持。')
    chunks = bytearray()
    read = getattr(response, 'read1', response.read)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ThumbnailError('封面读取超时。')
        # HTTPResponse retains the socket here; shrinking its timeout also
        # bounds a slow final read to the remaining request budget.
        sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if sock is not None:
            sock.settimeout(remaining)
        block = read(min(64 * 1024, MAX_BYTES + 1 - len(chunks)))
        if not block:
            break
        chunks.extend(block)
        if len(chunks) > MAX_BYTES:
            raise ThumbnailError('封面文件过大，已跳过。')
    if length is not None and len(chunks) != length:
        raise ThumbnailError('封面响应不完整。')
    return bytes(chunks)


def _decode_image(data: bytes, max_size: tuple[int, int]) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in _FORMATS:
                    raise ThumbnailError('封面图片格式暂不支持。')
                width, height = original.size
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise ThumbnailError('封面分辨率过大，已跳过。')
                # Only the first frame is used. EXIF orientation is honored.
                original.seek(0)
                original.load()
                oriented = ImageOps.exif_transpose(original)
                result = oriented.convert('RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB')
                result.thumbnail(max_size, Image.Resampling.LANCZOS)
                return result
    except ThumbnailError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ThumbnailError('封面图片无效或无法解码。') from None


def load_thumbnail(thumbnail_url: str, max_size: tuple[int, int] = MAX_SIZE) -> Image.Image:
    """Return a detached Pillow image fitted inside max_size, never upscaled.

    Call from a worker thread. The URL must come from the corresponding media
    object's thumbnail_url; this module never guesses covers from video URLs.
    """
    if not valid_thumbnail_url(thumbnail_url):
        raise ThumbnailError('封面地址不受支持。')
    if (not isinstance(max_size, (tuple, list)) or len(max_size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in max_size)
            or max_size[0] > MAX_SIZE[0] or max_size[1] > MAX_SIZE[1]):
        raise ValueError('Thumbnail size must fit inside 224 × 288.')
    size = tuple(max_size)
    cache_key = (thumbnail_url, size)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            _cache.move_to_end(cache_key)
            return cached.copy()
    request = urllib.request.Request(thumbnail_url, headers={
        'User-Agent': 'Mozilla/5.0', 'Accept': 'image/jpeg,image/png,image/webp,image/gif',
        'Accept-Encoding': 'identity',
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            data = _read_image_bytes(response, deadline)
        image = _decode_image(data, size)
    except ThumbnailError:
        raise
    except (urllib.error.URLError, OSError, ValueError):
        # URL-bearing urllib errors must never become UI or log messages.
        raise ThumbnailError('暂时无法读取封面。') from None
    with _cache_lock:
        _cache[cache_key] = image.copy()
        _cache.move_to_end(cache_key)
        while len(_cache) > MAX_CACHE_ITEMS:
            _cache.popitem(last=False)
    return image
