"""Tencent's normal browser login, metadata, and complete-video checks.

Cookie mapping follows Tencent's own player (Yp/Mp, useVCookie=true):
https://vm.gtimg.cn/thumbplayer/superplayer/1.80.0/superplayer-txv-light.js
Only VQQVideoIE is extended. Upstream client/DRM parameters are unchanged.
Ordinary identity AES-128 HLS is supported by yt-dlp's standard downloader:
https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/yt_dlp/downloader/hls.py
"""

import json
import math
import re
from urllib.parse import unquote_plus, urlsplit

from yt_dlp.downloader.hls import HlsFD
from yt_dlp.extractor.tencent import VQQVideoIE as _VQQVideoIE
from yt_dlp.utils import ExtractorError, parse_m3u8_attributes

__all__ = []  # This replaces VQQVideoIE; it is not another URL extractor.


def _login_token(cookies):
    """Read site-scoped cookies without broadening their cookie-jar domains."""
    def value(name):
        cookie = cookies.get(name)
        if cookie is None:
            return ''
        raw = cookie.value
        # Tencent's cookie utility parses quoted values and percent encoding
        # before it constructs logintoken (parseValue in the player source).
        if raw.startswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        if re.search(r'%(?![0-9a-fA-F]{2})', raw):
            return ''
        try:
            return unquote_plus(raw, errors='strict')
        except UnicodeError:
            return ''

    old_login = value('main_login')
    old_prefix = 'vqq_' if old_login == 'qq' else ''
    token = {
        name: value(modern) or (value(old_prefix + name) if old_login else '')
        for name, modern in (
            ('access_token', 'v_t_access_token'),
            ('appid', 'v_t_appid'),
            ('vusession', 'v_vusession'),
            ('openid', 'v_t_openid'),
            ('vuserid', 'v_vuserid'),
        )
    }
    token['main_login'] = value('v_main_login') or old_login
    token['video_guid'] = value('video_guid')
    return token if token['main_login'] else None


def _positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _manifest_summary(document):
    """Inspect only playlist text; never request segments or encryption keys."""
    if not isinstance(document, str) or not document.lstrip('\ufeff').startswith('#EXTM3U'):
        return None
    durations = [
        _positive_number(value)
        for value in re.findall(r'^#EXTINF:([^,\r\n]+)', document, re.MULTILINE)
    ]
    # Fail closed on a truncated or malformed VOD playlist. A master playlist
    # has no EXTINF; its selected media playlists will be checked individually.
    duration = None
    if durations and all(durations) and '#EXT-X-ENDLIST' in document:
        duration = sum(durations)
    def unsupported_key(attributes):
        method = attributes.get('METHOD')
        if method not in ('NONE', 'AES-128') or attributes.get('KEYFORMAT', 'identity') != 'identity':
            return True
        uri = attributes.get('URI')
        if not uri:
            return method != 'NONE'
        try:
            parsed = urlsplit(uri)
            return (bool(re.search(r'\s', uri)) or parsed.scheme not in ('', 'http', 'https')
                    or bool(parsed.username or parsed.password)
                    or bool((parsed.scheme or parsed.netloc) and not parsed.hostname))
        except ValueError:
            return True

    # AES-128 with identity keys uses the engine's ordinary HLS handling. Only
    # inspect the declaration here: do not fetch keys or alter DRM parameters.
    protected = HlsFD._has_drm(document) or any(
        unsupported_key(parse_m3u8_attributes(attributes))
        for attributes in re.findall(r'^#EXT-X-(?:SESSION-)?KEY:([^\r\n]*)', document, re.MULTILINE)
    )
    return {'duration': duration, 'protected': protected}


