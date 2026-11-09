"""Extract bounded Channels media URL strings without interpreting nearby data.

Chromium strings may be one-byte, UTF-16LE, or serialized JSON. This module
does not read processes, issue requests, pair decoding keys, or persist input.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlsplit

from .capture import valid_media_url

MAX_URL = 16384
MAX_MATCHES = 1000
_PREFIX = 'https://finder.video.qq.com/'
_ALLOWED = re.escape(bytes(c for c in range(33, 127) if c not in b'"\'<>\\`{}[]'))
_ASCII = re.compile(re.escape(_PREFIX.encode()) + b'[' + _ALLOWED + b']{1,16384}(?=[^' + _ALLOWED + br'\\])')
_WIDE = re.compile(re.escape(_PREFIX.encode('utf-16le')) + br'(?:[' + _ALLOWED + br']\x00){1,16384}'
                   + br'(?=(?:[^' + _ALLOWED + br'\\]\x00|[\s\S][\x01-\xff]))')
_JSON = re.compile(rb'"https:(?:\\/|/){2}finder\.video\.qq\.com(?:\\/|/)(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4}){1,32768}"')
_WIDE_JSON_PREFIX = '"https:'.encode('utf-16le')
_DECODER = json.JSONDecoder()


def usable_video_url(url: object, *, require_token: bool = True) -> bool:
    if not isinstance(url, str) or len(url) > MAX_URL:
        return False
    if not valid_media_url(url) or any(ord(c) < 33 or ord(c) > 126 for c in url):
        return False
    parsed = urlsplit(url)
    if not (parsed.path.lower().endswith('/stodownload') or parsed.path.lower().endswith('.mp4')):
        return False
    params = {key.lower(): value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    if {'picformat', 'wxampicformat'} & params.keys():
        return False
    return not require_token or bool(params.get('token'))


def extract_loaded_urls(data: bytes, *, require_token: bool = True) -> list[str]:
    """Return complete URL strings; defer unterminated chunk tails.

    ASCII strings can end at native non-ASCII bytes as well as NUL/quotes.
    JSON escapes are decoded only from a complete quoted JSON string. Nothing
    from adjacent properties is combined with a URL.
    """
    found = {}

    def keep(value):
        if len(found) < MAX_MATCHES and usable_video_url(value, require_token=require_token):
            found[value] = None

    for pattern, encoding in ((_ASCII, 'ascii'), (_WIDE, 'utf-16le')):
        for number, match in enumerate(pattern.finditer(data)):
            if number >= MAX_MATCHES:
                break
            keep(match.group().decode(encoding))
    for number, match in enumerate(_JSON.finditer(data)):
        if number >= MAX_MATCHES:
            break
        try:
            keep(json.loads(match.group()))
        except (ValueError, UnicodeError):
            continue
    start = 0
    for _ in range(MAX_MATCHES):
        hit = data.find(_WIDE_JSON_PREFIX, start)
        if hit < 0:
            break
        start = hit + len(_WIDE_JSON_PREFIX)
        encoded = data[hit:hit + 65536]
        encoded = encoded[:len(encoded) // 2 * 2]
        try:
            text = encoded.decode('utf-16le')
        except UnicodeDecodeError as exc:
            text = encoded[:exc.start].decode('utf-16le')
        try:
            value, _end = _DECODER.raw_decode(text)
        except (ValueError, RecursionError):
            continue
        keep(value)
    return list(found)
