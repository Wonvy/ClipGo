"""Small, fail-closed page transforms for the opt-in Channels page button.

This module is pure: it has no networking, process access or certificate side
effects. The matching names are WeChat's public page resource/property names;
the implementation and injected UI are local to ClipGo.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

_IDENT = r'[A-Za-z_$][\w$]*'
_BASE = re.compile(r'/__clipgo/[A-Za-z0-9_-]{24,128}\Z')
_HEAD = re.compile(r'<head\b[^>]*>', re.IGNORECASE)
_SCRIPT = re.compile(r'(<script\b[^>]*\bsrc\s*=\s*)([\"\'])([^\"\']+)(\2)', re.IGNORECASE)
_PRELOAD = re.compile(r'(<link\b[^>]*\bhref\s*=\s*)([\"\'])([^\"\']+)(\2)', re.IGNORECASE)
_NONCE = re.compile(r'<script\b[^>]*\bnonce\s*=\s*([\"\'])([^\"\']+)\1', re.IGNORECASE)
_MARKER = 'data-clipgo-page-entry'
_METHOD = re.compile(r'\basync\s+finderGetCommentDetail\s*\([^{};]*\)\s*\{')
_PROPERTY = re.compile(r'(?<=[{,])\s*(flowTab|localFlowTab|goToNextFlowFeed|goToPrevFlowFeed|loadLocalPlaylist)\s*:\s*(' + _IDENT + r')(?=\s*[,}])')


def _tag_url(value: str, tag: str) -> str:
    """Only cache-bust JavaScript assets; never alter a media URL."""
    if not tag or not re.fullmatch(r'[a-f0-9]{12}', tag):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme and parsed.scheme != 'https':
        return value
    if parsed.netloc and parsed.hostname not in ('res.wx.qq.com', 'channels.weixin.qq.com'):
        return value
    if not parsed.path.endswith('.js') or parsed.fragment:
        return value
    if '_clipgo' in parse_qs(parsed.query):
        return value
    query = parsed.query + ('&' if parsed.query else '') + urlencode({'_clipgo': tag})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ''))


def rewrite_html(text: str, config: dict) -> str | None:
    """Add one blocking same-origin bootstrap to a matched home/feed page.

    The proxy is responsible for exact host/path matching before this function.
    A missing <head> or an invalid session path leaves the response untouched.
    No inline JavaScript/configuration is added, making existing nonce policies
    usable without widening them to unsafe-inline.
    """
    base = config.get('basePath', config.get('base')) if isinstance(config, dict) else None
    if not isinstance(base, str) or not _BASE.fullmatch(base):
        raise ValueError('Invalid page session path')
    if not isinstance(text, str) or _MARKER in text or not _HEAD.search(text):
        return None
    tag = hashlib.sha256(base.encode('ascii')).hexdigest()[:12]
    nonce_match = _NONCE.search(text)
    nonce = f' nonce="{html.escape(html.unescape(nonce_match[2]), quote=True)}"' if nonce_match else ''
    entry = (f'<script {_MARKER}="1" data-clipgo-base="{base}"'
             f' data-clipgo-version="1" charset="utf-8" src="{base}/embed.js"{nonce}></script>')
    def rewrite_src(match):
        old = html.unescape(match[3])
        new = _tag_url(old, tag)
        return match[0] if new == old else match[1] + match[2] + html.escape(new, quote=True) + match[4]
    result = _SCRIPT.sub(rewrite_src, text)
    result = _PRELOAD.sub(rewrite_src, result)
    return _HEAD.sub(lambda match: match[0] + entry, result, count=1)


def _mask_literals(source: str) -> str:
    """Preserve offsets while excluding strings/comments from property matches.

    We skip template literals as a whole. Hooking inside template substitutions
    is unnecessary for the exported store properties we match. A bounded scan
    is preferable to evaluating or reparsing third-party application code.
    """
    result = list(source)
    index, size = 0, len(source)
    previous = ''
    while index < size:
        start = index
        char = source[index]
        if char in "\"'`":
            quote = char
            index += 1
            while index < size:
                if source[index] == '\\':
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
        elif source.startswith('//', index):
            end = source.find('\n', index + 2)
            index = size if end < 0 else end
        elif source.startswith('/*', index):
            end = source.find('*/', index + 2)
            index = size if end < 0 else end + 2
        elif char == '/' and (previous in ('', 'return', 'throw', 'case', 'yield', 'await', 'typeof', 'void', 'delete')
                              or previous in '=(:,[!&|?{};'):
            # A regex literal may contain braces or fake property names. This
            # distinction is only needed at expression starts; division keeps
            # its ordinary tokens and cannot introduce object properties.
            index += 1
            character_class = False
            while index < size:
                current = source[index]
                if current == '\\':
                    index += 2
                    continue
                if current == '[':
                    character_class = True
                elif current == ']':
                    character_class = False
                elif current == '/' and not character_class:
                    index += 1
                    while index < size and source[index].isalpha():
                        index += 1
                    break
                elif current in '\r\n':
                    break
                index += 1
            previous = 'literal'
        elif char.isalpha() or char in '_$':
            index += 1
            while index < size and (source[index].isalnum() or source[index] in '_$'):
                index += 1
            previous = source[start:index]
            continue
        else:
            if not char.isspace():
                previous = char
            index += 1
            continue
        for position in range(start, min(index, size)):
            if result[position] not in '\r\n':
                result[position] = ' '
        if char in "\"'`":
            previous = 'literal'
    return ''.join(result)


def _body_end(mask: str, opening: int) -> int | None:
    depth = 0
    for position in range(opening, len(mask)):
        if mask[position] == '{':
            depth += 1
        elif mask[position] == '}':
            depth -= 1
            if depth == 0:
                return position
    return None


def _property_groups(mask: str):
    """Group aliases by their object value; never rewrite destructuring binds."""
    matches = iter(_PROPERTY.finditer(mask))
    pending = next(matches, None)
    if pending is None:
        return {}
    groups, stack = {}, []
    for position, character in enumerate(mask):
        if position == pending.start():
            if stack and stack[-1][1]:
                groups.setdefault(stack[-1][0], []).append(pending)
            pending = next(matches, None)
            if pending is None:
                break
        if (stack and mask.startswith('return', position) and
                (position == 0 or not (mask[position - 1].isalnum() or mask[position - 1] in '_$')) and
                (position + 6 == len(mask) or not (mask[position + 6].isalnum() or mask[position + 6] in '_$'))):
            stack[-1][2] = True
        elif character == ';' and stack:
            stack[-1][2] = False
        if character == '{':
            prefix = mask[max(0, position - 100):position].rstrip()
            literal = bool(re.search(r'(?:=|\breturn|\breturn\s*\(|=>\s*\()\s*$', prefix))
            if stack and stack[-1][1] and prefix.endswith(':'):
                literal = True
            # Bundled Pinia stores often return a comma expression:
            # return watch(...), { flowTab: selected, ... }.
            # Keep the return context on its block while nested callbacks are
            # scanned, instead of guessing from the last few source bytes.
            if stack and stack[-1][2] and prefix.endswith(','):
                literal = True
            stack.append([position, literal, False])
        elif character == '}' and stack:
            stack.pop()
    return groups


def rewrite_script(text: str, url: str = '') -> str | None:
    """Hook explicit selected-feed state without replacing networking methods.

    Every helper is guarded so a cached module also runs without the ClipGo
    bootstrap. Original functions retain their receiver, arguments, result and
    rejection behavior; the page helper observes completion separately.
    """
    if not isinstance(text, str) or len(text) > 12 * 1024 * 1024:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if url and (parsed.scheme != 'https' or parsed.hostname != 'res.wx.qq.com'):
        return None
    if '/*clipgo-page-hooks-v1*/' in text:
        return None
    mask = _mask_literals(text)
    changes = []
    for properties in _property_groups(mask).values():
        references = {match[1]: match[2] for match in properties if match[1] in ('flowTab', 'localFlowTab')}
        for match in properties:
            name, identifier = match[1], match[2]
            start, end = match.span(2)
            if name in ('flowTab', 'localFlowTab'):
                expression = f'globalThis.__clipgoPage.track({identifier},{json.dumps(name)})'
            else:
                reference = references.get('localFlowTab' if name == 'loadLocalPlaylist' else 'flowTab')
                if not reference:
                    continue
                expression = f'globalThis.__clipgoPage.wrap({identifier},()=>{reference},{json.dumps(name)})'
            changes.append((start, end, f'(globalThis.__clipgoPage?{expression}:{identifier})'))

    # A detail response is a single selected object, unlike recommendation /
    # prefetch lists. Do not treat the newest observed list item as the current
    # video. The selected flow index / actual slide has higher authority.
    for match in _METHOD.finditer(mask):
        opening = match.end() - 1
        ending = _body_end(mask, opening)
        if ending is None:
            continue
        body = text[opening + 1:ending]
        if '__clipgoResult' in body or any(start >= opening and start < ending for start, _, _ in changes):
            continue
        replacement = ('const __clipgoResult=await(async()=>{' + body + '})();'
                       'try{globalThis.__clipgoPage&&globalThis.__clipgoPage.detail(__clipgoResult)}catch{};'
                       'return __clipgoResult;')
        changes.append((opening + 1, ending, replacement))

    result = text
    for start, end, value in sorted(changes, reverse=True):
        result = result[:start] + value + result[end:]
    tags = parse_qs(parsed.query).get('_clipgo', [])
    tag = tags[0] if tags else ''
    if re.fullmatch(r'[a-f0-9]{12}', tag):
        # Import specifiers and Vite dependency tables are resource URLs only.
        # Do not rewrite arbitrary strings, JSON, or video request parameters.
        pattern = re.compile(r'((?:\bfrom\s*|\bimport\s*\(?\s*)([\"\']))([^\"\']+)(\2)')
        result = pattern.sub(lambda m: m[1] + _tag_url(m[3], tag) + m[4], result)
        pattern = re.compile(r'([\"\'])(js/[^\"\'\s]+\.js)(\1)')
        result = pattern.sub(lambda m: m[1] + _tag_url(m[2], tag) + m[3], result)
    if result == text:
        return None
    return ('/*clipgo-page-hooks-v1*/' if changes else '') + result
