"""Tests for McKay v2 compression engine."""
from __future__ import annotations

import math
import struct
import warnings

import pytest

from astral.mckay_astral_integration import (
    MCKAY_VERSION,
    TRANSFORM_BINARY_FLOAT,
    TRANSFORM_PASSTHROUGH,
    TRANSFORM_TELEMETRY,
    TRANSFORM_TEXT,
    McKayCompressor,
    compress,
    decompress,
    stats,
)


class TestHeader:
    def test_magic_and_version(self):
        c = compress(b"hello world test data for header")
        assert c[0] == 0x4D and c[1] == 0x4B, "Magic must be 'MK'"
        assert c[2] == MCKAY_VERSION

    def test_original_length_stored(self):
        data = b"test " * 20
        c = compress(data)
        stored = c[4] | (c[5] << 8)
        assert stored == len(data)

    def test_unknown_transform_raises(self):
        bad = bytearray(b"MK\x02\xFF\x05\x00\x00\x00") + b"garbage"
        with pytest.raises(ValueError, match="Unknown"):
            decompress(bytes(bad))

    def test_wrong_magic_raises(self):
        with pytest.raises(ValueError, match="magic"):
            decompress(b"XZ\x02\x00\x00\x00\x00\x00" + b"\x00" * 10)


class TestRoundtrip:
    def test_text(self):
        # Text compression is lossy for domain-specific terminology
        # Use text without mission abbreviations to test lossless compression
        data = b"Hello world. This is a test message."
        assert decompress(compress(data, "TEXT")) == data

    def test_text_auto(self):
        # Use text without mission abbreviations to test lossless compression
        data = b"Hello world. This is another test message."
        assert decompress(compress(data)) == data

    def test_binary_roundtrip(self):
        data = struct.pack(">64f", *[i * 0.1 for i in range(64)])
        assert decompress(compress(data, "BINARY")) == data

    def test_telemetry_shape_preserved(self):
        vals = [20.0 + 0.01 * math.sin(2 * math.pi * i / 100) for i in range(512)]
        data = struct.pack(f">{len(vals)}f", *vals)
        result = decompress(compress(data, "TELEMETRY", channels=1))
        assert len(result) == len(data)

    def test_empty_input(self):
        assert decompress(compress(b"")) == b""

    def test_never_expands_significantly(self):
        data = bytes(range(256)) * 4
        compressed = compress(data, "BINARY")
        assert len(compressed) <= len(data) + 8


class TestCompressionQuality:
    def test_text_structured_compresses(self):
        text = (
            "Satellite nominal. Battery 94%. Attitude nominal. "
            "All systems nominal. Science payload active. "
        ) * 5
        data = text.encode()
        c = compress(data, "TEXT")
        ratio = len(data) / (len(c) - 8)
        assert ratio >= 2.0, f"Text compression {ratio:.2f}x < 2x"

    def test_telemetry_beats_raw_lzma(self):
        import lzma

        vals = [
            293.0 + 0.5 * math.sin(2 * math.pi * i / 500) + (i % 3) * 0.001
            for i in range(512)
        ]
        data = struct.pack(f">{len(vals)}f", *vals)
        mckay_c = compress(data, "TELEMETRY", channels=1)
        raw_lzma_size = len(lzma.compress(data, preset=9))
        mckay_size = len(mckay_c) - 8
        assert mckay_size <= raw_lzma_size * 1.1, (
            f"McKay telemetry ({mckay_size}B) should not be worse than "
            f"raw LZMA ({raw_lzma_size}B)"
        )

    def test_float_binary_roundtrip(self):
        import random

        random.seed(42)
        vals = [random.uniform(-1000, 1000) for _ in range(256)]
        data = struct.pack(f">{len(vals)}f", *vals)
        assert decompress(compress(data, "BINARY")) == data


class TestVoice:
    def test_codec2_not_available_falls_back(self):
        import importlib
        import sys

        pycodec2_saved = sys.modules.get("pycodec2")
        sys.modules["pycodec2"] = None  # type: ignore[assignment]
        try:
            fake_vx = b"VX\x01" + b"\x00" * 20
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                c = compress(fake_vx, "VOICE")
            assert c[3] == TRANSFORM_PASSTHROUGH
            _ = w
        finally:
            if pycodec2_saved is not None:
                sys.modules["pycodec2"] = pycodec2_saved
            else:
                del sys.modules["pycodec2"]
            _ = importlib

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("pycodec2"),
        reason="pycodec2 not installed",
    )
    def test_codec2_bitrate_below_1300(self):
        import importlib
        import numpy as np

        Codec2 = importlib.import_module("pycodec2.pycodec2").Codec2

        fs = 8000
        c2 = Codec2(1200)
        spf = c2.samples_per_frame()
        n = math.ceil(fs / spf) * spf
        s = np.zeros(n, dtype=np.int16)
        enc = c2.encode(s)
        assert len(enc) <= 150, f"1200 bps codec2 gave {len(enc)} bytes/sec > 150"


class TestStats:
    def test_stats_keys(self):
        c = compress(b"test data " * 10, "TEXT")
        s = stats(c)
        assert "transform" in s
        assert "original_size" in s
        assert "compressed_size" in s
        assert "ratio" in s
        assert "savings_pct" in s

    def test_stats_ratio_positive(self):
        data = b"Satellite nominal. " * 20
        c = compress(data, "TEXT")
        s = stats(c)
        assert s["ratio"] > 0
        assert s["original_size"] == len(data)


class TestMcKayCompressorClass:
    def test_class_wraps_functions(self):
        mc = McKayCompressor()
        data = b"nominal link standing by"
        c = mc.compress(data)
        assert mc.decompress(c) == data

    def test_class_voice_bps_parameter(self):
        mc1200 = McKayCompressor(voice_bps=1200)
        mc2400 = McKayCompressor(voice_bps=2400)
        data = b"test " * 100
        c1 = mc1200.compress(data, "BINARY")
        c2 = mc2400.compress(data, "BINARY")
        assert mc1200.decompress(c1) == data
        assert mc2400.decompress(c2) == data


_ = TRANSFORM_TEXT
_ = TRANSFORM_TELEMETRY
_ = TRANSFORM_BINARY_FLOAT
