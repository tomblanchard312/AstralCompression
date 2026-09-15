"""
Regression tests for defects found in the pre-1.0 review.

Each test here pins behaviour that was previously wrong, so the specific bug
cannot come back unnoticed.
"""

from __future__ import annotations

import json
import math
import os
import random
import struct
import warnings
import zlib

import pytest

from gistlink import codec, container, tmframe

try:
    from gistlink import rs_fec

    RS_AVAILABLE = True
except ImportError:  # optional extra: pip install gistlink[rs]
    rs_fec = None
    RS_AVAILABLE = False
from gistlink import compress as compress
from gistlink.fountain import lt_decode_blocks, lt_encode_blocks
from gistlink.spacepacket import SpacePacketSequenceCounter, unwrap, wrap
from gistlink.textpack import decode_text, encode_text


def _telemetry(n: int, channels: int = 1) -> bytes:
    vals = [math.sin(i / 50.0) + 0.01 * (i % channels) for i in range(n)]
    return struct.pack(f">{n}f", *vals)


# --------------------------------------------------------------------------
# container header: 16-bit original length silently truncated anything over 64 KiB
# --------------------------------------------------------------------------


class TestCompressLargePayloads:
    @pytest.mark.parametrize("n_floats", [4_000, 40_000, 100_000])
    def test_telemetry_roundtrip_above_64k(self, n_floats):
        data = _telemetry(n_floats)
        out = compress.decompress(compress.compress(data, "TELEMETRY", channels=1))
        assert len(out) == len(data)

    @pytest.mark.parametrize("n_floats", [4_000, 20_000, 60_000])
    def test_binary_float_roundtrip_above_64k(self, n_floats):
        data = struct.pack(f">{n_floats}f", *[i * 0.5 for i in range(n_floats)])
        assert compress.decompress(compress.compress(data, "BINARY")) == data

    def test_text_roundtrip_above_64k(self):
        data = ("Satellite telemetry nominal. Battery temperature nominal. " * 3000)
        data = data.encode()
        assert len(data) > 65535
        assert compress.decompress(compress.compress(data, "TEXT")) == data

    def test_header_declares_true_length(self):
        data = _telemetry(50_000)
        stream = compress.compress(data, "TELEMETRY", channels=1)
        _v, _t, orig_len, _ch, _e, _p = compress._parse_header(stream)
        assert orig_len == len(data)
        assert compress.stats(stream)["original_size"] == len(data)

    def test_version_is_3(self):
        assert compress.COMPRESS_VERSION == 3
        assert compress.compress(b"hello world")[2] == 3

    def test_legacy_v2_stream_still_decodes(self):
        """A v2 stream under the old 16-bit limit must keep working."""
        payload = _telemetry(1000)
        v3 = compress.compress(payload, "TELEMETRY", channels=1)
        _v, tid, orig_len, ch, entropy, body = compress._parse_header(v3)
        v2 = (
            compress.MAGIC
            + bytes([2, tid])
            + compress._pack_u16(orig_len)
            + bytes([ch, entropy])
            + body
        )
        assert len(compress.decompress(v2)) == len(payload)

    def test_legacy_v2_truncated_length_is_reported_not_guessed(self):
        """v2 could not represent >=65535; say so instead of returning junk."""
        payload = _telemetry(40_000)
        v3 = compress.compress(payload, "TELEMETRY", channels=1)
        _v, tid, _orig, ch, entropy, body = compress._parse_header(v3)
        v2 = (
            compress.MAGIC
            + bytes([2, tid])
            + compress._pack_u16(0xFFFF)
            + bytes([ch, entropy])
            + body
        )
        with pytest.raises(ValueError, match="truncated"):
            compress.decompress(v2)

    def test_ragged_telemetry_is_rejected(self):
        with pytest.raises(ValueError, match="multiple of 4"):
            compress.compress(b"\x00" * 33, "TELEMETRY", channels=1)
        with pytest.raises(ValueError, match="whole number"):
            compress.compress(_telemetry(10), "TELEMETRY", channels=3)


class TestRustDetection:
    def test_stub_namespace_package_is_not_treated_as_available(self):
        """
        The un-built `gistlink_native/` directory imports as an empty
        namespace package. Selecting the Rust path against it made every
        compression emit a fallback warning.
        """
        if not compress._RUST_AVAILABLE:
            assert not compress._rust_has("compress_text")
        else:
            assert hasattr(compress._ac, "compress_text")

    def test_compression_emits_no_fallback_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            compress.decompress(compress.compress(b"nominal telemetry " * 100, "TEXT"))
            compress.decompress(compress.compress(_telemetry(400), "TELEMETRY", channels=1))


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
        data = b"GistLink" * 200
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
# compression over the fountain/atom layer
# --------------------------------------------------------------------------


