"""Tests for astral/grammar.py - gist bits and payload encoding."""

import pytest
from astral.grammar import (
    DICT_TYPE,
    decode_payload,
    encode_payload,
    make_gist_bits,
    parse_gist,
)


class TestPayloadRoundtrip:
    BASE = {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": 0.0,
        "lon": 0.0,
        "depth_m": 0.0,
        "conf": 0.5,
    }

    def _rt(self, **overrides) -> dict:
        msg = {**self.BASE, **overrides}
        return decode_payload(encode_payload(msg))

    def test_lat_negative(self):
        dec = self._rt(lat=-43.7, lon=130.2)
        assert abs(dec["lat"] - -43.7) < 1e-6

    def test_lat_positive(self):
        dec = self._rt(lat=51.5, lon=-0.1)
        assert abs(dec["lat"] - 51.5) < 1e-6

    def test_lat_extremes(self):
        dec_n = self._rt(lat=-90.0)
        assert abs(dec_n["lat"] - -90.0) < 1e-4
        dec_p = self._rt(lat=90.0)
        assert abs(dec_p["lat"] - 90.0) < 1e-4

    def test_lon_negative(self):
        dec = self._rt(lon=-179.9)
        assert abs(dec["lon"] - -179.9) < 1e-4

    def test_lon_positive(self):
        dec = self._rt(lon=130.2)
        assert abs(dec["lon"] - 130.2) < 1e-6

    def test_depth_m(self):
        dec = self._rt(depth_m=18.0)
        assert abs(dec["depth_m"] - 18.0) < 0.2

    def test_conf(self):
        dec = self._rt(conf=0.94)
        assert abs(dec["conf"] - 0.94) < 0.01

    def test_all_message_types(self):
        for mtype in DICT_TYPE:
            msg = {**self.BASE, "type": mtype}
            try:
                dec = decode_payload(encode_payload(msg))
                assert dec["type"] == mtype
            except Exception as exc:
                pytest.fail(f"type={mtype} raised: {exc}")


class TestGistBits:
    def test_roundtrip(self):
        msg = {
            "type": "DETECT",
            "object": "H2O_ICE",
            "lat": -10.0,
            "lon": 20.0,
            "conf": 0.9,
        }
        gist_bytes, gist_bits = make_gist_bits(msg)
        assert gist_bits == 33
        parsed = parse_gist(gist_bytes, gist_bits)
        assert parsed["type"] == "DETECT"
        assert abs(parsed["conf_coarse"] - 0.9) < 0.2

    def test_missing_type_raises(self):
        with pytest.raises((ValueError, KeyError)):
            make_gist_bits({"lat": 0.0})

    def test_gist_bytes_length(self):
        msg = {"type": "TEXT", "conf": 0.99}
        gist_bytes, gist_bits = make_gist_bits(msg)
        assert len(gist_bytes) == (gist_bits + 7) // 8
