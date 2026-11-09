import json
import unittest

from tools.wechat_channels.memory_metadata import extract_media_metadata


class MemoryMetadataTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://finder.video.qq.com/251/20302/stodownload?encfilekey=sample'

    def test_complete_media_object_preserves_exact_pair_and_title(self):
        payload = {'objectDesc': {'description': '视频标题', 'media': [
            {'url': self.url, 'urlToken': '&token=sample', 'decodeKey': '12345'}]},
            'contact': {'nickname': '作者'}}
        raw = b'\xff\x00' + json.dumps(payload, ensure_ascii=False).encode() + b'\xff'
        found = extract_media_metadata(raw, {self.url}, now=1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['decode_key'], '12345')
        self.assertEqual(found[0]['title'], '视频标题')
        self.assertEqual(found[0]['author'], '作者')
        self.assertEqual(found[0]['url'], self.url + '&token=sample')
        self.assertEqual(set(found[0]), {'id','url','decode_key','title','author','source','seen_at','engagement'})

    def test_counts_follow_the_same_feed_not_neighbor_objects(self):
        second = self.url.replace('sample', 'other')
        payload = {'feeds': [
            {'objectDesc': {'description': '甲', 'media': [{'url': self.url, 'decodeKey': '11'}]},
             'favCount': 4302, 'forwardCount': 2380, 'likeCount': 3489, 'commentCount': 304},
            {'objectDesc': {'description': '乙', 'media': [{'url': second, 'decodeKey': '22'}]},
             'favCount': 0, 'forwardCount': 2, 'likeCount': -1, 'commentCount': True}],
            'unrelated': {'favCount': 99999, 'likeCount': 99999}}
        found = {item['title']: item for item in extract_media_metadata(json.dumps(payload).encode())}
        self.assertEqual(found['甲']['engagement'], {'recommend': 4302, 'share': 2380, 'like': 3489, 'comment': 304})
        self.assertEqual(found['乙']['engagement'], {'recommend': 0, 'share': 2, 'like': None, 'comment': None})

    def test_count_context_is_not_inherited_by_unrelated_media_branch(self):
        other = self.url.replace('sample', 'neighbor')
        payload = {'objectDesc': {'description': '甲', 'media': [{'url': self.url, 'decodeKey': '11'}]},
                   'favCount': 123, 'unrelated': {'url': other, 'decodeKey': '22'}}
        found = extract_media_metadata(json.dumps(payload).encode())
        neighbor = next(item for item in found if item['url'] == other)
        self.assertTrue(all(value is None for value in neighbor['engagement'].values()))

    def test_unrelated_neighbor_key_is_never_paired(self):
        raw = json.dumps({'url': self.url, 'neighbor': {'decodeKey': '12345'}}).encode()
        self.assertEqual(extract_media_metadata(raw), [])
        self.assertEqual(extract_media_metadata((self.url + '\0decodeKey\012345').encode()), [])

    def test_unencrypted_complete_media_retains_title_without_borrowing_key(self):
        payload = {'objectDesc': {'description': '明文视频', 'media': [
            {'url': self.url, 'urlToken': '&token=signed', 'neighbor': {'decodeKey': '999'}}]}}
        found = extract_media_metadata(json.dumps(payload).encode())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['title'], '明文视频')
        self.assertIsNone(found[0]['decode_key'])
        self.assertEqual(found[0]['url'], self.url + '&token=signed')

    def test_utf16_json_and_known_url_filter(self):
        raw = json.dumps({'url':self.url,'decodeKey':'0'}).encode('utf-16le')
        self.assertEqual(extract_media_metadata(raw)[0]['decode_key'], '0')
        self.assertEqual(extract_media_metadata(raw, set()), [])

    def test_truncated_object_and_distant_key_rejected(self):
        raw = json.dumps({'url':self.url,'decodeKey':'12345'}).encode()
        self.assertEqual(extract_media_metadata(raw[:-1]), [])
        far = json.dumps({'url':self.url,'padding':'x'*20000,'decodeKey':'12345'}).encode()
        self.assertEqual(extract_media_metadata(far), [])


if __name__ == '__main__':
    unittest.main()