class TestCompressionOverAtoms:
    def test_roundtrip_and_wire_level_compression(self):
        payload = ("Satellite telemetry nominal. Battery temperature nominal. " * 200)
        payload = payload.encode()
        stream = codec.pack_compressed_message(payload, "TEXT")
        result = codec.unpack_compressed_stream(stream)
        assert result["data"] == payload
        assert result["complete"] is True
        # The whole point: smaller on the wire than the source, redundancy
        # included.
        assert len(stream) < len(payload)

    def test_telemetry_roundtrip(self):
        data = _telemetry(8000)
        stream = codec.pack_compressed_message(data, "TELEMETRY", channels=1)
        result = codec.unpack_compressed_stream(stream)
        assert len(result["data"]) == len(data)

    def test_gist_survives_total_fountain_loss(self):
        payload = b"telemetry nominal " * 500
        stream = codec.pack_compressed_message(payload, "TEXT")
        atoms = [stream[i : i + 32] for i in range(0, len(stream), 32)]
        gist_only = b"".join(
            a for a in atoms if a[9] in (container.HEADER_GIST, container.COMPRESSED_GIST)
        )
        result = codec.unpack_stream(gist_only)
        assert result["complete"] is False
        assert result["compression"]["original_size"] == len(payload)
        assert result["compression"]["data_type"] == "TEXT"
        assert result["compression"]["ratio"] > 1.0

    def test_rejects_non_compressed_stream(self):
        stream = codec.pack_text_message("hello")
        assert "error" in codec.unpack_compressed_stream(stream)

    def test_bad_data_type_rejected(self):
        with pytest.raises(ValueError):
            codec.pack_compressed_message(b"x", "NOT_A_TYPE")


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
        assert unwrap(packet)["gistlink_stream"] == stream

    def test_sequence_counter_set(self):
        counter = SpacePacketSequenceCounter()
        counter.set(0x010, 16383)
        assert counter.next(0x010) == 16383
        assert counter.next(0x010) == 0


class TestCliSurface:
    def test_wrap_sp_roundtrip(self, tmp_path):
        from gistlink.cli import main

        src = tmp_path / "msg.json"
        src.write_text(json.dumps({"type": "DETECT", "object": "H2O_ICE"}))
        packed = tmp_path / "out.bin"
        wrapped = tmp_path / "sp.bin"
        assert main(["pack", str(src), str(packed)]) == 0
        assert main(["wrap-sp", str(packed), str(wrapped), "--msg-type", "DETECT"]) == 0
        assert unwrap(wrapped.read_bytes())["gistlink_stream"] == packed.read_bytes()

    def test_pack_and_unpack_compress(self, tmp_path):
        from gistlink.cli import main

        src = tmp_path / "data.bin"
        src.write_bytes(b"nominal telemetry downlink " * 400)
        packed = tmp_path / "mk.bin"
        out = tmp_path / "recovered.bin"
        assert main(["pack-file", str(src), str(packed), "--type", "TEXT"]) == 0
        assert main(["unpack-file", str(packed), str(out)]) == 0
        assert out.read_bytes() == src.read_bytes()

    def test_simulate_is_reproducible_with_seed(self, tmp_path):
        from gistlink.cli import main

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
        big = codec.pack_compressed_message(payload, "BINARY", redundancy=1.0)
        small = codec.pack_compressed_message(payload, "BINARY", redundancy=0.3)
        assert len(small) < len(big) * 0.8
        assert codec.unpack_compressed_stream(small)["data"] == payload

    def test_negative_redundancy_rejected(self):
        with pytest.raises(ValueError):
            codec.pack_text_message("hi", redundancy=-0.1)


class TestPayloadIntegrity:
    """
    A corrupt atom that slips past its CRC-8 (1 in 256 do) poisons the XOR
    reconstruction. Nothing used to check the result, so the decoder could
    report a clean decode and hand back different bytes than were sent.
    """

    @staticmethod
    def _corrupt_one_atom(blob: bytes, which: int = 0) -> bytes:
        from gistlink.crc import crc8_j1850

        atoms = [bytearray(blob[i : i + 32]) for i in range(0, len(blob), 32)]
        fountain = [a for a in atoms if a[9] == container.FOUNTAIN_PACKET]
        hit = fountain[which % len(fountain)]
        hit[12] ^= 0x08
        hit[31] = crc8_j1850(bytes(hit[:31])) & 0xFF  # CRC-8 now passes again
        return b"".join(bytes(a) for a in atoms)

    def test_clean_stream_reports_integrity_ok(self):
        stream = codec.pack_text_message("nominal link, standing by")
        result = codec.unpack_stream(stream)
        assert result["complete"] is True
        assert result["integrity_ok"] is True

    def test_header_carries_the_payload_crc(self):
        payload = b"telemetry nominal " * 20
        stream = codec.pack_compressed_message(payload, "TEXT")
        header = next(
            a.payload
            for a in container.parse_atoms(stream)
            if a.atom_type == container.HEADER_GIST
        )
        crc = int.from_bytes(
            header[codec.PAYLOAD_CRC_OFFSET : codec.PAYLOAD_CRC_OFFSET + 4],
            "little",
        )
        assert crc != 0

    def test_atoms_declare_format_version_2(self):
        stream = codec.pack_text_message("hello")
        assert all(a.version == container.ATOM_VERSION
                   for a in container.parse_atoms(stream))

    def test_corrupt_atom_is_never_reported_as_a_clean_decode(self):
        payload = bytes((i * 7 + 11) % 256 for i in range(600))
        silently_wrong = 0
        detected = 0
        for seed in range(30):
            blob = codec.pack_compressed_message(
                payload, "BINARY", message_id=seed + 1, redundancy=0.05,
                min_redundancy=1,
            )
            damaged = self._corrupt_one_atom(blob, seed)
            result = codec.unpack_stream(damaged)
            if result.get("integrity_ok") is False:
                detected += 1
                assert result["complete"] is False
                assert result["message"] is None
                assert "integrity" in result["error"]
            elif result["complete"] and result.get("data") != payload:
                silently_wrong += 1
        assert silently_wrong == 0
        # The corruption has to actually reach the solution sometimes, or this
        # test proves nothing.
        assert detected > 0

    def test_gist_still_available_when_integrity_fails(self):
        payload = bytes((i * 13 + 5) % 256 for i in range(600))
        for seed in range(30):
            blob = codec.pack_compressed_message(
                payload, "BINARY", message_id=seed + 1, redundancy=0.05,
                min_redundancy=1,
            )
            result = codec.unpack_stream(self._corrupt_one_atom(blob, seed))
            if result.get("integrity_ok") is False:
                # A failed payload still leaves the operator the gist.
                assert result["gist"]["type"] == "COMPRESS"
                assert result["compression"]["original_size"] == len(payload)
                return
        pytest.fail("no corruption reached the fountain solution")


