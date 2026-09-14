"""
Regression tests for defects found in the pre-1.0 review.

Each test here pins behaviour that was previously wrong, so the specific bug
cannot come back unnoticed.
"""

from __future__ import annotations

import json
import math
import random
import struct
import warnings

import pytest

from astral import codec, container, tmframe

try:
    from astral import rs_fec

    RS_AVAILABLE = True
except ImportError:  # optional extra: pip install astral-compression[rs]
    rs_fec = None
    RS_AVAILABLE = False
from astral import mckay_astral_integration as mckay
from astral.fountain import lt_decode_blocks, lt_encode_blocks
from astral.spacepacket import SpacePacketSequenceCounter, unwrap, wrap
from astral.textpack import decode_text, encode_text


def _telemetry(n: int, channels: int = 1) -> bytes:
    vals = [math.sin(i / 50.0) + 0.01 * (i % channels) for i in range(n)]
    return struct.pack(f">{n}f", *vals)


# --------------------------------------------------------------------------
# McKay header: 16-bit original length silently truncated anything over 64 KiB
# --------------------------------------------------------------------------


class TestMcKayLargePayloads:
    @pytest.mark.parametrize("n_floats", [4_000, 40_000, 100_000])
    def test_telemetry_roundtrip_above_64k(self, n_floats):
        data = _telemetry(n_floats)
        out = mckay.decompress(mckay.compress(data, "TELEMETRY", channels=1))
        assert len(out) == len(data)

    @pytest.mark.parametrize("n_floats", [4_000, 20_000, 60_000])
    def test_binary_float_roundtrip_above_64k(self, n_floats):
        data = struct.pack(f">{n_floats}f", *[i * 0.5 for i in range(n_floats)])
        assert mckay.decompress(mckay.compress(data, "BINARY")) == data

    def test_text_roundtrip_above_64k(self):
        data = ("Satellite telemetry nominal. Battery temperature nominal. " * 3000)
        data = data.encode()
        assert len(data) > 65535
        assert mckay.decompress(mckay.compress(data, "TEXT")) == data

    def test_header_declares_true_length(self):
        data = _telemetry(50_000)
        stream = mckay.compress(data, "TELEMETRY", channels=1)
        _v, _t, orig_len, _ch, _e, _p = mckay._parse_header(stream)
        assert orig_len == len(data)
        assert mckay.stats(stream)["original_size"] == len(data)

    def test_version_is_3(self):
        assert mckay.MCKAY_VERSION == 3
        assert mckay.compress(b"hello world")[2] == 3

    def test_legacy_v2_stream_still_decodes(self):
        """A v2 stream under the old 16-bit limit must keep working."""
        payload = _telemetry(1000)
        v3 = mckay.compress(payload, "TELEMETRY", channels=1)
        _v, tid, orig_len, ch, entropy, body = mckay._parse_header(v3)
        v2 = (
            mckay.MAGIC
            + bytes([2, tid])
            + mckay._pack_u16(orig_len)
            + bytes([ch, entropy])
            + body
        )
        assert len(mckay.decompress(v2)) == len(payload)

    def test_legacy_v2_truncated_length_is_reported_not_guessed(self):
        """v2 could not represent >=65535; say so instead of returning junk."""
        payload = _telemetry(40_000)
        v3 = mckay.compress(payload, "TELEMETRY", channels=1)
        _v, tid, _orig, ch, entropy, body = mckay._parse_header(v3)
        v2 = (
            mckay.MAGIC
            + bytes([2, tid])
            + mckay._pack_u16(0xFFFF)
            + bytes([ch, entropy])
            + body
        )
        with pytest.raises(ValueError, match="truncated"):
            mckay.decompress(v2)

    def test_ragged_telemetry_is_rejected(self):
        with pytest.raises(ValueError, match="multiple of 4"):
            mckay.compress(b"\x00" * 33, "TELEMETRY", channels=1)
        with pytest.raises(ValueError, match="whole number"):
            mckay.compress(_telemetry(10), "TELEMETRY", channels=3)


