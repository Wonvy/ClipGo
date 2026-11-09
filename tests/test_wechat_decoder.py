"""Independent media vector and boundary checks for Channels decoding."""

import unittest

from tools.wechat_channels.decoder import (
    ENCRYPTED_PREFIX_SIZE,
    Isaac64,
    PrefixDecoder,
    decode_prefix,
    decrypt_prefix,
    parse_key,
)


class DecoderTests(unittest.TestCase):
    def test_published_channels_header(self):
        # Published sample key/header, independently produced using WeChat WASM:
        # https://github.com/Evil0ctal/WeChat-Channels-Video-File-Decryption
        encrypted = bytes.fromhex("23766a16ff8ffe1a1ca6cd5f994846ab")
        expected = bytes.fromhex("000000206674797069736f6d00000200")
        self.assertEqual(decode_prefix(encrypted, "2136343393"), expected)

    def test_zero_seed_reference_word(self):
        # ISAAC-64's standard zero-seed sequence, also used by Polyglot.
        self.assertEqual(Isaac64(0).next_word(), 0x9D39247E33776D41)

    def test_wrong_and_missing_key_fail_before_saving(self):
        encrypted = bytes.fromhex("23766a16ff8ffe1a1ca6cd5f994846ab")
        for key in (None, "", "2136343394"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                decode_prefix(encrypted, key)

    def test_chunk_boundaries_and_plain_suffix(self):
        plaintext = (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00" +
                     bytes(range(256)) * 600)
        encrypted = decrypt_prefix(plaintext, "18446744073709551615")
        self.assertEqual(encrypted[ENCRYPTED_PREFIX_SIZE:],
                         plaintext[ENCRYPTED_PREFIX_SIZE:])
        decoder = PrefixDecoder("18446744073709551615")
        chunks = []
        pos = 0
        for size in (1, 7, 2051, 65521, 65519, len(encrypted)):
            chunk = encrypted[pos:pos + size]
            chunks.append(decoder.decode(chunk))
            pos += len(chunk)
        self.assertEqual(b"".join(chunks), plaintext)

    def test_plain_video_and_unencrypted_stream(self):
        plain = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 40
        self.assertEqual(decode_prefix(plain, None), plain)
        self.assertEqual(PrefixDecoder(None).decode(plain), plain)

    def test_keys_are_exact_unsigned_integers(self):
        self.assertEqual(parse_key("18446744073709551615"), (1 << 64) - 1)
        self.assertEqual(parse_key("0"), 0)
        for invalid in (-1, 1 << 64, 1.0, True, "1e9", "-1", "１２", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_key(invalid)

    def test_short_or_non_video_payload_rejected(self):
        for data in (b"", b"12345678", b"<html>error page</html>"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_prefix(data, None)


if __name__ == "__main__":
    unittest.main()
