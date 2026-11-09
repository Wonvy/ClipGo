"""Read narrowly bound Channels media fields from a supported V8 heap layout.

This is a read-only, fail-closed parser for the compressed-pointer layout
observed in the Windows Channels renderer. It is not a general JS serializer.
It follows decodeKey -> DescriptorArray -> Map -> same media object, retaining
only URL/token/key and media duration. A matching key still needs validation
against the downloaded MP4 header; object structure alone is not that proof.

Layout references (V8 BSD source): src/objects/{map,descriptor-array,string}.tq
and src/objects/property-details.h. No source implementation is vendored here.
"""
from __future__ import annotations

import ctypes
import re
import struct
import time
import threading

from .capture import _decode_key, _media_url, valid_media_url
from .engagement import FEED_COUNT_FIELDS, engagement_from_feed, merge_engagement

MAP_TYPE = 277
DESCRIPTOR_TYPE = 253
JS_OBJECT_TYPE = 1057
PROPERTY_ARRAY_TYPE = 282
JS_ARRAY_TYPE = 2119
FIXED_ARRAY_TYPE = 205
MAX_DESCRIPTORS = 128
MEDIA_FIELDS = frozenset({'url', 'urlToken', 'decodeKey', 'mediaType', 'videoPlayLen',
                          'thumbUrl', 'coverUrl'})
FIELDS = MEDIA_FIELDS | {'objectDesc', 'contact', 'description', 'media', 'nickname'} | set(FEED_COUNT_FIELDS.values())
# Addresses and field structure only; never cached URLs, keys or media bytes.
_HINTS = {}
_HINTS_LOCK = threading.Lock()
SCHEMA_REFRESH_SECONDS = 30


def _hint_key(process):
    try:
        return (int(process['pid']), int(process['created']), str(process['path']).casefold())
    except (KeyError, TypeError, ValueError):
        return None


ANCHORS = {'decodeKey': frozenset({'url', 'decodeKey'}),
           'objectDesc': frozenset({'objectDesc', 'contact'})}


def bound_thumbnail(attrs):
    """Observed cover fields contain their own full signature, not video tokens."""
    for field in ('thumbUrl', 'coverUrl'):
        url = attrs.get(field)
        if valid_media_url(url) and not any(ord(char) < 33 for char in url):
            return url
    return ''


def word(data, offset=0):
    return int.from_bytes(data[offset:offset + 4], 'little')


def aligned_matches(pattern, data, base):
    """Do not let an unaligned byte match hide a valid overlapping pointer."""
    position = 0
    while match := pattern.search(data, position):
        aligned = (base + match.start()) % 4 == 0
        position = match.start() + (4 if aligned else 1)
        if aligned:
            yield match


def pointer_pattern(values):
    """Factor shared pointer bytes to avoid testing hundreds of full branches.

    V8 maps have many related four-byte tagged pointers. A flat regex alternation
    spends most scan time retrying each branch on unrelated heap bytes; a byte
    trie keeps the exact same matches with a bounded four-byte decision path.
    """
    values = sorted(set(values))
    if not values:
        return re.compile(b'(?!)')
    if any(len(value) != 4 for value in values):
        raise ValueError('Expected four-byte compressed pointers')
    def build(words):
        if len(words) == 1:
            return re.escape(words[0])
        if len(words[0]) == 1:
            return b'[' + b''.join(re.escape(word) for word in words) + b']'
        groups = {}
        for value in words:
            groups.setdefault(value[:1], []).append(value[1:])
        choices = [re.escape(prefix) + build(rest) for prefix, rest in groups.items()]
        return b'(?:' + b'|'.join(choices) + b')'
    return re.compile(build(values))