class TestRustDetection:
    def test_stub_namespace_package_is_not_treated_as_available(self):
        """
        The un-built `astral_compress/` directory imports as an empty
        namespace package. Selecting the Rust path against it made every
        compression emit a fallback warning.
        """
        if not mckay._RUST_AVAILABLE:
            assert not mckay._rust_has("compress_text")
        else:
            assert hasattr(mckay._ac, "compress_text")

    def test_compression_emits_no_fallback_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            mckay.decompress(mckay.compress(b"nominal telemetry " * 100, "TEXT"))
            mckay.decompress(mckay.compress(_telemetry(400), "TELEMETRY", channels=1))


# --------------------------------------------------------------------------
# CCSDS TM frames
# --------------------------------------------------------------------------


class TestTmFrameConformance:
    def test_randomizer_matches_ccsds_sequence(self):
        """
        The published CCSDS 131.0-B-5 PN sequence starts FF 48 0E C0 9A 0D 70
        BC. The previous implementation produced FF 1A AF ..., which no ground
        station would derandomize.
        """
        assert tmframe.apply_prng(bytes(8)) == bytes.fromhex("ff480ec09a0d70bc")

    def test_randomizer_is_self_inverse(self):
        data = bytes(range(256)) * 3
        assert tmframe.apply_prng(tmframe.apply_prng(data)) == data

    def test_randomizer_covers_the_whole_frame(self):
        """CCSDS randomizes header and FECF too, not just the data field."""
        wire = tmframe.encode_frames(b"A" * 100, scid=5, randomise=True)
        raw_header = wire[4 : 4 + tmframe.FRAME_HEADER_SIZE]
        plain = tmframe.encode_frames(b"A" * 100, scid=5, randomise=False)
        plain_header = plain[4 : 4 + tmframe.FRAME_HEADER_SIZE]
        assert raw_header != plain_header

    def test_roundtrip(self):
        data = bytes(range(256)) * 4
        wire = tmframe.encode_frames(data, scid=300, vcid=3)
        out, stats = tmframe.decode_frames(wire)
        assert stats["n_crc_errors"] == 0
        assert out[: len(data)] == data

    def test_resyncs_on_asm_when_capture_is_misaligned(self):
        data = b"ASTRAL" * 200
        wire = tmframe.encode_frames(data, scid=7)
        expected_frames = len(wire) // tmframe.WIRE_FRAME_SIZE
        for junk in (b"\x00", b"\xff" * 13, bytes(range(40))):
            out, stats = tmframe.decode_frames(junk + wire)
            assert stats["n_frames"] == expected_frames
            assert out[: len(data)] == data

    def test_vca_mode_sets_sync_flag(self):
        wire = tmframe.encode_frames(b"x" * 50, scid=1, mode=tmframe.MODE_VCA)
        info = tmframe.frame_info(wire)[0]
        assert info["sync_flag"] == 1

    def test_packet_mode_first_header_pointer_locates_packets(self):
        counter = SpacePacketSequenceCounter()
        packets = [
            wrap(b"\xa5\xe6" + bytes(30), "DETECT", counter) for _ in range(6)
        ]
        stream = b"".join(packets)
        wire = tmframe.encode_frames(stream, scid=9, mode=tmframe.MODE_PACKET)
        info = tmframe.frame_info(wire)
        assert all(i["sync_flag"] == 0 for i in info)
        # A ground station uses the FHP of the first frame to find packet 1.
        data, _stats = tmframe.decode_frames(wire)
        fhp = info[0]["first_header_pointer"]
        assert fhp == 0
        first = unwrap(data[fhp : fhp + len(packets[0])])
        assert first["msg_type"] == "DETECT"

    def test_packet_mode_fills_with_idle_packets(self):
        counter = SpacePacketSequenceCounter()
        stream = wrap(bytes(64), "TEXT", counter)
        wire = tmframe.encode_frames(stream, scid=9, mode=tmframe.MODE_PACKET)
        data, _ = tmframe.decode_frames(wire)
        parsed = tmframe.split_space_packets(data[: tmframe.FRAME_DATA_SIZE])
        assert parsed[0] == stream
        assert unwrap(parsed[1])["apid"] == 0x7FF  # idle fill

    def test_crc_error_drops_only_the_bad_frame(self):
        data = b"Z" * (tmframe.FRAME_DATA_SIZE + 10)
        wire = bytearray(tmframe.encode_frames(data, scid=11))
        wire[4 + 20] ^= 0xFF  # corrupt inside frame 1
        out, stats = tmframe.decode_frames(bytes(wire))
        assert stats["n_crc_errors"] >= 1
        assert stats["n_frames"] == 1


