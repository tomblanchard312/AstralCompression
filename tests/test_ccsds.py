"""
CCSDS conformance tests: Space Packets, TM Transfer Frames, Reed-Solomon.

This consolidates the checks that used to live in the PHASE3/4/5 development
scripts at the repository root, as tests that run with everything else.
"""

from __future__ import annotations

import math
import struct

import pytest

from astral import codec
from astral.crc import crc16_ccitt
from astral.spacepacket import (
    APID_IDLE,
    APID_MAP,
    SpacePacketSequenceCounter,
    make_idle_packet,
    unwrap,
    wrap,
)
from astral.tmframe import (
    ASM,
    FECF_SIZE,
    FILL_BYTE,
    FRAME_DATA_SIZE,
    FRAME_HEADER_SIZE,
    FRAME_SIZE,
    MODE_PACKET,
    WIRE_FRAME_SIZE,
    TmFrameCounter,
    apply_prng,
    decode_frames,
    encode_frames,
    make_idle_frame,
)

try:
    from astral import rs_fec

    RS_AVAILABLE = True
except ImportError:  # optional extra
    rs_fec = None
    RS_AVAILABLE = False

DETECT = {"type": "DETECT", "subject": "KESTREL-2", "object": "H2O_ICE",
          "lat": -43.7, "lon": 130.2, "depth_m": 18.0, "conf": 0.94}


# --------------------------------------------------------------------------
# CCSDS 133.0-B-2 Space Packet Protocol
# --------------------------------------------------------------------------


class TestSpacePacket:
    def test_primary_header_fields(self):
        counter = SpacePacketSequenceCounter()
        packet = wrap(b"ASTRAL payload", "DETECT", counter)
        w1, w2, length = struct.unpack(">HHH", packet[:6])
        assert (w1 >> 13) & 0x7 == 0  # CCSDS packet version 1
        assert (w1 >> 12) & 0x1 == 0  # TM
        assert (w1 >> 11) & 0x1 == 0  # no secondary header
        assert w1 & 0x7FF == APID_MAP["DETECT"][0]
        assert (w2 >> 14) & 0x3 == 0b11  # standalone packet
        assert length == len(packet) - 6 - 1  # length field is count minus one

    def test_command_apids_are_marked_as_telecommand(self):
        counter = SpacePacketSequenceCounter()
        for msg_type in ("CMD", "CMD_BATCH"):
            packet = wrap(b"x", msg_type, counter)
            (w1,) = struct.unpack(">H", packet[:2])
            assert (w1 >> 12) & 0x1 == 1, f"{msg_type} should be TC"

    def test_roundtrip(self):
        counter = SpacePacketSequenceCounter()
        payload = bytes(range(256))
        parsed = unwrap(wrap(payload, "TEXT", counter))
        assert parsed["astral_stream"] == payload
        assert parsed["msg_type"] == "TEXT"

    def test_counters_are_independent_per_apid_and_wrap(self):
        counter = SpacePacketSequenceCounter()
        assert counter.next(0x010) == 0
        assert counter.next(0x010) == 1
        assert counter.next(0x011) == 0  # independent
        counter.set(0x010, 16383)
        assert counter.next(0x010) == 16383
        assert counter.next(0x010) == 0  # wraps at 2^14

    def test_counter_reset(self):
        counter = SpacePacketSequenceCounter()
        counter.next(0x010)
        counter.reset()
        assert counter.next(0x010) == 0

    def test_idle_packet(self):
        idle = make_idle_packet()
        assert len(idle) == 7
        assert unwrap(idle)["apid"] == APID_IDLE

    @pytest.mark.parametrize(
        "bad", [b"", b"short", 12345, bytes(5)]
    )
    def test_invalid_packets_rejected(self, bad):
        with pytest.raises(ValueError):
            unwrap(bad)

    def test_wrap_rejects_bad_input(self):
        counter = SpacePacketSequenceCounter()
        with pytest.raises(ValueError):
            wrap(b"", "DETECT", counter)
        with pytest.raises(ValueError):
            wrap(b"x", "NOT_A_TYPE", counter)
        with pytest.raises(ValueError):
            wrap(bytes(65537), "DETECT", counter)

    def test_convenience_wrappers(self):
        counter = SpacePacketSequenceCounter()
        packet = codec.pack_message_sp(DETECT, counter)
        result = codec.unpack_stream_sp(packet)
        assert result["msg_type"] == "DETECT"
        assert result["complete"] is True
        assert result["message"]["object"] == "H2O_ICE"

    def test_unwrap_errors_are_returned_not_raised(self):
        assert "error" in codec.unpack_stream_sp(b"\x00\x01\x02")


