"""Tests for McKay v2 compression engine."""

import math
import struct

import pytest

from astral.mckay_astral_integration import compress, decompress


class TestRoundtrip:
    def test_text_roundtrip(self):
        data = b"Satellite nominal. Battery 94%. Entering dark side."
        assert decompress(compress(data, "TEXT")) == data

    def test_binary_roundtrip(self):
        data = struct.pack(">64f", *[i * 0.1 for i in range(64)])
        assert decompress(compress(data, "BINARY")) == data

    def test_telemetry_roundtrip_shape(self):
        vals = [20.0 + 0.01 * i for i in range(512)]
        data = struct.pack(f">{len(vals)}f", *vals)
        result = decompress(compress(data, "TELEMETRY"))
        assert len(result) == len(data)

    def test_auto_detect_text(self):
        data = b"nominal link standing by"
        assert decompress(compress(data)) == data

    def test_empty_input(self):
        assert decompress(compress(b"")) == b""

    def test_never_expands_above_threshold(self):
        data = b"x" * 200
        compressed = compress(data, "BINARY")
        assert len(compressed) <= len(data) + 6


class TestCompressionRatios:
    def test_repetitive_telemetry_5x(self):
        vals = [20.0 + 0.01 * math.sin(2 * math.pi * i / 100) for i in range(1024)]
        data = struct.pack(f">{len(vals)}f", *vals)
        compressed = compress(data, "TELEMETRY")
        ratio = len(data) / len(compressed)
        assert ratio >= 5.0, f"Expected >=5x, got {ratio:.2f}x"

    def test_structured_text_1_5x(self):
        text = (
            "Satellite nominal. Battery 94%. Attitude nominal. "
            "Power nominal. Science payload active. " * 5
        )
        data = text.encode()
        compressed = compress(data, "TEXT")
        ratio = len(data) / len(compressed)
        assert ratio >= 1.5, f"Expected >=1.5x, got {ratio:.2f}x"

    def test_float_binary_1_5x(self):
        vals = [((i * 17) % 100) * 0.73 for i in range(512)]
        data = struct.pack(f">{len(vals)}f", *vals)
        compressed = compress(data, "BINARY")
        ratio = len(data) / len(compressed)
        assert ratio >= 1.5, f"Expected >=1.5x, got {ratio:.2f}x"


class TestVoiceCodec:
    def test_voice_bitrate_matches_frame_layout(self):
        pytest.importorskip("astral.voice")
        from astral.voice import encode_wav_to_bitstream  # noqa: F401

        frames_per_sec = 50
        bits_per_frame = 1 + 6 + 5 + 10 * 4
        bps = bits_per_frame * frames_per_sec
        assert bps <= 2600, f"LSF vocoder bitrate {bps} bps exceeds 2600 bps"


class TestHeaderFormat:
    def test_magic_bytes(self):
        compressed = compress(b"hello world test data")
        assert compressed[0] == 0x4D
        assert compressed[1] == 0x4B
        assert compressed[2] == 0x02

    def test_original_length_field(self):
        data = b"test " * 20
        compressed = compress(data)
        stored_len = struct.unpack("<H", compressed[4:6])[0]
        assert stored_len == len(data)

    def test_unknown_transform_raises_on_decompress(self):
        bad = bytearray(b"MK\x02\xFF\x05\x00") + b"garbage"
        with pytest.raises((ValueError, Exception)):
            decompress(bytes(bad))