class _ClipGoVQQVideoIE(_VQQVideoIE, plugin_name='clipgo'):
    def _download_webpage(self, url_or_request, video_id, *args, **kwargs):
        # The browser's v.qq.com cookies do not domain-match video.qq.com.
        # The first-party player intentionally sends these fields as logintoken.
        # Do not copy Cookie headers to a CDN or change cookie domain scope.
        if url_or_request == self._API_URL:
            token = _login_token(self._get_cookies('https://v.qq.com/'))
            if token:
                kwargs['query'] = {
                    **(kwargs.get('query') or {}),
                    'logintoken': json.dumps(token, separators=(',', ':')),
                }
                self._clipgo_login_sent = True
                # Request errors can contain their URL. Do not let a login
                # query escape into the host log or the extension's task error.
                try:
                    return super()._download_webpage(url_or_request, video_id, *args, **kwargs)
                except Exception:
                    pass
                # Construct outside the handler: ExtractorError otherwise
                # preserves sys.exc_info even when raised "from None".
                raise ExtractorError(
                    'Tencent video API request failed; please retry with your current '
                    'browser login.', expected=True) from None
        return super()._download_webpage(url_or_request, video_id, *args, **kwargs)

    def _get_video_api_response(self, *args, **kwargs):
        response = super()._get_video_api_response(*args, **kwargs)
        videos = (response.get('vl') or {}).get('vi') or []
        if videos and isinstance(videos[0], dict):
            video = videos[0]
            title = video.get('ti')
            if isinstance(title, str) and title.strip():
                self._clipgo_title = title.strip()
            durations = filter(None, (
                _positive_number(video.get('td')),
                getattr(self, '_clipgo_duration', None),
            ))
            self._clipgo_duration = max(durations, default=None)
        return response

    def _parse_m3u8_formats_and_subtitles(self, document, m3u8_url=None, *args, **kwargs):
        if not hasattr(self, '_clipgo_manifests'):
            self._clipgo_manifests = {}
        if not hasattr(self, '_clipgo_protected_urls'):
            self._clipgo_protected_urls = set()
        summary = _manifest_summary(document)
        self._clipgo_manifests[m3u8_url] = summary
        formats, subtitles = super()._parse_m3u8_formats_and_subtitles(
            document, m3u8_url, *args, **kwargs)
        # Upstream Tencent currently overwrites the HLS parser's has_drm flag
        # with a format-level API flag. Retain manifest evidence separately.
        self._clipgo_protected_urls.update(
            fmt['url'] for fmt in formats
            if fmt.get('has_drm') or (summary and summary['protected']))
        return formats, subtitles

    def _media_manifest(self, url, video_id, headers=None):
        cache = self._clipgo_manifests
        if url not in cache:
            document = self._download_webpage(
                url, video_id, note='Checking Tencent video duration',
                errnote='Unable to verify Tencent video duration',
                fatal=False, headers=headers or {})
            cache[url] = _manifest_summary(document)
        return cache[url]

    def _extract_video_formats_and_subtitles(self, api_response, video_id):
        formats, subtitles = super()._extract_video_formats_and_subtitles(api_response, video_id)
        accepted = []
        expected = getattr(self, '_clipgo_duration', None)
        for video_format in formats:
            if video_format.get('has_drm') or video_format.get('url') in self._clipgo_protected_urls:
                self._clipgo_protected = True
                continue
            if str(video_format.get('protocol', '')).startswith('m3u8'):
                manifest = self._media_manifest(
                    video_format['url'], video_id, video_format.get('http_headers'))
                if manifest and manifest['protected']:
                    self._clipgo_protected = True
                    continue
                actual = manifest and manifest['duration']
                if expected and not actual:
                    self._clipgo_unverified = True
                    continue
                # Allow small rounding/edit differences, but reject a preview
                # even when the API advertises the full episode's duration.
                if expected and actual + max(10, expected * 0.02) < expected:
                    self._clipgo_preview_duration = max(
                        getattr(self, '_clipgo_preview_duration', 0), actual)
                    continue
            accepted.append(video_format)
        return accepted, subtitles

    def _real_extract(self, url):
        self._clipgo_manifests = {}
        self._clipgo_protected_urls = set()
        self._clipgo_title = None
        self._clipgo_duration = None
        self._clipgo_preview_duration = 0
        self._clipgo_protected = False
        self._clipgo_unverified = False
        self._clipgo_login_sent = False
        result = super()._real_extract(url)
        login_state = 'present' if self._clipgo_login_sent else 'absent'
        if not result.get('formats'):
            if self._clipgo_preview_duration:
                raise ExtractorError(
                    'CLIPGO_TENCENT_PREVIEW: Tencent returned only a preview '
                    f'({self._clipgo_preview_duration:.0f}s of {self._clipgo_duration:.0f}s). '
                    'The current login did not provide a downloadable full video; '
                    f'the preview was not saved. (login={login_state})', expected=True)
            if self._clipgo_protected:
                raise ExtractorError(
                    'Tencent returned only protected/encrypted video formats; '
                    f'a supported non-DRM video was not provided. (login={login_state})', expected=True)
            if self._clipgo_unverified:
                raise ExtractorError(
                    'CLIPGO_TENCENT_UNVERIFIED: Unable to verify the complete Tencent video duration; '
                    'no partial video was saved. Please retry.', expected=True)
        if self._clipgo_title:
            result['title'] = self._clipgo_title
        if self._clipgo_duration:
            result['duration'] = self._clipgo_duration
        return result