class V8Layout:
    """Bounded object decoding; read(address,size) must return exact bytes or b''."""
    def __init__(self, cage, read):
        self.cage = cage
        self.read = read
        self._entry_cache = {}

    def address(self, pointer):
        if not isinstance(pointer, int) or not 0 < pointer < 2**32 or pointer & 3 != 1:
            return None
        return self.cage + pointer - 1

    def instance_type(self, obj):
        head = self.read(obj, 4)
        if len(head) != 4:
            return None
        mapa = self.address(word(head))
        if mapa is None:
            return None
        info = self.read(mapa, 16)
        if len(info) != 16:
            return None
        return int.from_bytes(info[8:10], 'little')

    def string(self, pointer, limit=16384, depth=0):
        obj = self.address(pointer)
        if obj is None or depth > 10:
            return None
        head = self.read(obj, 20)
        if len(head) < 12:
            return None
        typ = self.instance_type(obj)
        length = word(head, 8)
        if typ is None or typ >= 128 or length > limit:
            return None
        if not length:
            return ''
        representation = typ & 7
        if representation == 0:
            width = 1 if typ & 8 else 2
            data = self.read(obj + 12, length * width)
            if len(data) != length * width:
                return None
            try:
                return data.decode('latin1' if width == 1 else 'utf-16le')
            except UnicodeError:
                return None
        if representation == 5:
            result = self.string(word(head, 12), limit, depth + 1)
            return result if result is not None and len(result.encode('utf-16le')) // 2 == length else None
        if representation == 1:
            first = self.string(word(head, 12), limit, depth + 1)
            second = self.string(word(head, 16), limit, depth + 1)
            if (first is not None and second is not None
                    and len((first + second).encode('utf-16le')) // 2 == length):
                return first + second
        if representation == 3 and not word(head, 16) & 1:
            parent = self.string(word(head, 12), limit, depth + 1)
            offset = word(head, 16) >> 1
            if parent is not None:
                encoded = parent.encode('utf-16le')
                if (offset + length) * 2 <= len(encoded):
                    try:
                        return encoded[offset * 2:(offset + length) * 2].decode('utf-16le')
                    except UnicodeError:
                        return None
        return None

    def descriptor_entries(self, address):
        if address % 4 or self.instance_type(address) != DESCRIPTOR_TYPE:
            return None
        head = self.read(address, 20)
        if len(head) != 20:
            return None
        capacity = int.from_bytes(head[4:6], 'little')
        count = int.from_bytes(head[6:8], 'little')
        if not 0 < count <= capacity <= MAX_DESCRIPTORS:
            return None
        body = self.read(address + 20, count * 12)
        if len(body) != count * 12:
            return None
        cached = self._entry_cache.get(address)
        if cached is not None and cached[0] == head and cached[1] == body:
            return cached[2]
        entries = []
        for i in range(count):
            raw_details = word(body, i * 12 + 4)
            if raw_details & 1:
                return None
            name = self.string(word(body, i * 12), 120)
            # Retain only names required for the media binding.
            entries.append((name if name in FIELDS else '', raw_details >> 1, word(body, i * 12 + 8)))
        if len(self._entry_cache) < 512:
            # Cache only descriptor structure; reread and compare its bytes before
            # reuse so a GC/map change cannot reuse stale field offsets or names.
            self._entry_cache[address] = (head, body, entries)
        return entries

    def descriptor(self, address, anchor_index, anchor_pointer, anchor_name='decodeKey'):
        entries = self.descriptor_entries(address)
        if (not entries or not 0 <= anchor_index < len(entries)
                or entries[anchor_index][0] != anchor_name
                or word(self.read(address + 20 + anchor_index * 12, 4)) != anchor_pointer
                or not ANCHORS[anchor_name].issubset({e[0] for e in entries})):
            return None
        return entries

    def media_map(self, address, descriptor_address, entries, required=ANCHORS['decodeKey']):
        if address % 4 or self.instance_type(address) != MAP_TYPE:
            return None
        head = self.read(address, 40)
        if len(head) != 40 or self.address(word(head, 24)) != descriptor_address:
            return None
        if int.from_bytes(head[8:10], 'little') != JS_OBJECT_TYPE:
            return None
        size, start = head[4], head[5]
        own = (word(head, 12) >> 10) & 1023
        if not 3 <= start <= size <= 128 or not 1 <= own <= len(entries):
            return None
        if (word(head, 12) >> 21) & 1:  # Dictionary-mode objects use another layout.
            return None
        subset = entries[:own]
        if not required.issubset({entry[0] for entry in subset}):
            return None
        return {'address': address, 'descriptor_address': descriptor_address, 'size': size, 'start': start, 'entries': subset}

    def field(self, obj, media_map, entry):
        _name, details, constant = entry
        if details & 1:  # Accessors are never invoked.
            return None
        if (details >> 5) & 1:
            return constant
        index = (details >> 19) & 1023
        in_count = media_map['size'] - media_map['start']
        if index < in_count:
            value = self.read(obj + (media_map['start'] + index) * 4, 4)
            return word(value) if len(value) == 4 else None
        properties = self.read(obj + 4, 4)
        if len(properties) != 4:
            return None
        backing = self.address(word(properties))
        if backing is None or self.instance_type(backing) != PROPERTY_ARRAY_TYPE:
            return None
        head = self.read(backing, 8)
        if len(head) != 8 or word(head, 4) & 1:
            return None
        length = (word(head, 4) >> 1) & 1023
        out_index = index - in_count
        if out_index >= length:
            return None
        value = self.read(backing + 8 + out_index * 4, 4)
        return word(value) if len(value) == 4 else None

    def object_map(self, pointer, required):
        """Follow a named object's own descriptor; never enumerate its values."""
        obj = self.address(pointer)
        if obj is None or self.instance_type(obj) != JS_OBJECT_TYPE:
            return None
        address = self.address(word(self.read(obj, 4)))
        head = self.read(address, 40) if address is not None else b''
        if len(head) != 40:
            return None
        desc = self.address(word(head, 24))
        entries = self.descriptor_entries(desc) if desc is not None else None
        return self.media_map(address, desc, entries, required) if entries else None

    def named_fields(self, pointer, names):
        media_map = self.object_map(pointer, names)
        if not media_map:
            return None
        obj = self.address(pointer)
        entries = [e for e in media_map['entries'] if e[0] in names]
        values = {e[0]: self.field(obj, media_map, e) for e in entries}
        if any(value is None for value in values.values()):
            return None
        if word(self.read(obj, 4)) != media_map['address'] - self.cage + 1:
            return None
        if any(self.field(obj, media_map, e) != values[e[0]] for e in entries):
            return None
        return values

    def media_array(self, pointer):
        """Read only bounded packed/tagged JSArray elements along desc.media."""
        obj = self.address(pointer)
        if obj is None or self.instance_type(obj) != JS_ARRAY_TYPE:
            return None
        head = self.read(obj, 16)
        if len(head) != 16 or word(head, 12) & 1:
            return None
        count = word(head, 12) >> 1
        backing = self.address(word(head, 8))
        if not 0 < count <= 100 or backing is None or self.instance_type(backing) != FIXED_ARRAY_TYPE:
            return None
        info = self.read(backing, 8)
        if len(info) != 8 or word(info, 4) & 1 or not count <= word(info, 4) >> 1 <= 4096:
            return None
        elements = self.read(backing + 8, count * 4)
        if len(elements) != count * 4 or self.read(obj, 16) != head:
            return None
        return [word(elements, index * 4) for index in range(count)]

    def feed_engagement(self, pointer):
        """Read optional counts from this exact feed, never nearby heap objects.

        Counts observed in this runtime are tagged small integers. Other value
        layouts (strings, booleans, BigInts, unsupported boxed numbers) remain
        unknown rather than being guessed or rounded.
        """
        schema = self.object_map(pointer, ANCHORS['objectDesc'])
        if not schema:
            return engagement_from_feed(None)
        obj = self.address(pointer)
        entries = [entry for entry in schema['entries'] if entry[0] in FEED_COUNT_FIELDS.values()]
        pointers = {entry[0]: self.field(obj, schema, entry) for entry in entries}
        values = {}
        for entry in entries:
            value = pointers[entry[0]]
            if value is not None and not value & 1:
                values[entry[0]] = struct.unpack('<i', struct.pack('<I', value))[0] >> 1
        if word(self.read(obj, 4)) != schema['address'] - self.cage + 1:
            return engagement_from_feed(None)
        # Same-map counter mutations are possible while the player is open.
        for entry in entries:
            if self.field(obj, schema, entry) != pointers[entry[0]]:
                values.pop(entry[0], None)
        return engagement_from_feed(values)

    def feed_media(self, pointer, allowed_ids=None):
        """Bind title/author through feed -> objectDesc -> media[] references."""
        feed = self.named_fields(pointer, ANCHORS['objectDesc'])
        if not feed:
            return []
        desc_names = frozenset({'media', 'description'})
        desc = self.named_fields(feed['objectDesc'], desc_names)
        if not desc:
            return []
        members = self.media_array(desc['media'])
        if not members:
            return []
        found = []
        for member in members:
            media_map = self.object_map(member, ANCHORS['decodeKey'])
            item = self.media(self.address(member), media_map, allowed_ids) if media_map else None
            if item:
                found.append(item)
        if not found:
            return []
        title = self.string(desc['description'], 4096)
        contact = self.named_fields(feed['contact'], frozenset({'nickname'}))
        author = self.string(contact['nickname'], 120) if contact else None
        engagement = self.feed_engagement(pointer)
        for item in found:
            item['title'] = title or ''
            item['author'] = author or ''
            item['engagement'] = dict(engagement)
        # Reject a parent/array transition while following the complete chain.
        if (self.named_fields(pointer, ANCHORS['objectDesc']) != feed
                or self.named_fields(feed['objectDesc'], desc_names) != desc
                or self.media_array(desc['media']) != members
                or (contact and self.named_fields(feed['contact'], frozenset({'nickname'})) != contact)):
            return []
        return found

    def media(self, obj, media_map, allowed_ids=None):
        expected = media_map['address'] - self.cage + 1
        if obj % 4 or word(self.read(obj, 4)) != expected:
            return None
        attrs, pointers = {}, {}
        entries = sorted(media_map['entries'], key=lambda entry: entry[0] in {'thumbUrl', 'coverUrl', 'videoPlayLen'})
        checked_identity = False
        for entry in entries:
            if entry[0] in {'thumbUrl', 'coverUrl', 'videoPlayLen'} and allowed_ids is not None and not checked_identity:
                from .memory import loaded_identity
                early_url = _media_url(attrs)
                if not early_url or loaded_identity(early_url) not in allowed_ids:
                    return None
                checked_identity = True
            name = entry[0]
            if name not in MEDIA_FIELDS:
                continue
            pointer = self.field(obj, media_map, entry)
            if pointer is None:
                continue
            pointers[name] = pointer
            text = self.string(pointer, 16384 if name in {'url', 'urlToken', 'thumbUrl', 'coverUrl'} else 32)
            if text is not None:
                attrs[name] = text
            elif not pointer & 1:
                attrs[name] = struct.unpack('<i', struct.pack('<I', pointer))[0] >> 1
        url, key = _media_url(attrs), _decode_key(attrs.get('decodeKey'))
        if allowed_ids is not None and not checked_identity:
            from .memory import loaded_identity
            if not url or loaded_identity(url) not in allowed_ids:
                return None
        if not url or key is None or attrs.get('mediaType') not in (None, 4, '4'):
            return None
        # A moving GC / map transition invalidates this snapshot.
        if word(self.read(obj, 4)) != expected:
            return None
        # A same-map value update must not pair a new token/key with an old URL.
        for entry in media_map['entries']:
            if entry[0] in pointers and self.field(obj, media_map, entry) != pointers[entry[0]]:
                return None
        return {'url': url, 'decode_key': key, 'duration': attrs.get('videoPlayLen'),
                'thumbnail_url': bound_thumbnail(attrs), 'title': '', 'author': '',
                'source': 'bound_v8_media', 'seen_at': time.time()}


def extract_bound_media(reader, process, cancel, *, max_bytes=512 * 1024 * 1024,
                        max_seconds=20, diagnostics=None, on_items=None, allowed_ids=None):
    """Inspect only an already-selected renderer, at most four bounded passes.

    `max_bytes` bounds private readable address space per pass (up to 4 passes).
    A single global deadline also bounds structural reads and all passes.
    Validated structural hints skip discovery on repeat calls for up to 30s;
    every hinted object is reread, then fresh objects are scanned using the
    validated schemas. Nothing is written to disk or retained as media bytes.
    """
    from .memory import _MemoryInfo, loaded_identity
    if cancel.is_set():
        return []
    handle = reader.open(process)
    if handle is None:
        return []
    deadline = time.monotonic() + max_seconds
    stats = diagnostics if diagnostics is not None else {}
    stats.update(v8_strings=0, v8_descriptors=0, v8_maps=0, v8_media=0, v8_titled=0,
                 v8_hint_maps=0, v8_hint_objects=0, v8_schema_refreshed=False)
    cache_key = _hint_key(process)
    with _HINTS_LOCK:
        hint = _HINTS.get(cache_key) if cache_key is not None else None
    found, object_hints = {}, {}
    maps = {}
    last_emit = 0.0
    schema_updated = hint['updated'] if hint else 0

    def record(items, object_hint):
        nonlocal last_emit
        changed = False
        for item in items:
            if not item:
                continue
            item['id'] = loaded_identity(item['url'])
            key = (item['url'], item['decode_key'])
            old = found.get(key)
            if old:
                for field in ('title', 'author'):
                    if not item.get(field):
                        item[field] = old.get(field, '')
                if 'engagement' in old or 'engagement' in item:
                    item['engagement'] = merge_engagement(old.get('engagement'), item.get('engagement'))
            # Ignore read timestamps when deciding whether the UI needs an update.
            changed |= old is None or any(item.get(k) != old.get(k) for k in
                ('title', 'author', 'duration', 'thumbnail_url', 'engagement'))
            found[key] = item
            if len(object_hints) < 256:
                object_hints[object_hint] = None
        stamp = time.monotonic()
        if changed and on_items is not None and (stamp - last_emit >= .1 or len(found) == 1):
            on_items([dict(item) for item in found.values()])
            last_emit = stamp
    def active():
        return not cancel.is_set() and time.monotonic() < deadline
    def small(address, length):
        if not active() or not 0 < length <= 32768 or not 0 < address < 0x7FFFFFFFFFFF:
            return b''
        info = _MemoryInfo()
        if not reader.api.VirtualQueryEx(handle, address, ctypes.byref(info), ctypes.sizeof(info)):
            return b''
        if info.state != 0x1000 or info.protect not in (2, 4, 8) or address + length > (info.base or 0) + info.size:
            return b''
        result = reader.read(handle, address, length)
        return result if len(result) == length else b''
    def chunks():
        address, attempted = 0, 0
        while address < 0x7FFFFFFFFFFF and attempted < max_bytes and active():
            region = reader.query(handle, address)
            if not region:
                break
            base, size, private_readable = region
            if size <= 0 or base + size <= address:
                break
            address = base + size
            if not private_readable:
                continue
            for offset in range(0, size, 1024 * 1024):
                if attempted >= max_bytes or not active():
                    return
                length = min(1024 * 1024, size - offset, max_bytes - attempted)
                attempted += length
                yield base + offset, reader.read(handle, base + offset, length)
    try:
        if hint:
            layouts = {}
            for cage, address, desc, name in hint['maps']:
                if not active():
                    break
                layout = layouts.setdefault(cage, V8Layout(cage, small))
                entries = layout.descriptor_entries(desc)
                media_map = layout.media_map(address, desc, entries, ANCHORS[name]) if entries else None
                if media_map:
                    maps[(cage, address)] = (layout, media_map, name)
            stats['v8_hint_maps'] = len(maps)
            for cage, address, map_address, name in hint['objects']:
                cached = maps.get((cage, map_address))
                if not cached or not active():
                    continue
                layout, media_map, _name = cached
                items = (layout.feed_media(address - cage + 1, allowed_ids) if name == 'objectDesc'
                         else [layout.media(address, media_map, allowed_ids)])
                record(items, (cage, address, map_address, name))
            stats['v8_hint_objects'] = len(object_hints)
        # Revalidate every cached descriptor/map and every returned object. Cached
        # schemas avoid three discovery passes; fresh objects still get scanned.
        refresh = (not hint or not maps or len(maps) != len(hint['maps'])
                   or time.monotonic() - schema_updated >= SCHEMA_REFRESH_SECONDS)
        if refresh and active():
            labels = {}
            for base, data in chunks():
                for name in ANCHORS:
                    needle = name.encode('ascii')
                    offset = data.find(needle)
                    while offset >= 0 and len(labels) < 16 and active():
                        obj = base + offset - 12
                        cage = obj & ~0xFFFFFFFF
                        layout = V8Layout(cage, small)
                        pointer = obj - cage + 1
                        if obj % 4 == 0 and layout.instance_type(obj) == 8 and layout.string(pointer, len(name)) == name:
                            labels[(cage, pointer)] = (layout, name)
                        offset = data.find(needle, offset + len(needle))
            stats['v8_strings'] = len(labels)
            descriptors = {}
            for base, data in chunks():
                for (cage, pointer), (layout, name) in labels.items():
                    if base & ~0xFFFFFFFF != cage:
                        continue
                    needle = struct.pack('<I', pointer)
                    offset = data.find(needle)
                    while offset >= 0 and active() and len(descriptors) < 128:
                        ref = base + offset
                        if ref % 4 == 0:
                            for index in range(64):
                                address = ref - 20 - index * 12
                                entries = layout.descriptor(address, index, pointer, name)
                                if entries:
                                    descriptors[(cage, address)] = (layout, entries, name)
                        # An unaligned byte match can overlap a real aligned pointer.
                        offset = data.find(needle, offset + 1)
            stats['v8_descriptors'] = len(descriptors)
            patterns = {struct.pack('<I', address - cage + 1): (cage, address, layout, entries, name)
                        for (cage, address), (layout, entries, name) in descriptors.items()}
            pattern = pointer_pattern(patterns)
            for base, data in chunks():
                for hit in aligned_matches(pattern, data, base):
                    cage, desc, layout, entries, name = patterns[hit.group()]
                    if base & ~0xFFFFFFFF != cage or not active() or len(maps) >= 512:
                        continue
                    address = base + hit.start() - 24
                    media_map = layout.media_map(address, desc, entries, ANCHORS[name])
                    if media_map:
                        maps[(cage, address)] = (layout, media_map, name)
            stats['v8_maps'] = len(maps)
            schema_updated = time.monotonic()
            stats['v8_schema_refreshed'] = True
        stats['v8_maps'] = len(maps)
        if not maps:
            return list(found.values())
        patterns = {struct.pack('<I', address - cage + 1): (cage, layout, media_map, name)
                    for (cage, address), (layout, media_map, name) in maps.items()}
        pattern = pointer_pattern(patterns)
        for base, data in chunks():
            for hit in aligned_matches(pattern, data, base):
                cage, layout, media_map, name = patterns[hit.group()]
                if base & ~0xFFFFFFFF != cage or not active() or len(found) >= 256:
                    continue
                address = base + hit.start()
                items = (layout.feed_media(address - cage + 1, allowed_ids) if name == 'objectDesc'
                         else [layout.media(address, media_map, allowed_ids)])
                record(items, (cage, address, media_map['address'], name))
        stats['v8_media'] = len(found)
        stats['v8_titled'] = sum(bool(item.get('title')) for item in found.values())
        if cache_key is not None and maps:
            cached_maps = []
            for (cage, address), (layout, media_map, name) in maps.items():
                desc = media_map.get('descriptor_address')
                if desc is not None:
                    cached_maps.append((cage, address, desc, name))
            if cached_maps:
                with _HINTS_LOCK:
                    _HINTS[cache_key] = {'updated': schema_updated, 'maps': cached_maps,
                                         'objects': list(object_hints)}
                    while len(_HINTS) > 4:
                        _HINTS.pop(next(iter(_HINTS)))
        if on_items is not None and found:
            on_items([dict(item) for item in found.values()])
        return list(found.values())
    finally:
        reader.close(handle)