# --------------------------------------------------------------------------
# Reed-Solomon
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RS_AVAILABLE, reason="requires the 'rs' extra (reedsolo)")
class TestCcsdsReedSolomon:

    def test_codeblock_geometry(self):
        assert rs_fec.codeblock_sizes(223, 5) == (1115, 1275)
        assert rs_fec.codeblock_sizes(239, 5) == (1195, 1275)
        assert len(rs_fec.encode_codeblock(bytes(1115))) == 1275

    def test_frame_sized_codeblock(self):
        """A TM transfer frame is exactly one RS(255,223) I=5 codeblock."""
        assert tmframe.FRAME_SIZE == rs_fec.codeblock_sizes(223, 5)[0]

    def test_corrects_burst_up_to_interleaved_capacity(self):
        rng = random.Random(4)
        data = bytes(rng.randrange(256) for _ in range(1115))
        block = bytearray(rs_fec.encode_codeblock(data))
        # Interleave 5 x 16 correctable symbols = an 80-byte burst.
        for i in range(400, 480):
            block[i] ^= 0xFF
        out, corrected, ok = rs_fec.decode_codeblock(bytes(block))
        assert ok and out == data and corrected == 80

    def test_reports_uncorrectable_burst(self):
        data = bytes(1115)
        block = bytearray(rs_fec.encode_codeblock(data))
        for i in range(100, 400):
            block[i] ^= 0xA5
        _out, _corrected, ok = rs_fec.decode_codeblock(bytes(block))
        assert not ok

    def test_rs_255_239(self):
        rng = random.Random(5)
        data = bytes(rng.randrange(256) for _ in range(239 * 5))
        block = bytearray(rs_fec.encode_codeblock(data, k=239))
        for i in range(10, 50):
            block[i] ^= 0x3C
        out, _c, ok = rs_fec.decode_codeblock(bytes(block), k=239)
        assert ok and out == data

    def test_codeblock_stream_roundtrip(self):
        rng = random.Random(6)
        payload = bytes(rng.randrange(256) for _ in range(3000))
        enc = rs_fec.encode_codeblocks(payload)
        out, stats = rs_fec.decode_codeblocks(enc, original_length=len(payload))
        assert out == payload
        assert stats["n_uncorrectable_blocks"] == 0

    def test_atom_level_codes_are_still_available(self):
        atoms = bytes(range(32)) * 3
        enc = rs_fec.encode_stream(atoms, e=16)
        out, _corrected, uncorrectable = rs_fec.decode_stream(enc, e=16)
        assert out == atoms and uncorrectable == 0


# --------------------------------------------------------------------------
# Fountain code
# --------------------------------------------------------------------------


class TestFountainElimination:
    @pytest.mark.parametrize("K", [2, 5, 10, 37, 100])
    def test_recovers_within_modest_overhead(self, K):
        rng = random.Random(K)
        blocks = [bytes(rng.randrange(256) for _ in range(16)) for _ in range(K)]
        packets = lt_encode_blocks(blocks, seed=K + 1, num_packets=K * 3)
        for n in range(1, len(packets) + 1):
            decoded, _frac = lt_decode_blocks(packets[:n], K, 16)
            if decoded is not None:
                assert decoded == blocks
                # Peeling alone needed up to 1.8x; elimination brings this
                # down and, more importantly, stops it stalling outright.
                assert n <= max(K * 2, 8)
                break
        else:
            pytest.fail(f"never decoded with {len(packets)} packets at K={K}")

    def test_decoded_blocks_are_exact(self):
        rng = random.Random(99)
        blocks = [bytes(rng.randrange(256) for _ in range(16)) for _ in range(40)]
        packets = lt_encode_blocks(blocks, seed=3, num_packets=120)
        decoded, frac = lt_decode_blocks(packets, 40, 16)
        assert frac == 1.0 and decoded == blocks


# --------------------------------------------------------------------------
# Text payloads
# --------------------------------------------------------------------------


