"""Extract only complete media JSON objects beside an observed Channels URL.

This module does not read process memory. Callers supply a bounded buffer from
an independently selected renderer. Unstructured nearby keys are never paired
with a URL, and surrounding data is neither returned nor retained.
"""
from __future__ import annotations

import json
import re
import time

from .capture import _decode_key, _media_url, _text, media_identity
from .engagement import engagement_from_feed, merge_engagement, normalize_engagement

NEIGHBORHOOD_BYTES = 16 * 1024
MAX_MATCHES = 1000
_URL = re.compile(r'https://finder\.video\.qq\.com/[^\x00-\x20"<>\\]{8,8192}')
_NEEDLE = b'https://finder.video.qq.com/'
_JSON = json.JSONDecoder()


def _decoded_prefix(data: bytes, encoding: str) -> str:
    """Discard bytes after the first invalid character, without repairing JSON."""
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        return data[:exc.start].decode(encoding)


def _paired_objects(obj: object, exact_url: str, now: float) -> list[dict]:
    result = []
    pending = [(obj, '', '', 0, False, None)]
    visited = 0
    while pending and visited < 1000:
        current, title, author, depth, media_context, engagement = pending.pop()
        visited += 1
        if depth > 12:
            continue
        if isinstance(current, list):
            pending.extend((v, title, author, depth + 1, media_context, engagement) for v in current[:100])
        elif isinstance(current, dict):
            desc = current.get('objectDesc') or current.get('object_desc')
            if isinstance(desc, dict):
                title = _text(desc.get('description'), 500) or title
                engagement = engagement_from_feed(current)
            elif isinstance(current.get('media'), list):
                title = _text(current.get('description'), 500) or title
            contact = current.get('contact')
            if isinstance(contact, dict):
                author = _text(contact.get('nickname'), 120) or author
            # Identity and key must be properties of exactly this same object.
            key = _decode_key(current.get('decodeKey', current.get('decode_key')))
            has_media_shape = media_context or isinstance(current.get('urlToken', current.get('url_token')), str)
            if current.get('url') == exact_url and (key is not None or has_media_shape):
                media = dict(current)
                if 'url_token' in media and 'urlToken' not in media:
                    media['urlToken'] = media['url_token']
                url = _media_url(media)
                if url:
                    result.append({
                        'id': media_identity(url), 'url': url, 'decode_key': key,
                        'title': title or '视频号视频', 'author': author,
                        'engagement': normalize_engagement(engagement),
                        'source': 'memory_metadata', 'seen_at': now,
                    })
            pending.extend((v, title, author, depth + 1, name == 'media' and isinstance(v, list),
                            engagement if name in ('objectDesc', 'object_desc', 'media') else None)
                           for name, v in list(current.items())[:100]
                           if isinstance(v, (dict, list)))
    return result


def extract_media_metadata(data: bytes, known_urls: set[str] | None = None,
                           now: float | None = None) -> list[dict]:
    """Return allowlisted URL/key pairs inside complete, bounded JSON objects.

    Parsing stays within 16 KiB surrounding each exact Channels URL. UTF-8 and
    UTF-16LE serialized JSON are supported. No candidate is returned for V8
    object properties stored as disconnected native strings. A caller scanning
    multiple chunks can overlap 32 KiB and deduplicate by the returned id.
    """
    timestamp = time.time() if now is None else now
    found = {}
    matches = 0
    for encoding, needle, width in (
        ('utf-8', _NEEDLE, 1),
        ('utf-16le', _NEEDLE.decode().encode('utf-16le'), 2),
    ):
        at = 0
        while matches < MAX_MATCHES:
            hit = data.find(needle, at)
            if hit < 0:
                break
            matches += 1
            at = hit + len(needle)
            start = max(0, hit - NEIGHBORHOOD_BYTES // 2)
            start += (hit - start) % width
            stop = min(len(data), hit + NEIGHBORHOOD_BYTES // 2)
            stop -= (stop - start) % width
            # Start decoding at the observed URL, so arbitrary preceding native
            # bytes cannot prevent finding the URL itself.
            url_text = _decoded_prefix(data[hit:stop], encoding)
            url_match = _URL.match(url_text)
            if not url_match:
                continue
            exact = url_match.group()
            if known_urls is not None and exact not in known_urls:
                continue
            brace = '{'.encode(encoding)
            positions = []
            pos = data.find(brace, start, hit)
            while pos >= 0:
                if (hit - pos) % width == 0:
                    positions.append(pos)
                pos = data.find(brace, pos + width, hit)
            for pos in positions[-64:]:
                text = _decoded_prefix(data[pos:stop], encoding)
                try:
                    obj, end = _JSON.raw_decode(text)
                except (ValueError, RecursionError):
                    continue
                if exact not in text[:end]:
                    continue
                for candidate in _paired_objects(obj, exact, timestamp):
                    ident = candidate['id']
                    old = found.get(ident)
                    if old is None or old['title'] == '视频号视频':
                        found[ident] = candidate
                    if old is not None:
                        found[ident]['engagement'] = merge_engagement(old.get('engagement'), candidate.get('engagement'))
    return list(found.values())
