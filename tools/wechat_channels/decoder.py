"""Decode the ISAAC-64 XOR prefix used by Channels media.

The generator is a Python adaptation of Bob Jenkins' public-domain ISAAC-64
algorithm (1996): https://burtleburtle.net/bob/c/isaac64.c and isaac64.h.
No third-party downloader implementation or WebAssembly binary is bundled.
The caller must supply the decode key paired with the original media URL.
"""

from __future__ import annotations

MASK64 = (1 << 64) - 1
ENCRYPTED_PREFIX_SIZE = 128 * 1024


def parse_key(value: int | str) -> int:
    """Accept only exact unsigned 64-bit integers (never rounded floats)."""
    if isinstance(value, bool):
        raise ValueError("视频解码密钥必须是无符号整数")
    if isinstance(value, str):
        value = value.strip()
        if not value or not value.isascii() or not value.isdecimal():
            raise ValueError("视频解码密钥格式无效")
        value = int(value, 10)
    if not isinstance(value, int) or not 0 <= value <= MASK64:
        raise ValueError("视频解码密钥超出 64 位整数范围")
    return value


def _mix(words: list[int]) -> list[int]:
    """Eight-word avalanche, with unsigned wraparound after every operation."""
    a, b, c, d, e, f, g, h = words
    a = (a - e) & MASK64
    f ^= h >> 9
    h = (h + a) & MASK64
    b = (b - f) & MASK64
    g = (g ^ (a << 9)) & MASK64
    a = (a + b) & MASK64
    c = (c - g) & MASK64
    h ^= b >> 23
    b = (b + c) & MASK64
    d = (d - h) & MASK64
    a = (a ^ (c << 15)) & MASK64
    c = (c + d) & MASK64
    e = (e - a) & MASK64
    b ^= d >> 14
    d = (d + e) & MASK64
    f = (f - b) & MASK64
    c = (c ^ (e << 20)) & MASK64
    e = (e + f) & MASK64
    g = (g - c) & MASK64
    d ^= f >> 17
    f = (f + g) & MASK64
    h = (h - d) & MASK64
    e = (e ^ (g << 14)) & MASK64
    g = (g + h) & MASK64
    return [a, b, c, d, e, f, g, h]


class Isaac64:
    """ISAAC-64 seeded in word zero, yielding reverse-ordered result words."""

    def __init__(self, key: int | str):
        seed = [parse_key(key)] + [0] * 255
        self._memory = [0] * 256
        self._results = [0] * 256
        self._a = self._b = self._c = 0
        words = [0x9E3779B97F4A7C13] * 8
        for _ in range(4):
            words = _mix(words)
        for source in (seed, self._memory):
            for start in range(0, 256, 8):
                words = _mix([
                    (words[j] + source[start + j]) & MASK64 for j in range(8)
                ])
                self._memory[start:start + 8] = words
        self._refill()

    def _refill(self) -> None:
        memory = self._memory
        a = self._a
        self._c = (self._c + 1) & MASK64
        b = (self._b + self._c) & MASK64
        for i in range(256):
            x = memory[i]
            if i % 4 == 0:
                a = ~(a ^ (a << 21)) & MASK64
            elif i % 4 == 1:
                a ^= a >> 5
            elif i % 4 == 2:
                a = (a ^ (a << 12)) & MASK64
            else:
                a ^= a >> 33
            a = (a + memory[(i + 128) & 255]) & MASK64
            y = (memory[(x >> 3) & 255] + a + b) & MASK64
            memory[i] = y
            b = (memory[(y >> 11) & 255] + x) & MASK64
            self._results[i] = b
        self._a, self._b = a, b
        self._remaining = 256

    def next_word(self) -> int:
        if self._remaining == 0:
            self._refill()
        self._remaining -= 1
        return self._results[self._remaining]


class PrefixDecoder:
    """Stateful decoder for sequential download chunks; suffix passes through.

    A key of None means unencrypted media; numeric zero is a valid seed. Only
    the first ``limit`` bytes are transformed, independent of chunk boundaries.
    """

    def __init__(self, key: int | str | None, limit: int = ENCRYPTED_PREFIX_SIZE):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("Invalid encrypted prefix length")
        self._generator = Isaac64(key) if key is not None else None
        self._remaining = limit if key is not None else 0
        self._word = b""
        self._word_offset = 0

    def decode(self, data: bytes) -> bytes:
        count = min(len(data), self._remaining)
        if not count:
            return data
        result = bytearray(data)
        for i in range(count):
            if self._word_offset == len(self._word):
                self._word = self._generator.next_word().to_bytes(8, "big")
                self._word_offset = 0
            result[i] ^= self._word[self._word_offset]
            self._word_offset += 1
        self._remaining -= count
        return bytes(result)


def decrypt_prefix(data: bytes, key: int | str | None,
                   limit: int = ENCRYPTED_PREFIX_SIZE) -> bytes:
    """Decode one complete file/prefix already in memory."""
    return PrefixDecoder(key, limit).decode(data)


def decode_prefix(data: bytes, key: int | str | None) -> bytes:
    """Decode a file's opening chunk and reject a missing/mismatched media key.

    Supply at least 12 bytes from offset zero, ideally the complete 128 KiB
    prefix. This validates an ISO BMFF file header, not full media integrity.
    Files already carrying a normal MP4 header are returned unchanged.
    """
    if len(data) < 12:
        raise ValueError("视频数据过短，无法识别 MP4 文件")
    if data[4:8] == b"ftyp":
        return data
    if key is None or key == "":
        raise ValueError("此视频需要解码密钥，请重新播放后再下载")
    decoded = decrypt_prefix(data, key)
    if decoded[4:8] != b"ftyp":
        raise ValueError("视频解码失败：密钥与视频不匹配，请重新播放后再下载")
    return decoded
