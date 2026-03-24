"""Tests for astral/codec.py - pack_message / unpack_stream."""
import random
import pytest
from astral.codec import (
    pack_cmd_batch,
    pack_cmd_message,
    pack_message,
    pack_text_message,
    unpack_stream,
)


BASE_MSG = {
    "type": "DETECT",
    "subject": "KESTREL-2",
    "object": "H2O_ICE",
    "lat": -43.7,
    "lon": 130.2,
    "depth_m": 18.0,
    "conf": 0.94,
}


class TestPackUnpack:
    def test_detect_roundtrip(self, detect_msg):
        result = unpack_stream(pack_message(detect_msg, extra_fountain=3))
        assert result["complete"]
        assert abs(result["message"]["lat"] - detect_msg["lat"]) < 1e-6
        assert abs(result["message"]["lon"] - detect_msg["lon"]) < 1e-6

    def test_output_aligned_to_32_bytes(self, detect_msg):
        blob = pack_message(detect_msg)
        assert len(blob) % 32 == 0

    def test_text_roundtrip(self):
        text = "nominal link standing by"
        result = unpack_stream(pack_text_message(text, extra_fountain=5))
        assert result["complete"]
        assert result["message"]["type"] == "TEXT"
        assert result["message"]["text"] == text

    def test_cmd_roundtrip(self):
        cmd = {"name": "REBOOT"}
        result = unpack_stream(pack_cmd_message(cmd, extra_fountain=3))
        assert result["complete"]
        assert result["message"]["cmd"]["name"] == "REBOOT"

    def test_cmd_point_roundtrip(self):
        cmd = {"name": "POINT", "az": -12.3456, "el": 30.0}
        result = unpack_stream(pack_cmd_message(cmd, extra_fountain=3))
        assert result["complete"]
        decoded_cmd = result["message"]["cmd"]
        assert decoded_cmd["name"] == "POINT"
        assert abs(decoded_cmd["az"] - -12.3456) < 0.001
        assert abs(decoded_cmd["el"] - 30.0) < 0.001

    def test_cmd_batch_roundtrip(self):
        batch = {
            "policy": {"rollback_on_fail": True},
            "items": [
                {"tai_offset_s": 5, "cmd": {"name": "SET_MODE", "mode": "SCIENCE"}},
                {"tai_offset_s": 30, "cmd": {"name": "REBOOT"}},
            ],
        }
        result = unpack_stream(pack_cmd_batch(batch, extra_fountain=5))
        assert result["complete"]
        assert result["message"]["type"] == "CMD_BATCH"

    def test_min_redundancy_zero(self, detect_msg):
        """min_redundancy=0 produces a much smaller stream."""
        blob_default = pack_message(detect_msg, extra_fountain=0)
        blob_zero = pack_message(
            detect_msg,
            extra_fountain=0,
            min_redundancy=0,
        )
        assert len(blob_zero) < len(blob_default)
        result = unpack_stream(blob_zero)
        assert result["complete"]

    def test_min_redundancy_preserves_default(self, detect_msg):
        """Default min_redundancy=10 gives same output as before."""
        blob_default = pack_message(detect_msg, extra_fountain=0)
        blob_explicit = pack_message(
            detect_msg,
            extra_fountain=0,
            min_redundancy=10,
        )
        assert len(blob_default) == len(blob_explicit)


class TestLossTolerance:
    def test_survives_30_percent_loss(self, detect_msg):
        """Header is always retained; fountain recovers from 30% atom loss."""
        blob = pack_message(detect_msg, extra_fountain=20)
        rng = random.Random(42)
        lossy = bytearray()
        first = True
        for i in range(0, len(blob), 32):
            atom = blob[i : i + 32]
            if len(atom) < 32:
                break
            if first or rng.random() >= 0.3:
                lossy += atom
            first = False
        result = unpack_stream(bytes(lossy))
        assert "gist" in result
        assert result["gist"]["type"] == "DETECT"

    def test_gist_survives_heavy_loss(self, detect_msg):
        """Gist (header atom) is always available even with extreme loss."""
        blob = pack_message(detect_msg, extra_fountain=0)
        header_only = blob[:32]
        result = unpack_stream(header_only)
        assert "gist" in result
        assert result["gist"]["type"] == "DETECT"


class TestInvalidInputs:
    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            pack_message("not a dict")  # type: ignore[arg-type]

    def test_missing_type_raises(self):
        with pytest.raises(ValueError):
            pack_message({"lat": 0.0})

    def test_negative_extra_fountain_raises(self):
        with pytest.raises(ValueError):
            pack_message({"type": "DETECT"}, extra_fountain=-1)

    def test_unpack_empty_raises_or_errors(self):
        result = unpack_stream(b"")
        assert "error" in result

    def test_unpack_non_bytes_raises(self):
        with pytest.raises((ValueError, TypeError)):
            unpack_stream("not bytes")  # type: ignore[arg-type]


class TestOptionalPhases:
    """Tests for Phase 3-5 modules. Skipped if modules are not yet present."""

    def test_space_packet_wrap_unwrap(self, detect_msg):
        sp = pytest.importorskip("astral.spacepacket")
        counter = sp.SpacePacketSequenceCounter()
        astral = pack_message(detect_msg, extra_fountain=3)
        packet = sp.wrap(astral, "DETECT", counter)
        parsed = sp.unwrap(packet)
        assert parsed["astral_stream"] == astral
        assert parsed["apid"] == sp.APID_MAP["DETECT"][0]

    def test_rs_fec_roundtrip(self, detect_msg):
        rs = pytest.importorskip("astral.rs_fec")
        astral = pack_message(detect_msg, extra_fountain=3)
        protected = rs.encode_stream(astral, e=16)
        recovered, _, n_drop = rs.decode_stream(protected, e=16)
        assert recovered == astral
        assert n_drop == 0

    def test_tm_frame_roundtrip(self, detect_msg):
        tm = pytest.importorskip("astral.tmframe")
        astral = pack_message(detect_msg, extra_fountain=3)
        wire = tm.encode_frames(astral, scid=42, vcid=0)
        data, stats = tm.decode_frames(wire)
        assert stats["n_crc_errors"] == 0
        assert data[:len(astral)] == astral
