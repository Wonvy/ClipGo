import struct
import re
import sys
from pathlib import Path
import unittest
import threading
import types
import random

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wechat_channels.v8_media import V8Layout, aligned_matches
from wechat_channels import v8_media


class Heap:
    cage = 0x100000000

    def __init__(self):
        self.data = bytearray(0x20000)
        self.next = 0x1000
        self.meta = self.allocate(40)
        self.put(self.meta, self.ptr(self.meta))
        self.short(self.meta + 8, 277)
        self.maps = {277: self.meta}
        self.layout = V8Layout(self.cage, self.read)

    def allocate(self, size):
        address = self.cage + self.next
        self.next += (size + 3) & ~3
        return address

    def read(self, address, size):
        offset = address - self.cage
        return bytes(self.data[offset:offset + size]) if 0 <= offset < len(self.data) else b''

    def write(self, address, data):
        offset = address - self.cage
        self.data[offset:offset + len(data)] = data

    def put(self, address, value):
        self.write(address, struct.pack('<I', value))

    def short(self, address, value):
        self.write(address, struct.pack('<H', value))

    def ptr(self, address):
        return address - self.cage + 1

    def type_map(self, typ):
        if typ not in self.maps:
            address = self.allocate(40)
            self.put(address, self.ptr(self.meta))
            self.short(address + 8, typ)
            self.maps[typ] = address
        return self.maps[typ]

    def string(self, value, two_byte=False):
        data = value.encode('utf-16le' if two_byte else 'latin1')
        address = self.allocate(max(20, 12 + len(data)))
        self.put(address, self.ptr(self.type_map(0 if two_byte else 8)))
        self.put(address + 8, len(data) // 2 if two_byte else len(data))
        self.write(address + 12, data)
        return self.ptr(address)

    def media(self, key='1234567890'):
        names = ['url', 'mediaType', 'videoPlayLen', 'decodeKey', 'urlToken']
        indices = [0, 2, 3, 11, 12]
        name_ptrs = [self.string(name) for name in names]
        desc = self.allocate(20 + len(names) * 12)
        self.put(desc, self.ptr(self.type_map(253)))
        self.short(desc + 4, len(names))
        self.short(desc + 6, len(names))
        for i, (name, index) in enumerate(zip(name_ptrs, indices)):
            self.put(desc + 20 + i * 12, name)
            self.put(desc + 24 + i * 12, ((index << 19) | (3 << 6)) << 1)
        media_map = self.allocate(40)
        self.put(media_map, self.ptr(self.meta))
        self.write(media_map + 4, bytes([7, 3]))
        self.short(media_map + 8, 1057)
        self.put(media_map + 12, len(names) << 10)
        self.put(media_map + 24, self.ptr(desc))
        obj = self.allocate(28)
        backing = self.allocate(8 + 9 * 4)
        self.put(backing, self.ptr(self.type_map(282)))
        self.put(backing + 4, 9 << 1)
        self.put(obj, self.ptr(media_map))
        self.put(obj + 4, self.ptr(backing))
        self.put(obj + 12, self.string('https://finder.video.qq.com/251/20302/stodownload?encfilekey=test&idx=1'))
        self.put(obj + 20, 4 << 1)
        self.put(obj + 24, 1407 << 1)
        self.put(backing + 8 + 7 * 4, self.string(key))
        self.put(backing + 8 + 8 * 4, self.string('&token=bound'))
        return obj, desc, media_map, backing, name_ptrs[3]

    def object(self, fields):
        desc = self.allocate(20 + len(fields) * 12)
        self.put(desc, self.ptr(self.type_map(253)))
        self.short(desc + 4, len(fields))
        self.short(desc + 6, len(fields))
        for index, name in enumerate(fields):
            self.put(desc + 20 + index * 12, self.string(name))
            self.put(desc + 24 + index * 12, ((index << 19) | (3 << 6)) << 1)
        mapa = self.allocate(40)
        self.put(mapa, self.ptr(self.meta))
        self.write(mapa + 4, bytes([3 + len(fields), 3]))
        self.short(mapa + 8, 1057)
        self.put(mapa + 12, len(fields) << 10)
        self.put(mapa + 24, self.ptr(desc))
        obj = self.allocate(12 + len(fields) * 4)
        self.put(obj, self.ptr(mapa))
        for index, value in enumerate(fields.values()):
            self.put(obj + 12 + index * 4, value)
        return self.ptr(obj)

    def array(self, elements):
        backing = self.allocate(8 + len(elements) * 4)
        self.put(backing, self.ptr(self.type_map(205)))
        self.put(backing + 4, len(elements) << 1)
        for index, element in enumerate(elements):
            self.put(backing + 8 + index * 4, element)
        obj = self.allocate(16)
        self.put(obj, self.ptr(self.type_map(2119)))
        self.put(obj + 8, self.ptr(backing))
        self.put(obj + 12, len(elements) << 1)
        return self.ptr(obj)

    def feed(self, media, title, author):
        array = self.array([self.ptr(media)])
        desc = self.object({'media': array, 'description': self.string(title, True)})
        contact = self.object({'nickname': self.string(author, True)})
        return self.object({'objectDesc': desc, 'contact': contact}), array


class V8MediaTests(unittest.TestCase):
    def setUp(self):
        self.heap = Heap()
        self.obj, self.desc, self.map, self.backing, self.anchor = self.heap.media()
        self.layout = self.heap.layout

    def parsed_map(self):
        entries = self.layout.descriptor(self.desc, 3, self.anchor)
        self.assertIsNotNone(entries)
        return self.layout.media_map(self.map, self.desc, entries)

    def test_same_object_url_key_token_duration(self):
        item = self.layout.media(self.obj, self.parsed_map())
        self.assertEqual(item['decode_key'], '1234567890')
        self.assertEqual(item['duration'], 1407)
        self.assertTrue(item['url'].endswith('&token=bound'))

    def test_counts_and_descriptor_anchor_bounds(self):
        self.assertIsNone(self.layout.descriptor(self.desc, 5, self.anchor))
        self.heap.short(self.desc + 4, 4)
        self.assertIsNone(self.layout.descriptor(self.desc, 3, self.anchor))
        self.heap.short(self.desc + 4, 129)
        self.assertIsNone(self.layout.descriptor(self.desc, 3, self.anchor))

    def test_wrong_runtime_descriptor_and_map_types_fail_closed(self):
        self.heap.put(self.desc, self.heap.ptr(self.heap.type_map(252)))
        self.assertIsNone(self.layout.descriptor(self.desc, 3, self.anchor))
        self.heap.put(self.desc, self.heap.ptr(self.heap.type_map(253)))
        self.heap.short(self.map + 8, 1058)
        self.assertIsNone(self.parsed_map())

    def test_wrong_map_descriptor_pointer_fails(self):
        self.heap.put(self.map + 24, self.heap.ptr(self.desc + 4))
        self.assertIsNone(self.parsed_map())

    def test_dictionary_and_invalid_inobject_bounds_fail(self):
        self.heap.put(self.map + 12, (5 << 10) | (1 << 21))
        self.assertIsNone(self.parsed_map())
        self.heap.put(self.map + 12, 5 << 10)
        self.heap.write(self.map + 4, bytes([3, 4]))
        self.assertIsNone(self.parsed_map())

    def test_short_or_wrong_property_array_does_not_borrow_neighbor_key(self):
        media_map = self.parsed_map()
        self.heap.put(self.backing + 4, 7 << 1)
        self.assertIsNone(self.layout.media(self.obj, media_map))
        self.heap.put(self.backing + 4, 9 << 1)
        self.heap.put(self.backing, self.heap.ptr(self.heap.type_map(253)))
        self.assertIsNone(self.layout.media(self.obj, media_map))

    def test_key_range_and_video_type_validation(self):
        self.heap.put(self.backing + 8 + 7 * 4, self.heap.string(str(2**64)))
        self.assertIsNone(self.layout.media(self.obj, self.parsed_map()))
        self.heap.put(self.backing + 8 + 7 * 4, self.heap.string('123'))
        self.heap.put(self.obj + 20, 2 << 1)
        self.assertIsNone(self.layout.media(self.obj, self.parsed_map()))

    def test_accessors_and_descriptor_non_smi_rejected(self):
        parsed = self.parsed_map()
        entry = parsed['entries'][3]
        self.assertIsNone(self.layout.field(self.obj, parsed, (entry[0], entry[1] | 1, entry[2])))
        self.heap.put(self.desc + 24, 1)
        self.assertIsNone(self.layout.descriptor(self.desc, 3, self.anchor))

    def test_utf16_and_cycle_string_bounds(self):
        ptr = self.heap.string('短视频', True)
        self.assertEqual(self.layout.string(ptr), '短视频')
        self.assertIsNone(self.layout.string(ptr, 2))
        address = self.heap.allocate(20)
        self.heap.put(address, self.heap.ptr(self.heap.type_map(13)))
        self.heap.put(address + 8, 4)
        self.heap.put(address + 12, self.heap.ptr(address))
        self.assertIsNone(self.layout.string(self.heap.ptr(address)))

    def test_strong_pointer_and_object_map_validation(self):
        self.assertIsNone(self.layout.address(self.anchor + 2))
        self.assertIsNone(self.layout.address(0x100000001))
        parsed = self.parsed_map()
        self.heap.put(self.obj, self.heap.ptr(self.heap.meta))
        self.assertIsNone(self.layout.media(self.obj, parsed))

    def test_same_map_field_change_rejects_mixed_snapshot(self):
        parsed = self.parsed_map()
        original = self.layout.field
        reads = {}
        def changing(obj, media_map, entry):
            reads[entry[0]] = reads.get(entry[0], 0) + 1
            value = original(obj, media_map, entry)
            return value + 4 if entry[0] == 'url' and reads['url'] > 1 else value
        self.layout.field = changing
        self.assertIsNone(self.layout.media(self.obj, parsed))

    def test_unaligned_overlapping_pointer_does_not_hide_aligned_match(self):
        pattern = re.compile(b'aaaa')
        matches = list(aligned_matches(pattern, b'baaaaaaaa', 0))
        self.assertEqual([match.start() for match in matches], [4])

    def test_title_author_follow_exact_feed_media_references(self):
        other, *_ = self.heap.media('987654321')
        first, _ = self.heap.feed(self.obj, '第一条真实标题', '作者甲')
        second, _ = self.heap.feed(other, '第二条真实标题', '作者乙')
        first_item, = self.layout.feed_media(first)
        second_item, = self.layout.feed_media(second)
        self.assertEqual((first_item['title'], first_item['author'], first_item['decode_key']),
                         ('第一条真实标题', '作者甲', '1234567890'))
        self.assertEqual((second_item['title'], second_item['author'], second_item['decode_key']),
                         ('第二条真实标题', '作者乙', '987654321'))
        # Even identical media URLs cannot cause neighboring titles to be borrowed.
        self.assertEqual(first_item['url'], second_item['url'])

    def test_array_runtime_and_capacity_fail_closed(self):
        feed, array = self.heap.feed(self.obj, '绑定标题', '绑定作者')
        address = self.layout.address(array)
        self.heap.put(address, self.heap.ptr(self.heap.type_map(2118)))
        self.assertEqual(self.layout.feed_media(feed), [])
        self.heap.put(address, self.heap.ptr(self.heap.type_map(2119)))
        self.heap.put(address + 12, 101 << 1)
        self.assertEqual(self.layout.feed_media(feed), [])
        self.heap.put(address + 12, 2 << 1)
        self.assertEqual(self.layout.feed_media(feed), [])

    def test_changed_parent_array_rejects_title_pairing(self):
        feed, _ = self.heap.feed(self.obj, '原始标题', '原始作者')
        original = self.layout.media_array
        calls = []
        def changing(pointer):
            calls.append(pointer)
            return original(pointer) if len(calls) == 1 else []
        self.layout.media_array = changing
        self.assertEqual(self.layout.feed_media(feed), [])

    def test_title_does_not_require_or_borrow_unrelated_nickname(self):
        array = self.heap.array([self.heap.ptr(self.obj)])
        desc = self.heap.object({'media': array, 'description': self.heap.string('只有标题', True)})
        wrong_contact = self.heap.object({'description': self.heap.string('不是作者', True)})
        self.heap.object({'nickname': self.heap.string('邻居作者', True)})
        feed = self.heap.object({'objectDesc': desc, 'contact': wrong_contact})
        item, = self.layout.feed_media(feed)
        self.assertEqual(item['title'], '只有标题')
        self.assertEqual(item['author'], '')

    def test_composite_title_strings_use_utf16_code_units(self):
        emoji = self.heap.string('😀', True)
        suffix = self.heap.string('标题', True)
        cons = self.heap.allocate(20)
        self.heap.put(cons, self.heap.ptr(self.heap.type_map(1)))
        self.heap.put(cons + 8, 4)
        self.heap.put(cons + 12, emoji)
        self.heap.put(cons + 16, suffix)
        self.assertEqual(self.layout.string(self.heap.ptr(cons)), '😀标题')
        parent = self.heap.string('前😀后', True)
        sliced = self.heap.allocate(20)
        self.heap.put(sliced, self.heap.ptr(self.heap.type_map(3)))
        self.heap.put(sliced + 8, 2)
        self.heap.put(sliced + 12, parent)
        self.heap.put(sliced + 16, 1 << 1)
        self.assertEqual(self.layout.string(self.heap.ptr(sliced)), '😀')

    def test_descriptor_cache_rechecks_live_field_offsets(self):
        self.assertEqual(self.layout.media(self.obj, self.parsed_map())['decode_key'], '1234567890')
        # Redirect decodeKey's descriptor to the token slot: cached names/offsets
        # must not silently retain the former, valid key.
        self.heap.put(self.desc + 24 + 3 * 12, ((12 << 19) | (3 << 6)) << 1)
        self.assertIsNone(self.layout.media(self.obj, self.parsed_map()))

    def test_thumbnail_keeps_its_own_full_signature_from_same_media_object(self):
        thumb = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=image&token=image-token&picformat=200'
        cover = thumb.replace('image-token', 'cover-token')
        ptr = self.heap.object({
            'url': self.heap.string('https://finder.video.qq.com/video?encfilekey=video'),
            'urlToken': self.heap.string('&token=video-token'),
            'decodeKey': self.heap.string('123'),
            'thumbUrl': self.heap.string(thumb),
            'coverUrl': self.heap.string(cover),
        })
        media_map = self.layout.object_map(ptr, frozenset({'url', 'decodeKey'}))
        item = self.layout.media(self.layout.address(ptr), media_map)
        self.assertEqual(item['thumbnail_url'], thumb)
        self.assertTrue(item['url'].endswith('&token=video-token'))
        self.assertNotIn('video-token', item['thumbnail_url'])

    def test_cover_fallback_uses_only_allowlisted_bound_image_host(self):
        from wechat_channels.v8_media import bound_thumbnail
        cover = 'https://finder.video.qq.com/image?token=cover'
        self.assertEqual(bound_thumbnail({'thumbUrl': 'https://example.com/image', 'coverUrl': cover}), cover)
        for bad in ('http://finder.video.qq.com/image', 'https://finder.video.qq.com.evil.test/image',
                    'https://user@finder.video.qq.com/image', 'https://finder.video.qq.com:444/image',
                    cover + '\n', cover + '#fragment'):
            self.assertEqual(bound_thumbnail({'thumbUrl': bad}), '')


class HintScanTests(unittest.TestCase):
    def setUp(self):
        self.heap = Heap()
        self.obj, self.desc, self.map, self.backing, _ = self.heap.media()
        self.process = {'pid': 123, 'created': 456, 'path': 'C:\\WeChatAppEx.exe'}
        self.reader = types.SimpleNamespace(open=lambda _: 1, close=lambda _: None)
        self.queries = []
        def query(handle, address):
            self.queries.append(address)
            if address <= self.heap.cage:
                return self.heap.cage, len(self.heap.data), True
            return None
        def virtual_query(handle, address, info, size):
            if not self.heap.cage <= address < self.heap.cage + len(self.heap.data):
                return 0
            info._obj.base = self.heap.cage
            info._obj.size = len(self.heap.data)
            info._obj.state = 0x1000
            info._obj.protect = 4
            info._obj.kind = 0x20000
            return size
        self.reader.query = query
        self.reader.read = lambda handle, address, size: self.heap.read(address, size)
        self.reader.api = types.SimpleNamespace(VirtualQueryEx=virtual_query)
        v8_media._HINTS.clear()

    def scan(self, **kwargs):
        return v8_media.extract_bound_media(self.reader, self.process, threading.Event(), **kwargs)

    def test_warm_hint_emits_before_heap_pass_and_rechecks_changed_key(self):
        initial = self.scan()
        self.assertTrue(initial)
        self.heap.put(self.backing + 8 + 7 * 4, self.heap.string('987654321'))
        self.queries.clear()
        events, stats = [], {}
        def update(items):
            events.append((len(self.queries), items))
        current = self.scan(on_items=update, diagnostics=stats)
        self.assertEqual(events[0][0], 0)
        self.assertEqual({item['decode_key'] for item in current}, {'987654321'})
        self.assertFalse(stats['v8_schema_refreshed'])
        self.assertGreater(stats['v8_hint_objects'], 0)
        self.assertEqual(self.queries.count(0), 1)

    def test_restarted_process_does_not_reuse_hint_addresses(self):
        self.scan()
        self.process['created'] += 1
        stats = {}
        self.scan(diagnostics=stats)
        self.assertEqual(stats['v8_hint_maps'], 0)
        self.assertTrue(stats['v8_schema_refreshed'])

    def test_invalidated_schema_cannot_return_cached_values(self):
        self.scan()
        self.heap.put(self.map + 24, 0)
        stats = {}
        current = self.scan(diagnostics=stats)
        self.assertEqual(current, [])
        self.assertEqual(stats['v8_hint_maps'], 0)

    def test_periodic_discovery_refreshes_schema(self):
        self.scan()
        v8_media._HINTS[v8_media._hint_key(self.process)]['updated'] -= 31
        stats = {}
        self.scan(diagnostics=stats)
        self.assertTrue(stats['v8_schema_refreshed'])

    def test_warm_pass_discovers_new_objects_using_revalidated_schema(self):
        initial = self.scan()
        self.assertEqual(len(initial), 1)
        copied = self.heap.allocate(28)
        self.heap.write(copied, self.heap.read(self.obj, 28))
        backing = self.heap.allocate(8 + 9 * 4)
        self.heap.write(backing, self.heap.read(self.backing, 8 + 9 * 4))
        self.heap.put(copied + 4, self.heap.ptr(backing))
        self.heap.put(backing + 8 + 7 * 4, self.heap.string('1122334455'))
        stats = {}
        items = self.scan(diagnostics=stats)
        self.assertEqual({item['decode_key'] for item in items}, {'1234567890', '1122334455'})
        self.assertFalse(stats['v8_schema_refreshed'])

    def test_allowed_identity_filter_does_not_return_residual_media(self):
        self.heap.feed(self.obj, '不能借用的旧标题', '旧作者')
        items = self.scan(allowed_ids={'unrelated-video-id'})
        self.assertEqual(items, [])
        hint = v8_media._HINTS[v8_media._hint_key(self.process)]
        self.assertEqual(hint['objects'], [])

    def test_pointer_trie_matches_flat_pattern_including_special_bytes(self):
        rng = random.Random(128)
        needles = [bytes(rng.randrange(256) for _ in range(4)) for _ in range(300)]
        needles += [b'aaaa', b']-^\\', b'\0\0\0\0']
        data = b''.join(b'x' * rng.randrange(8) + value for value in needles)
        flat = re.compile(b'|'.join(re.escape(value) for value in needles))
        trie = v8_media.pointer_pattern(needles)
        for base in range(4):
            expected = [(m.start(), m.group()) for m in aligned_matches(flat, data, base)]
            result = [(m.start(), m.group()) for m in aligned_matches(trie, data, base)]
            self.assertEqual(result, expected)
        self.assertIsNone(v8_media.pointer_pattern([]).search(data))


if __name__ == '__main__':
    unittest.main()