class TestWireFormatStability:
    """
    The sampler and the CRCs were rewritten for speed. Both define the wire
    format and are mirrored by the Rust and C ports, so they must stay
    bit-identical to the reference definitions.
    """

    @staticmethod
    def _dense_sample(seed, n, k):
        """The original materialised Fisher-Yates, kept as the reference."""
        from gistlink.fountain import _Xorshift32

        rng = _Xorshift32(seed)
        pool = list(range(n))
        for i in range(k):
            j = i + (rng.next_u32() % (n - i))
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]

    def test_prng_test_vector(self):
        from gistlink.fountain import _Xorshift32

        assert _Xorshift32(1).next_u32() == 270369

    def test_sparse_sampler_matches_dense_reference(self):
        from gistlink.fountain import _Xorshift32

        for seed in range(1, 60):
            for n in (1, 2, 3, 5, 16, 97, 256, 1000):
                for k in range(0, min(n, 8) + 1):
                    assert (
                        _Xorshift32(seed).sample_indices(n, k)
                        == self._dense_sample(seed, n, k)
                    ), f"sampler diverged at seed={seed} n={n} k={k}"

    def test_crc8_matches_bitwise_reference(self):
        from gistlink.crc import crc8_j1850

        def reference(data):
            crc = 0xFF
            for b in data:
                crc ^= b
                for _ in range(8):
                    crc = (
                        ((crc << 1) ^ 0x1D) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
                    )
            return crc ^ 0xFF

        rng = random.Random(5)
        cases = [b"", b"\x00", bytes(range(256))]
        cases += [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64)))
                  for _ in range(100)]
        for data in cases:
            assert crc8_j1850(data) == reference(data)

    def test_crc16_check_value(self):
        from gistlink.crc import crc16_ccitt

        # The published CRC-16/CCITT-FALSE check value, which is the variant
        # CCSDS uses for the TM frame FECF.
        assert crc16_ccitt(b"123456789") == 0x29B1


class TestLargeMessagePerformance:
    """
    Decoding used to rescan every equation for every solved block, which is
    quadratic and made a few hundred KB impractical. These bounds are loose
    enough not to flake on a slow machine but tight enough to catch a return
    to quadratic behaviour, which was 10x slower.
    """

    def test_large_fountain_roundtrip_is_exact_and_prompt(self):
        import time

        rng = random.Random(9)
        K = 1500
        blocks = [bytes(rng.randrange(256) for _ in range(16)) for _ in range(K)]
        start = time.perf_counter()
        packets = lt_encode_blocks(blocks, seed=11, num_packets=K * 2)
        decoded, fraction = lt_decode_blocks(packets, K, 16)
        elapsed = time.perf_counter() - start
        assert fraction == 1.0 and decoded == blocks
        assert elapsed < 20.0, f"K={K} roundtrip took {elapsed:.1f}s"

    def test_200kb_message_roundtrip(self):
        import time

        rng = random.Random(3)
        payload = bytes(rng.randrange(256) for _ in range(200_000))
        start = time.perf_counter()
        stream = codec.pack_compressed_message(payload, "BINARY", redundancy=0.3)
        result = codec.unpack_compressed_stream(stream)
        elapsed = time.perf_counter() - start
        assert result["data"] == payload
        assert result["integrity_ok"] is True
        assert elapsed < 60.0, f"200 KB roundtrip took {elapsed:.1f}s"