class TestTextExactness:
    @pytest.mark.parametrize(
        "text",
        [
            "Hello from the far side.",
            "KESTREL-2 reports NOMINAL status; battery LOW.",
            "a  b\tc\nd",
            "   leading and trailing   ",
            "The the THE Title",
            "unicode: café naïve 日本語",
            "",
        ],
    )
    def test_exact_roundtrip(self, text):
        assert decode_text(encode_text(text)) == text

    def test_fuzz_roundtrip(self):
        rng = random.Random(11)
        alphabet = list(" \t\nabcXYZ.,;:-the world satellite Battery Nominal 123")
        for _ in range(500):
            s = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 80)))
            assert decode_text(encode_text(s)) == s

    def test_still_compresses_mission_prose(self):
        text = "The satellite battery status is nominal and the link is normal. " * 20
        assert len(encode_text(text)) < len(text.encode()) * 0.5

    def test_malformed_payload_raises(self):
        with pytest.raises(ValueError):
            decode_text(b"\x02\x00\x63")  # unknown tag


# --------------------------------------------------------------------------
# Atom framing
# --------------------------------------------------------------------------


class TestAtomResync:
    def _stream(self):
        return codec.pack_message(
            {"type": "DETECT", "object": "H2O_ICE", "lat": 1.0, "lon": 2.0}
        )

    @pytest.mark.parametrize("junk", [b"\x00", b"\xa5" * 5, bytes(range(17))])
    def test_byte_misaligned_stream_still_decodes(self, junk):
        result = codec.unpack_stream(junk + self._stream())
        assert result["complete"] is True

    def test_partial_atom_gap_does_not_lose_the_rest(self):
        blob = self._stream()
        damaged = blob[:100] + blob[137:]  # remove a non-atom-aligned slice
        result = codec.unpack_stream(damaged)
        assert result["received_atoms"] >= len(blob) // 32 - 3

    def test_corrupt_atom_is_rejected_by_crc(self):
        blob = bytearray(self._stream())
        blob[15] ^= 0xFF
        atoms = container.parse_atoms(bytes(blob))
        assert len(atoms) == len(blob) // 32 - 1


# --------------------------------------------------------------------------
# McKay over the fountain/atom layer
# --------------------------------------------------------------------------


class TestMcKayOverAtoms:
    def test_roundtrip_and_wire_level_compression(self):
        payload = ("Satellite telemetry nominal. Battery temperature nominal. " * 200)
        payload = payload.encode()
        stream = codec.pack_mckay_message(payload, "TEXT")
        result = codec.unpack_mckay_stream(stream)
        assert result["data"] == payload
        assert result["complete"] is True
        # The whole point: smaller on the wire than the source, redundancy
        # included.
        assert len(stream) < len(payload)

    def test_telemetry_roundtrip(self):
        data = _telemetry(8000)
        stream = codec.pack_mckay_message(data, "TELEMETRY", channels=1)
        result = codec.unpack_mckay_stream(stream)
        assert len(result["data"]) == len(data)

    def test_gist_survives_total_fountain_loss(self):
        payload = b"telemetry nominal " * 500
        stream = codec.pack_mckay_message(payload, "TEXT")
        atoms = [stream[i : i + 32] for i in range(0, len(stream), 32)]
        gist_only = b"".join(
            a for a in atoms if a[9] in (container.HEADER_GIST, container.MCKAY_GIST)
        )
        result = codec.unpack_stream(gist_only)
        assert result["complete"] is False
        assert result["mckay"]["original_size"] == len(payload)
        assert result["mckay"]["data_type"] == "TEXT"
        assert result["mckay"]["ratio"] > 1.0

    def test_rejects_non_mckay_stream(self):
        stream = codec.pack_text_message("hello")
        assert "error" in codec.unpack_mckay_stream(stream)

    def test_bad_data_type_rejected(self):
        with pytest.raises(ValueError):
            codec.pack_mckay_message(b"x", "NOT_A_TYPE")


class TestHeaderRedundancy:
    def test_header_redundancy_for_matches_probability(self):
        assert codec.header_redundancy_for(0.8) == 21
        assert codec.header_redundancy_for(0.4) == 6
        assert 0.8 ** codec.header_redundancy_for(0.8) < 0.01

    def test_gist_survives_80_percent_loss_when_configured(self):
        msg = {"type": "DETECT", "object": "H2O_ICE", "lat": -43.7, "lon": 130.2}
        blob = codec.pack_message(
            msg,
            extra_fountain=20,
            header_redundancy=codec.header_redundancy_for(0.8),
        )
        atoms = [blob[i : i + 32] for i in range(0, len(blob), 32)]
        survivors = 0
        trials = 200
        for t in range(trials):
            rng = random.Random(t)
            kept = b"".join(a for a in atoms if rng.random() >= 0.8)
            if codec.unpack_stream(kept).get("gist"):
                survivors += 1
        assert survivors / trials > 0.95

    def test_invalid_redundancy_rejected(self):
        with pytest.raises(ValueError):
            codec.pack_text_message("hi", header_redundancy=0)


