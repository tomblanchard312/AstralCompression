"""Tests for astral/commands.py — encoding and anti-replay."""
from __future__ import annotations

import pytest

from astral.commands import decode_cmd, decode_cmd_batch, encode_cmd, encode_cmd_batch

KEY = b"mission-secret-key-32bytes-pad!!"


class TestEncodeDecodeCmd:
    def test_reboot_roundtrip(self):
        assert decode_cmd(encode_cmd({"name": "REBOOT"}))["name"] == "REBOOT"

    def test_point_roundtrip(self):
        cmd = {"name": "POINT", "az": -12.3456, "el": 30.0}
        dec = decode_cmd(encode_cmd(cmd))
        assert dec["name"] == "POINT"
        assert abs(dec["az"] - -12.3456) < 0.001
        assert abs(dec["el"] - 30.0) < 0.001

    def test_set_mode_roundtrip(self):
        for mode in ("SAFE", "NORMAL", "SCIENCE"):
            assert decode_cmd(encode_cmd({"name": "SET_MODE", "mode": mode}))["mode"] == mode

    def test_unknown_command_raises(self):
        with pytest.raises(ValueError):
            encode_cmd({"name": "SELF_DESTRUCT"})

    def test_missing_name_raises(self):
        with pytest.raises(ValueError):
            encode_cmd({"az": 0.0})


class TestAntiReplay:
    def test_authenticated_roundtrip(self):
        enc = encode_cmd({"name": "REBOOT"}, key=KEY, counter=7)
        dec = decode_cmd(enc, key=KEY, counter=7)
        assert dec["auth_ok"] is True
        assert dec["counter"] == 7
        assert dec.get("counter_ok") is True

    def test_wrong_counter_flagged(self):
        enc = encode_cmd({"name": "REBOOT"}, key=KEY, counter=7)
        dec = decode_cmd(enc, key=KEY, counter=8)
        assert dec["auth_ok"] is True
        assert dec.get("counter_ok") is False

    def test_wrong_key_fails_hmac(self):
        enc = encode_cmd({"name": "REBOOT"}, key=KEY, counter=0)
        dec = decode_cmd(enc, key=b"wrong-key-32bytes-padding!!!!!!", counter=0)
        assert dec["auth_ok"] is False

    def test_tampered_counter_fails_hmac(self):
        enc = bytearray(encode_cmd({"name": "REBOOT"}, key=KEY, counter=5))
        enc[-36] ^= 0x01
        assert decode_cmd(bytes(enc), key=KEY)["auth_ok"] is False

    def test_trailer_is_36_bytes(self):
        plain = len(encode_cmd({"name": "REBOOT"}))
        auth = len(encode_cmd({"name": "REBOOT"}, key=KEY, counter=0))
        assert auth - plain == 36

    def test_batch_roundtrip_with_counter(self):
        batch = {
            "policy": {"rollback_on_fail": True},
            "items": [
                {"tai_offset_s": 5, "cmd": {"name": "SET_MODE", "mode": "SCIENCE"}},
                {"tai_offset_s": 30, "cmd": {"name": "REBOOT"}},
            ],
        }
        enc = encode_cmd_batch(batch, key=KEY, counter=42)
        dec = decode_cmd_batch(enc, key=KEY, counter=42)
        assert dec["auth_ok"] is True
        assert dec.get("counter_ok") is True

    def test_batch_replay_flagged(self):
        batch = {"policy": {}, "items": [{"tai_offset_s": 0, "cmd": {"name": "REBOOT"}}]}
        enc = encode_cmd_batch(batch, key=KEY, counter=1)
        dec = decode_cmd_batch(enc, key=KEY, counter=2)
        assert dec.get("counter_ok") is False


class TestUnauthenticated:
    def test_no_auth_fields_without_key(self):
        dec = decode_cmd(encode_cmd({"name": "REBOOT"}))
        assert "auth_ok" not in dec
        assert "counter" not in dec