# --------------------------------------------------------------------------
# CCSDS 132.0-B TM Transfer Frames + 131.0-B randomizer
# --------------------------------------------------------------------------


class TestTmFrames:
    def test_frame_geometry(self):
        assert ASM == bytes([0x1A, 0xCF, 0xFC, 0x1D])
        assert FRAME_SIZE == 1115
        assert FRAME_HEADER_SIZE == 6
        assert FECF_SIZE == 2
        assert FRAME_DATA_SIZE == 1107
        assert WIRE_FRAME_SIZE == 1119
        assert FRAME_SIZE == FRAME_HEADER_SIZE + FRAME_DATA_SIZE + FECF_SIZE

    def test_frame_count_matches_payload(self):
        for size in (1, 500, FRAME_DATA_SIZE, FRAME_DATA_SIZE + 1, 2500):
            wire = encode_frames(bytes(size), scid=42)
            expected = math.ceil(size / FRAME_DATA_SIZE)
            assert len(wire) == expected * WIRE_FRAME_SIZE

    def test_header_fields_and_fecf(self):
        wire = encode_frames(bytes(FRAME_DATA_SIZE), scid=0x2A, vcid=3,
                             randomise=False)
        frame = wire[4:]
        word1, mc, vc, _status = struct.unpack(">HBBH", frame[:FRAME_HEADER_SIZE])
        assert (word1 >> 14) & 0x3 == 0  # transfer frame version 1
        assert (word1 >> 4) & 0x3FF == 0x2A
        assert (word1 >> 1) & 0x7 == 3
        assert mc == 0 and vc == 0
        header = frame[:FRAME_HEADER_SIZE]
        data = frame[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + FRAME_DATA_SIZE]
        (fecf,) = struct.unpack(">H", frame[FRAME_HEADER_SIZE + FRAME_DATA_SIZE :])
        assert fecf == crc16_ccitt(header + data)

    def test_counters(self):
        counter = TmFrameCounter()
        assert counter.next(0) == (0, 0)
        assert counter.next(0) == (1, 1)
        assert counter.next(1)[1] == 0  # per-VC counter is independent
        counter2 = TmFrameCounter()
        counter2._mc = 255
        assert counter2.next(0)[0] == 255
        assert counter2.next(0)[0] == 0  # wraps at 256
        counter2.reset()
        assert counter2.next(0) == (0, 0)

    def test_scid_and_vcid_validation(self):
        with pytest.raises(ValueError):
            encode_frames(b"x", scid=1024)
        with pytest.raises(ValueError):
            encode_frames(b"x", scid=1, vcid=8)
        with pytest.raises(ValueError):
            encode_frames(b"x", scid=1, mode="NOT_A_MODE")

    def test_randomiser_roundtrip(self):
        payload = bytes(range(200)) + b"ASTRAL"
        for randomise in (True, False):
            wire = encode_frames(payload, scid=42, randomise=randomise)
            data, stats = decode_frames(wire, randomise=randomise)
            assert stats["n_frames"] == 1 and stats["n_crc_errors"] == 0
            assert data[: len(payload)] == payload

    def test_corrupt_frame_is_dropped(self):
        wire = bytearray(encode_frames(bytes(range(200)), scid=42))
        wire[4 + FRAME_HEADER_SIZE + 100] ^= 0xFF
        data, stats = decode_frames(bytes(wire))
        assert stats["n_crc_errors"] >= 1
        assert stats["n_frames"] == 0
        assert data == b""

    def test_broken_asm_yields_nothing(self):
        wire = bytearray(encode_frames(bytes(range(200)), scid=42))
        wire[0] ^= 0xFF
        _data, stats = decode_frames(bytes(wire))
        assert stats["n_frames"] == 0

    def test_multi_frame_roundtrip(self):
        payload = bytes(i % 256 for i in range(FRAME_DATA_SIZE * 3 - 50))
        wire = encode_frames(payload, scid=7, vcid=2)
        data, stats = decode_frames(wire)
        assert stats["n_frames"] == 3 and stats["n_crc_errors"] == 0
        assert data[: len(payload)] == payload

    def test_idle_frame(self):
        idle = make_idle_frame(scid=42)
        assert len(idle) == WIRE_FRAME_SIZE
        data, stats = decode_frames(idle)
        assert stats["n_frames"] == 1
        assert all(b == FILL_BYTE for b in data)

    def test_packet_mode_carries_space_packets(self):
        counter = SpacePacketSequenceCounter()
        stream = b"".join(
            wrap(codec.pack_message(DETECT, extra_fountain=2), "DETECT", counter)
            for _ in range(3)
        )
        wire = encode_frames(stream, scid=1, mode=MODE_PACKET)
        data, _stats = decode_frames(wire)
        assert data[: len(stream)] == stream

    def test_end_to_end_through_tm(self):
        wire = codec.pack_message_tm(DETECT, scid=42, extra_fountain=5)
        assert len(wire) % WIRE_FRAME_SIZE == 0
        result = codec.unpack_frames_tm(wire)
        assert result["complete"] is True
        assert abs(result["message"]["lat"] - DETECT["lat"]) < 1e-6

    def test_fountain_recovers_a_lost_frame(self):
        wire = bytearray(codec.pack_message_tm(DETECT, scid=42, extra_fountain=50))
        n_frames = len(wire) // WIRE_FRAME_SIZE
        last = (n_frames - 1) * WIRE_FRAME_SIZE
        wire[last + 4 + FRAME_HEADER_SIZE + 5] ^= 0xFF
        result = codec.unpack_frames_tm(bytes(wire))
        assert result["tm_n_crc_errors"] >= 1
        assert result["complete"] is True

    def test_tm_errors_are_returned_not_raised(self):
        assert codec.unpack_frames_tm(b"too short")["tm_n_frames"] == 0

    def test_prng_is_applied_to_whole_frame(self):
        clear = encode_frames(bytes(100), scid=1, randomise=False)
        randomised = encode_frames(bytes(100), scid=1, randomise=True)
        assert randomised[:4] == ASM  # never randomised
        assert apply_prng(randomised[4:]) == clear[4:]