class TestPayloadLimits:
    def test_oversized_payload_is_rejected_with_a_clear_error(self):
        limit = codec.max_payload_bytes()
        with pytest.raises(ValueError, match="too large"):
            codec._check_payload_size(limit + 1)

    def test_limit_is_consistent_with_atom_counter(self):
        limit = codec.max_payload_bytes()
        k = limit // codec.SYMBOL_SIZE
        atoms = codec.HEADER_REDUNDANCY + k + max(10, k)
        assert atoms <= codec.MAX_ATOMS


class TestSpacePacketWrapping:
    def test_wrap_preserves_the_input_stream(self):
        """`wrap-sp` used to re-pack a fresh message and drop its input."""
        counter = SpacePacketSequenceCounter()
        stream = codec.pack_message({"type": "DETECT", "object": "BASALT"})
        packet = wrap(stream, "DETECT", counter)
        assert unwrap(packet)["astral_stream"] == stream

    def test_sequence_counter_set(self):
        counter = SpacePacketSequenceCounter()
        counter.set(0x010, 16383)
        assert counter.next(0x010) == 16383
        assert counter.next(0x010) == 0


class TestCliSurface:
    def test_wrap_sp_roundtrip(self, tmp_path):
        from astral.cli import main

        src = tmp_path / "msg.json"
        src.write_text(json.dumps({"type": "DETECT", "object": "H2O_ICE"}))
        packed = tmp_path / "out.bin"
        wrapped = tmp_path / "sp.bin"
        assert main(["pack", str(src), str(packed)]) == 0
        assert main(["wrap-sp", str(packed), str(wrapped), "--msg-type", "DETECT"]) == 0
        assert unwrap(wrapped.read_bytes())["astral_stream"] == packed.read_bytes()

    def test_pack_and_unpack_mckay(self, tmp_path):
        from astral.cli import main

        src = tmp_path / "data.bin"
        src.write_bytes(b"nominal telemetry downlink " * 400)
        packed = tmp_path / "mk.bin"
        out = tmp_path / "recovered.bin"
        assert main(["pack-mckay", str(src), str(packed), "--type", "TEXT"]) == 0
        assert main(["unpack-mckay", str(packed), str(out)]) == 0
        assert out.read_bytes() == src.read_bytes()

    def test_simulate_is_reproducible_with_seed(self, tmp_path):
        from astral.cli import main

        src = tmp_path / "msg.json"
        src.write_text(json.dumps({"type": "DETECT", "object": "H2O_ICE"}))
        packed = tmp_path / "out.bin"
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        main(["pack", str(src), str(packed)])
        main(["simulate", str(packed), str(a), "--drop", "0.5", "--seed", "7"])
        main(["simulate", str(packed), str(b), "--drop", "0.5", "--seed", "7"])
        assert a.read_bytes() == b.read_bytes()


class TestRedundancyControl:
    """The fountain overhead used to be pinned at 100% with no way down."""

    def test_atom_count_scales_with_redundancy(self):
        assert codec.fountain_atom_count(100, redundancy=1.0) == 200
        assert codec.fountain_atom_count(100, redundancy=0.3) == 130
        assert codec.fountain_atom_count(100, redundancy=0.0) == 110  # min floor
        assert codec.fountain_atom_count(4, redundancy=0.3) == 14  # min floor

    def test_lower_redundancy_produces_a_smaller_stream_that_still_decodes(self):
        # Large enough that K clears the min_redundancy floor.
        payload = bytes(
            (i * 37 + (i // 251)) % 256 for i in range(40_000)
        )
        big = codec.pack_mckay_message(payload, "BINARY", redundancy=1.0)
        small = codec.pack_mckay_message(payload, "BINARY", redundancy=0.3)
        assert len(small) < len(big) * 0.8
        assert codec.unpack_mckay_stream(small)["data"] == payload

    def test_negative_redundancy_rejected(self):
        with pytest.raises(ValueError):
            codec.pack_text_message("hi", redundancy=-0.1)