class TestPythonVersionSupport:
    """
    pyproject declares `requires-python = ">=3.9"`. PEP 604 unions (`X | None`)
    and PEP 585 builtin generics are evaluated at definition time when they
    appear in a signature, so a module using them without
    `from __future__ import annotations` fails to import on 3.9 even though it
    parses fine. That is invisible on a modern interpreter and broke the 3.9
    CI leg.
    """

    def test_no_runtime_evaluated_pep604_unions(self):
        import pathlib
        import re as _re

        package = pathlib.Path(__file__).resolve().parent.parent / "gistlink"
        offenders = []
        signature = _re.compile(r"^\s*def .*\|", _re.MULTILINE)
        for path in sorted(package.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "from __future__ import annotations" in source:
                continue
            for match in signature.finditer(source):
                line = match.group(0).strip()
                if "|" in line.split("#")[0]:
                    offenders.append(f"{path.name}: {line}")
        assert not offenders, (
            "these signatures are evaluated at import and need "
            "`from __future__ import annotations` for Python 3.9: "
            + "; ".join(offenders)
        )

    def test_declared_minimum_python_is_still_3_9(self):
        import pathlib

        pyproject = (
            pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        ).read_text(encoding="utf-8")
        # If this is ever raised, the guard above can be relaxed to match.
        assert 'requires-python = ">=3.9"' in pyproject


class TestHeaderBoundIntegrity:
    """
    The checksum originally covered only the payload. A corrupt HEADER_GIST
    atom that passed its own CRC-8 could therefore flip the gist type, which
    selects the decoder, and still report `integrity_ok: True`: an intact TEXT
    payload came back as a fabricated STATUS report with invented lat/lon.
    Reported by Codex on PR #5.
    """

    @staticmethod
    def _flip_gist_type(stream: bytes, copies=None) -> bytes:
        from gistlink.crc import crc8_j1850

        atoms = [bytearray(stream[i : i + 32]) for i in range(0, len(stream), 32)]
        headers = [a for a in atoms if a[9] == container.HEADER_GIST]
        for a in headers if copies is None else headers[:copies]:
            a[21] ^= 0x01  # lowest bit of the gist's type field
            a[31] = crc8_j1850(bytes(a[:31])) & 0xFF  # atom CRC-8 passes again
        return b"".join(bytes(a) for a in atoms)

    def test_checksum_covers_the_header(self):
        payload = b"burn 12.5 s at T+0300"
        stream = codec.pack_text_message(payload.decode())
        damaged = self._flip_gist_type(stream)  # every copy
        result = codec.unpack_stream(damaged)
        assert result["integrity_ok"] is False
        assert result["complete"] is False
        assert result["message"] is None

    def test_one_corrupt_header_copy_is_outvoted(self):
        text = "burn 12.5 s at T+0300"
        stream = codec.pack_text_message(text)
        damaged = self._flip_gist_type(stream, copies=1)
        result = codec.unpack_stream(damaged)
        assert result["gist"]["type"] == "TEXT"
        assert result["complete"] is True
        assert result["integrity_ok"] is True
        assert result["message"]["text"] == text

    def test_crc_is_not_a_plain_payload_checksum(self):
        """The stored value must depend on the header, not the payload alone."""
        payload = b"some payload bytes"
        stream = codec.pack_text_message(payload.decode())
        header = next(
            a.payload
            for a in container.parse_atoms(stream)
            if a.atom_type == container.HEADER_GIST
        )
        stored = int.from_bytes(
            header[codec.PAYLOAD_CRC_OFFSET : codec.PAYLOAD_CRC_OFFSET + 4], "little"
        )
        body = b"".join(
            a.payload[5:21]
            for a in container.parse_atoms(stream)
            if a.atom_type == container.FOUNTAIN_PACKET
        )
        assert stored != zlib.crc32(body)
        # It is reproducible from the header and the payload together.
        assert stored == codec.integrity_crc(header, encode_text(payload.decode()))

    def test_checksum_slot_is_excluded_from_its_own_input(self):
        header = bytes(range(21))
        payload = b"abc"
        a = codec.integrity_crc(header, payload)
        mutated = bytearray(header)
        mutated[codec.PAYLOAD_CRC_OFFSET : codec.PAYLOAD_CRC_OFFSET + 4] = b"\xff" * 4
        assert codec.integrity_crc(bytes(mutated), payload) == a

    def test_integrity_is_none_when_recovery_is_incomplete(self):
        """`None` means not verified, which includes a partial v2 decode."""
        stream = codec.pack_text_message("a somewhat longer message to split up")
        atoms = [stream[i : i + 32] for i in range(0, len(stream), 32)]
        headers_only = b"".join(
            a for a in atoms if a[9] != container.FOUNTAIN_PACKET
        )
        result = codec.unpack_stream(headers_only)
        assert result["complete"] is False
        assert result["integrity_ok"] is None


class TestCommandAuthentication:
    """
    The HMAC used to be decorative. `unpack_stream` never verified, an
    unsigned command decoded identically to a signed one apart from a flag,
    decoding without a key omitted the flag entirely (so `.get("auth_ok",
    True)` failed open), and replay was unrestricted.
    """

    KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
    BURN = {"name": "BURN", "thruster_id": 1, "duration_ms": 12500}

    def test_decoding_without_a_key_raises(self):
        from gistlink.commands import CommandAuthError, decode_cmd, encode_cmd

        with pytest.raises(CommandAuthError):
            decode_cmd(encode_cmd(self.BURN, key=self.KEY))

    def test_stripped_hmac_is_refused(self):
        """The downgrade attack: remove the trailer and hope nobody checks."""
        from gistlink.commands import CommandAuthError, decode_cmd, encode_cmd

        with pytest.raises(CommandAuthError):
            decode_cmd(encode_cmd(self.BURN), key=self.KEY)

    def test_wrong_key_is_refused(self):
        from gistlink.commands import CommandAuthError, decode_cmd, encode_cmd

        signed = encode_cmd(self.BURN, key=self.KEY)
        with pytest.raises(CommandAuthError):
            decode_cmd(signed, key=b"x" * 16)

    def test_unverified_results_always_say_so(self):
        from gistlink.commands import decode_cmd, encode_cmd

        for blob in (encode_cmd(self.BURN), encode_cmd(self.BURN, key=self.KEY)):
            out = decode_cmd(blob, require_auth=False)
            assert out["authenticated"] is False
            assert out["auth_ok"] is False

    def test_replay_is_rejected(self):
        from gistlink.commands import (
            CommandAuthError,
            CommandSequencer,
            ReplayGuard,
            decode_cmd,
            encode_cmd,
        )

        seq = CommandSequencer()
        guard = ReplayGuard()
        first = encode_cmd(self.BURN, key=self.KEY, counter=seq.next())
        second = encode_cmd(self.BURN, key=self.KEY, counter=seq.next())

        assert decode_cmd(first, key=self.KEY, replay_guard=guard)["fresh"] is True
        assert decode_cmd(second, key=self.KEY, replay_guard=guard)["fresh"] is True
        with pytest.raises(CommandAuthError, match="replayed"):
            decode_cmd(first, key=self.KEY, replay_guard=guard)
        with pytest.raises(CommandAuthError, match="replayed"):
            decode_cmd(second, key=self.KEY, replay_guard=guard)

    def test_guard_does_not_advance_on_a_rejected_command(self):
        from gistlink.commands import ReplayGuard, decode_cmd, encode_cmd

        guard = ReplayGuard()
        decode_cmd(
            encode_cmd(self.BURN, key=self.KEY, counter=5),
            key=self.KEY,
            replay_guard=guard,
        )
        assert guard.last_accepted == 5
        with pytest.raises(Exception):
            decode_cmd(
                encode_cmd(self.BURN, key=b"y" * 16, counter=9),
                key=self.KEY,
                replay_guard=guard,
            )
        assert guard.last_accepted == 5  # a bad MAC must not move the window

    def test_unpack_stream_verifies_commands(self):
        from gistlink.commands import ReplayGuard

        stream = codec.pack_cmd_message(self.BURN, key=self.KEY, counter=3)
        guard = ReplayGuard()
        good = codec.unpack_stream(stream, key=self.KEY, replay_guard=guard)
        assert good["command_authenticated"] is True
        assert good["message"]["cmd"]["duration_ms"] == 12500

        replayed = codec.unpack_stream(stream, key=self.KEY, replay_guard=guard)
        assert replayed["command_authenticated"] is False
        assert replayed["message"] is None
        assert "authentication failed" in replayed["error"]

    def test_unpack_stream_marks_unverified_commands(self):
        stream = codec.pack_cmd_message(self.BURN, key=self.KEY, counter=1)
        result = codec.unpack_stream(stream)  # no key
        assert result["command_authenticated"] is False
        assert result["message"]["cmd"]["authenticated"] is False

    def test_non_command_messages_report_none(self):
        result = codec.unpack_stream(codec.pack_text_message("hello"))
        assert result["command_authenticated"] is None

    def test_tampered_command_body_is_refused(self):
        from gistlink.commands import CommandAuthError, decode_cmd, encode_cmd

        blob = bytearray(encode_cmd(self.BURN, key=self.KEY, counter=1))
        blob[4] ^= 0x01  # change the burn duration
        with pytest.raises(CommandAuthError):
            decode_cmd(bytes(blob), key=self.KEY)

    def test_batch_authentication(self):
        from gistlink.commands import CommandAuthError, decode_cmd_batch, encode_cmd_batch

        batch = {
            "policy": {"rollback_on_fail": True},
            "items": [{"tai_offset_s": 5, "cmd": self.BURN}],
        }
        signed = encode_cmd_batch(batch, key=self.KEY, counter=2)
        out = decode_cmd_batch(signed, key=self.KEY)
        assert out["authenticated"] is True
        assert out["items"][0]["cmd"]["duration_ms"] == 12500
        with pytest.raises(CommandAuthError):
            decode_cmd_batch(encode_cmd_batch(batch), key=self.KEY)


class TestPersistentReplayGuard:
    """
    The in-memory guard forgets everything when the receiver restarts, so a
    recorded BURN could be replayed after a process bounce. The persistent
    guard keeps the high-water mark on disk.
    """

    KEY = b"k" * 16
    BURN = {"name": "BURN", "thruster_id": 1, "duration_ms": 12500}

    def _signed(self, counter):
        from gistlink.commands import encode_cmd

        return encode_cmd(self.BURN, key=self.KEY, counter=counter)

    def test_survives_a_restart(self, tmp_path):
        from gistlink.commands import (
            CommandAuthError,
            PersistentReplayGuard,
            decode_cmd,
        )

        path = tmp_path / "uplink.json"
        blob = self._signed(7)
        decode_cmd(blob, key=self.KEY, replay_guard=PersistentReplayGuard(path))
        # A brand new guard object stands in for a restarted receiver.
        with pytest.raises(CommandAuthError, match="replayed"):
            decode_cmd(blob, key=self.KEY, replay_guard=PersistentReplayGuard(path))

    def test_in_memory_guard_does_not_survive_a_restart(self):
        """Documents why the persistent one exists."""
        from gistlink.commands import ReplayGuard, decode_cmd

        blob = self._signed(7)
        decode_cmd(blob, key=self.KEY, replay_guard=ReplayGuard())
        # Same bytes, new guard: accepted, which is the hazard.
        out = decode_cmd(blob, key=self.KEY, replay_guard=ReplayGuard())
        assert out["authenticated"] is True

    def test_counter_is_durable_before_the_command_is_accepted(self, tmp_path):
        """
        A crash must not leave a command executed but unrecorded, so the write
        happens first. The file already holds the counter by the time validate
        returns.
        """
        import json

        from gistlink.commands import PersistentReplayGuard

        path = tmp_path / "uplink.json"
        guard = PersistentReplayGuard(path, "sat-1")
        guard.validate(11)
        assert json.loads(path.read_text())["links"]["sat-1"] == 11

    def test_links_are_independent(self, tmp_path):
        from gistlink.commands import PersistentReplayGuard

        path = tmp_path / "uplink.json"
        PersistentReplayGuard(path, "sat-1").validate(5)
        assert PersistentReplayGuard(path, "sat-2").last_accepted == -1
        assert PersistentReplayGuard(path, "sat-1").last_accepted == 5

    def test_corrupt_state_is_refused_not_silently_reset(self, tmp_path):
        from gistlink.commands import PersistentReplayGuard, ReplayStateError

        path = tmp_path / "uplink.json"
        PersistentReplayGuard(path).validate(3)
        path.write_text("{ this is not json")
        with pytest.raises(ReplayStateError):
            PersistentReplayGuard(path)

    def test_missing_state_is_a_first_run(self, tmp_path):
        from gistlink.commands import PersistentReplayGuard, ReplayStateError

        path = tmp_path / "does-not-exist.json"
        assert PersistentReplayGuard(path).last_accepted == -1
        with pytest.raises(ReplayStateError):
            PersistentReplayGuard(path, require_existing=True)

    def test_rejected_command_does_not_advance_persisted_state(self, tmp_path):
        import json

        from gistlink.commands import CommandAuthError, PersistentReplayGuard, decode_cmd

        path = tmp_path / "uplink.json"
        guard = PersistentReplayGuard(path)
        decode_cmd(self._signed(9), key=self.KEY, replay_guard=guard)
        with pytest.raises(CommandAuthError):
            decode_cmd(self._signed(4), key=self.KEY, replay_guard=guard)
        assert json.loads(path.read_text())["links"]["default"] == 9

    def test_unwritable_state_refuses_the_command(self, tmp_path, monkeypatch):
        """If the counter cannot be recorded, the command must not be accepted."""
        from gistlink.commands import PersistentReplayGuard, ReplayStateError

        guard = PersistentReplayGuard(tmp_path / "uplink.json")

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("gistlink.commands.tempfile.mkstemp", boom)
        with pytest.raises(ReplayStateError, match="could not record"):
            guard.validate(1)
        assert guard.last_accepted == -1  # not accepted in memory either

    def test_no_temporary_files_left_behind(self, tmp_path, monkeypatch):
        from gistlink.commands import PersistentReplayGuard, ReplayStateError

        path = tmp_path / "uplink.json"
        guard = PersistentReplayGuard(path)
        guard.validate(1)

        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("interrupted")

        monkeypatch.setattr("gistlink.commands.os.replace", failing_replace)
        with pytest.raises(ReplayStateError):
            guard.validate(2)
        monkeypatch.setattr("gistlink.commands.os.replace", real_replace)
        leftovers = list(tmp_path.glob(".gistlink-replay-*"))
        assert leftovers == []

    def test_end_to_end_through_unpack_stream(self, tmp_path):
        from gistlink.commands import PersistentReplayGuard

        path = tmp_path / "uplink.json"
        stream = codec.pack_cmd_message(self.BURN, key=self.KEY, counter=2)
        first = codec.unpack_stream(
            stream, key=self.KEY, replay_guard=PersistentReplayGuard(path)
        )
        assert first["command_authenticated"] is True
        second = codec.unpack_stream(
            stream, key=self.KEY, replay_guard=PersistentReplayGuard(path)
        )
        assert second["command_authenticated"] is False
        assert "replayed" in second["error"]


class TestMissionDictionaries:
    """
    Short messages are where a general compressor has least to work with, and
    where GistLink is meant to operate. A dictionary trained on past traffic
    gives the compressor the cross-message context it otherwise lacks.
    """

    WORDS = ["satellite", "telemetry", "nominal", "battery", "temperature",
             "attitude", "command", "systems", "payload", "critical"]

    def _corpus(self, n=400, seed=5):
        rng = random.Random(seed)
        return [
            (" ".join(rng.choice(self.WORDS) for _ in range(rng.randint(6, 14))) + ".").encode()
            for _ in range(n)
        ]

    @pytest.fixture
    def trained(self):
        md = pytest.importorskip("gistlink.dictionary")
        if not md.available():
            pytest.skip("requires the 'dict' extra (zstandard)")
        corpus = self._corpus()
        return md, md.train(corpus[:300], name="test-v1"), corpus[300:]

    def test_beats_the_builtin_transform_on_short_messages(self, trained):
        md, dictionary, tests = trained
        plain = sum(len(compress.compress(m, "TEXT")) for m in tests)
        dicted = sum(len(compress.compress(m, "TEXT", dictionary=dictionary)) for m in tests)
        assert dicted < plain * 0.9, f"dictionary gave {dicted} vs {plain}"

    def test_roundtrip(self, trained):
        md, dictionary, tests = trained
        registry = md.DictionaryRegistry([dictionary])
        for message in tests[:20]:
            blob = compress.compress(message, "TEXT", dictionary=dictionary)
            assert compress.decompress(blob, dictionaries=registry) == message

    def test_compact_header_is_three_bytes(self, trained):
        md, dictionary, tests = trained
        blob = compress.compress(tests[0], "TEXT", dictionary=dictionary)
        assert blob[:2] == compress.MAGIC
        assert blob[2] >> 4 == compress.COMPRESS_VERSION_COMPACT
        assert blob[2] & 0x0F == compress.TRANSFORM_ZSTD_DICT
        # The payload is a bare zstd frame: length and dict id live in it.
        assert len(blob) == len(dictionary.compress(tests[0])) + 3

    def test_stats_reads_the_length_from_the_frame(self, trained):
        md, dictionary, tests = trained
        blob = compress.compress(tests[0], "TEXT", dictionary=dictionary)
        assert compress.stats(blob)["original_size"] == len(tests[0])
        assert compress.stats(blob)["transform"] == "zstd_dict"

    def test_missing_dictionary_names_the_id(self, trained):
        md, dictionary, tests = trained
        blob = compress.compress(tests[0], "TEXT", dictionary=dictionary)
        with pytest.raises(compress.MissingDictionaryError) as excinfo:
            compress.decompress(blob)
        assert excinfo.value.dict_id == dictionary.dict_id

    def test_transmission_layer_reports_a_missing_dictionary(self, trained):
        md, dictionary, tests = trained
        stream = codec.pack_compressed_message(tests[0], "TEXT", dictionary=dictionary)
        result = codec.unpack_compressed_stream(stream)
        assert result["missing_dictionary"] == dictionary.dict_id
        assert str(dictionary.dict_id) in result["error"]
        # The gist survives, so an operator still learns what was sent.
        assert result["compression"]["original_size"] == len(tests[0])
        assert result["complete"] is False

    def test_transmission_layer_roundtrip(self, trained):
        md, dictionary, tests = trained
        registry = md.DictionaryRegistry([dictionary])
        stream = codec.pack_compressed_message(tests[0], "TEXT", dictionary=dictionary)
        result = codec.unpack_compressed_stream(stream, dictionaries=registry)
        assert result["data"] == tests[0]
        assert result["integrity_ok"] is True

    def test_dictionary_is_skipped_when_it_does_not_help(self, trained):
        md, dictionary, _tests = trained
        # A text dictionary has nothing to offer random bytes.
        rng = random.Random(1)
        noise = bytes(rng.randrange(256) for _ in range(64))
        blob = compress.compress(noise, "BINARY", dictionary=dictionary)
        assert blob[2] >> 4 != compress.COMPRESS_VERSION_COMPACT
        assert compress.decompress(blob) == noise

    def test_save_and_load(self, trained, tmp_path):
        md, dictionary, tests = trained
        path = tmp_path / "mission.dict"
        dictionary.save(path)
        loaded = md.MissionDictionary.load(path)
        assert loaded.dict_id == dictionary.dict_id
        assert loaded.decompress(dictionary.compress(tests[0])) == tests[0]

    def test_registry_loads_a_directory(self, trained, tmp_path):
        md, dictionary, _tests = trained
        dictionary.save(tmp_path / "a.dict")
        dictionary.save(tmp_path / "b.dict")
        registry = md.DictionaryRegistry()
        assert registry.load_dir(tmp_path) == 2
        assert dictionary.dict_id in registry

    def test_training_needs_enough_samples(self, trained):
        md, _dictionary, _tests = trained
        with pytest.raises(ValueError, match="at least"):
            md.train([b"one", b"two"])


class TestDictionaryNeverHurts:
    """
    A dictionary is a bet that the payload resembles the traffic it was
    trained on. Measured, a mission-vocabulary dictionary made JSON status
    messages 8% BIGGER and log lines 2% bigger, so applying one blindly is a
    regression for anyone whose traffic differs from the training set. The
    compressor produces both encodings and sends the smaller.
    """

    @pytest.fixture
    def trained(self):
        md = pytest.importorskip("gistlink.dictionary")
        if not md.available():
            pytest.skip("requires the 'dict' extra (zstandard)")
        rng = random.Random(1)
        vocab = ["satellite", "telemetry", "nominal", "battery", "attitude",
                 "payload", "thruster", "downlink", "anomaly", "critical"]
        corpus = [
            (" ".join(rng.choice(vocab) for _ in range(rng.randint(6, 16))) + ".").encode()
            for _ in range(400)
        ]
        return md, md.train(corpus, name="mission")

    @pytest.mark.parametrize(
        "sample",
        [
            b'{"t":12,"sc":"KESTREL-2","mode":"SCIENCE","batt":87,"temp":-14.2}',
            b"2026-09-15T04:11:11Z SUBSYS=EPS volt=28.4V temp=-14.2C state=NOMINAL",
            b"The quick brown fox jumps over the lazy dog near the riverbank.",
            bytes(range(256)),
            b"",
        ],
    )
    def test_never_larger_than_without_a_dictionary(self, trained, sample):
        md, dictionary = trained
        without = compress.compress(sample, "TEXT")
        with_dict = compress.compress(sample, "TEXT", dictionary=dictionary)
        assert len(with_dict) <= len(without), (
            f"dictionary cost {len(with_dict) - len(without)} extra bytes"
        )

    def test_still_wins_on_matching_traffic(self, trained):
        md, dictionary = trained
        rng = random.Random(99)
        vocab = ["satellite", "telemetry", "nominal", "battery", "attitude",
                 "payload", "thruster", "downlink", "anomaly", "critical"]
        msgs = [
            (" ".join(rng.choice(vocab) for _ in range(rng.randint(6, 16))) + ".").encode()
            for _ in range(60)
        ]
        without = sum(len(compress.compress(m, "TEXT")) for m in msgs)
        with_dict = sum(len(compress.compress(m, "TEXT", dictionary=dictionary)) for m in msgs)
        assert with_dict < without * 0.85

    def test_fallback_output_still_roundtrips(self, trained):
        """When the dictionary loses, the result must decode without it."""
        md, dictionary = trained
        sample = b'{"t":12,"sc":"KESTREL-2","mode":"SCIENCE","batt":87}'
        blob = compress.compress(sample, "TEXT", dictionary=dictionary)
        assert compress.decompress(blob) == sample  # no registry needed


class TestDictionaryConfiguration:
    """GISTLINK_DICT makes a trained dictionary the default without --dict."""

    @pytest.fixture
    def trained(self, tmp_path):
        md = pytest.importorskip("gistlink.dictionary")
        if not md.available():
            pytest.skip("requires the 'dict' extra (zstandard)")
        rng = random.Random(2)
        vocab = ["satellite", "telemetry", "nominal", "battery", "attitude"]
        corpus = [
            (" ".join(rng.choice(vocab) for _ in range(10)) + ".").encode()
            for _ in range(200)
        ]
        dictionary = md.train(corpus)
        path = tmp_path / "mission.dict"
        dictionary.save(path)
        return md, dictionary, path

    def test_unset_means_no_dictionary(self, trained, monkeypatch):
        md, _dictionary, _path = trained
        monkeypatch.delenv(md.ENV_VAR, raising=False)
        assert md.configured_paths() == []
        assert md.default_dictionary() is None
        assert len(md.configured_registry()) == 0

    def test_configured_dictionary_is_found(self, trained, monkeypatch):
        md, dictionary, path = trained
        monkeypatch.setenv(md.ENV_VAR, str(path))
        assert md.default_dictionary().dict_id == dictionary.dict_id
        assert dictionary.dict_id in md.configured_registry()

    def test_several_paths(self, trained, monkeypatch, tmp_path):
        md, dictionary, path = trained
        second = tmp_path / "other.dict"
        dictionary.save(second)
        monkeypatch.setenv(md.ENV_VAR, os.pathsep.join([str(path), str(second)]))
        assert len(md.configured_paths()) == 2

    def test_cli_uses_the_configured_dictionary(self, trained, monkeypatch, tmp_path):
        from gistlink.cli import main

        md, dictionary, path = trained
        monkeypatch.setenv(md.ENV_VAR, str(path))
        source = tmp_path / "report.txt"
        source.write_bytes(b"satellite telemetry nominal battery attitude nominal.")
        packed, out = tmp_path / "p.bin", tmp_path / "out.txt"
        assert main(["pack-file", str(source), str(packed), "--type", "TEXT"]) == 0
        assert main(["unpack-file", str(packed), str(out)]) == 0
        assert out.read_bytes() == source.read_bytes()


class TestTextTransformRobustness:
    """
    `compress(data, "TEXT")` raised UnicodeDecodeError on bytes that are not
    valid UTF-8, turning a caller's wrong type hint into a lost message. The
    abbreviation step is skipped instead.
    """

    @pytest.mark.parametrize(
        "sample",
        [bytes(range(256)), b"\xff\xfe\x00binary", b"\x80\x81\x82", b""],
    )
    def test_non_utf8_declared_as_text(self, sample):
        assert compress.decompress(compress.compress(sample, "TEXT")) == sample

    def test_utf8_text_still_uses_abbreviation_coding(self):
        """The fallback must not have disabled the transform for real text."""
        text = ("satellite telemetry nominal battery temperature " * 40).encode()
        blob = compress.compress(text, "TEXT")
        assert blob[3] == compress.TRANSFORM_TEXT
        assert compress.decompress(blob) == text


class TestFountainAlwaysSolvable:
    """
    A randomly drawn packet set can be linearly dependent even with every
    block covered and nothing lost. Measured before the fix: 0.5% of seeds at
    K=3 with 13 packets produced a set that could not be decoded on a perfect
    link, which surfaced as a 3.9 CI failure but was neither version nor
    platform specific. For a command message that is a real loss.

    The decoder derives each packet's indices from that packet's own seed, so
    the encoder can choose seeds that span the whole message; redundant
    packets are replaced until they do.
    """

    @pytest.mark.parametrize("K,M", [(1, 11), (2, 12), (3, 13), (5, 15),
                                     (8, 18), (20, 40), (64, 128)])
    def test_every_complete_packet_set_decodes(self, K, M):
        blocks = [bytes([i % 256]) * 16 for i in range(K)]
        for seed in range(1, 400):
            packets = lt_encode_blocks(blocks, seed=seed, num_packets=M)
            decoded, fraction = lt_decode_blocks(packets, K, 16)
            assert decoded == blocks, (
                f"K={K} seed={seed} did not decode with all {M} packets "
                f"present (recovered {fraction:.2f})"
            )

    def test_command_messages_always_decode_on_a_clean_link(self):
        """The end-to-end shape of the CI failure."""
        key = b"k" * 16
        burn = {"name": "BURN", "thruster_id": 1, "duration_ms": 12500}
        for _ in range(300):
            stream = codec.pack_cmd_message(burn, key=key, counter=1)
            result = codec.unpack_stream(stream)
            assert result["command_authenticated"] is False, result.get("error")

    def test_large_messages_skip_the_guarantee(self):
        """It costs O(M*K) and the risk it covers is unmeasurable up there."""
        from gistlink import fountain

        assert fountain.RANK_GUARANTEE_MAX_K == 512

    def test_decoder_is_unchanged_by_the_guarantee(self):
        """
        The repair only changes which seeds the encoder emits. A decoder that
        knows nothing about it must still decode, which is what keeps this a
        non-breaking change.
        """
        blocks = [bytes([i]) * 16 for i in range(6)]
        packets = lt_encode_blocks(blocks, seed=11, num_packets=16)
        for packet_seed, degree, payload in packets:
            assert 0 <= packet_seed <= 0xFFFFFFFF
            assert 1 <= degree <= len(blocks)
            assert len(payload) == 16
        assert lt_decode_blocks(packets, 6, 16)[0] == blocks