# --------------------------------------------------------------------------
# Reed-Solomon
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RS_AVAILABLE, reason="requires the 'rs' extra (reedsolo)")
class TestAtomReedSolomon:
    def test_codeword_geometry(self):
        assert rs_fec.ATOM_SIZE == 32
        assert rs_fec.PARITY_E16 == 32 and rs_fec.PARITY_E8 == 16
        assert rs_fec.CODEWORD_SIZE[16] == 64 and rs_fec.CODEWORD_SIZE[8] == 48
        assert rs_fec.codeword_size(16) == 64 and rs_fec.codeword_size(8) == 48

    @pytest.mark.parametrize("e,expected", [(16, 64), (8, 48)])
    def test_stream_expansion(self, e, expected):
        atoms = bytes(range(32)) * 8
        assert len(rs_fec.encode_stream(atoms, e=e)) == 8 * expected

    @pytest.mark.parametrize("e", [8, 16])
    def test_corrects_up_to_e_errors_per_atom(self, e):
        atoms = bytes((i * 3) % 256 for i in range(32))
        encoded = bytearray(rs_fec.encode_stream(atoms, e=e))
        for i in range(e):  # exactly at the correction limit
            encoded[i] ^= 0xFF
        out, corrected, uncorrectable = rs_fec.decode_stream(bytes(encoded), e=e)
        assert out == atoms and uncorrectable == 0 and corrected == e

    @pytest.mark.parametrize("e", [8, 16])
    def test_reports_uncorrectable_atoms(self, e):
        atoms = bytes((i * 3) % 256 for i in range(32))
        encoded = bytearray(rs_fec.encode_stream(atoms, e=e))
        for i in range(rs_fec.CODEWORD_SIZE[e]):  # destroy the whole codeword
            encoded[i] ^= 0xFF
        _out, _corrected, uncorrectable = rs_fec.decode_stream(bytes(encoded), e=e)
        assert uncorrectable == 1

    def test_invalid_parameters(self):
        with pytest.raises(ValueError):
            rs_fec.encode_stream(bytes(33))  # not a whole number of atoms
        with pytest.raises(ValueError):
            rs_fec.encode_stream(bytes(32), e=4)
        with pytest.raises(TypeError):
            rs_fec.encode_stream("not bytes")

    def test_empty_input(self):
        assert rs_fec.encode_stream(b"") == b""
        assert rs_fec.decode_stream(b"") == (b"", 0, 0)

    def test_end_to_end_through_rs(self):
        stream = codec.pack_message_rs(DETECT, extra_fountain=5, e=16)
        result = codec.unpack_stream_rs(stream, e=16)
        assert result["complete"] is True
        assert result["rs_uncorrectable_atoms"] == 0

    def test_rs_repairs_bit_errors_end_to_end(self):
        stream = bytearray(codec.pack_message_rs(DETECT, extra_fountain=5, e=16))
        for atom in range(3):  # a few errors in each of the first atoms
            for i in range(4):
                stream[atom * 64 + i] ^= 0xFF
        result = codec.unpack_stream_rs(bytes(stream), e=16)
        assert result["rs_corrected_symbols"] >= 12
        assert result["complete"] is True
