import json, random
from astral.codec import pack_message, unpack_stream


def test_roundtrip():
    msg = {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": -43.7,
        "lon": 130.2,
        "depth_m": 18.0,
        "conf": 0.94,
    }
    blob = pack_message(msg, extra_fountain=3)
    out = unpack_stream(blob)
    assert out["complete"] == True
    assert abs(out["message"]["lat"] - msg["lat"]) < 1e-6


def test_lossy():
    msg = {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": -10.0,
        "lon": 20.0,
        "depth_m": 5.5,
        "conf": 0.8,
    }
    blob = pack_message(msg, extra_fountain=10)
    lossy = bytearray()
    for i in range(0, len(blob), 32):
        atom = blob[i : i + 32]
        if len(atom) < 32:
            break
        if random.random() >= 0.4:
            lossy += atom
    out = unpack_stream(bytes(lossy))
    assert "gist" in out
    assert out["gist"]["type"] == "DETECT"
