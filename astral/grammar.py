"""Grammar encoding/decoding helpers for ASTRAL messages."""

from .bitstream import BitReader, BitWriter
from .varint import leb128_decode, leb128_encode

DICT_SUBJECT = {
    "KESTREL-1": 1,
    "KESTREL-2": 2,
    "ORION-A": 3,
}
DICT_OBJECT = {
    "H2O_ICE": 1,
    "CH4_ICE": 2,
    "BASALT": 3,
    "UNKNOWN": 4,
}
DICT_TYPE = {
    "DETECT": 1,
    "STATUS": 2,
    "TEXT": 3,
    "VOICE": 4,
    "CMD": 5,
    "CMD_BATCH": 6,
}

_INV_TYPE = {v: k for k, v in DICT_TYPE.items()}
_INV_SUBJECT = {v: k for k, v in DICT_SUBJECT.items()}
_INV_OBJECT = {v: k for k, v in DICT_OBJECT.items()}

_LAT_BITS = 28
_LON_BITS = 29


def _to_twos_complement(value: int, bits: int) -> int:
    if value < 0:
        return (1 << bits) + value
    return value


def _from_twos_complement(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    if value & sign_bit:
        return value - (1 << bits)
    return value


def q_lat(value: float) -> int:
    value = max(-90.0, min(90.0, float(value)))
    return int(round(value * 1_000_000.0))


def q_lon(value: float) -> int:
    value = float(value)
    while value < -180.0:
        value += 360.0
    while value >= 180.0:
        value -= 360.0
    return int(round(value * 1_000_000.0))


def u_conf(c: float) -> int:
    c = max(0.0, min(1.0, float(c)))
    return int(round(c * 255.0))


def make_gist_bits(msg: dict) -> tuple[bytes, int]:
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dictionary")
    if "type" not in msg:
        raise ValueError("msg must contain 'type' field")

    t = DICT_TYPE.get(msg.get("type"), 0) & 0x7
    obj = DICT_OBJECT.get(msg.get("object", "UNKNOWN"), 0) & 0x7F

    lat = max(-90.0, min(90.0, float(msg.get("lat", 0.0))))
    lon = max(-180.0, min(180.0, float(msg.get("lon", 0.0))))
    conf = max(0.0, min(1.0, float(msg.get("conf", 0.5))))

    lat_q = int(round((lat + 90.0) / 180.0 * 1023.0)) & 0x3FF
    lon_q = int(round((lon + 180.0) / 360.0 * 1023.0)) & 0x3FF
    conf_q = int(round(conf * 7.0)) & 0x7

    bw = BitWriter()
    bw.write_bits(t, 3)
    bw.write_bits(obj, 7)
    bw.write_bits(lat_q, 10)
    bw.write_bits(lon_q, 10)
    bw.write_bits(conf_q, 3)
    return bw.getvalue(), 33


def encode_payload(msg: dict) -> bytes:
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dictionary")
    if "type" not in msg:
        raise ValueError("msg must contain 'type' field")

    bw = BitWriter()
    bw.write_bytes(leb128_encode(DICT_TYPE.get(msg.get("type"), 0)))
    bw.write_bytes(leb128_encode(DICT_SUBJECT.get(msg.get("subject", "KESTREL-1"), 0)))
    bw.write_bytes(leb128_encode(DICT_OBJECT.get(msg.get("object", "UNKNOWN"), 0)))

    lat = q_lat(msg.get("lat", 0.0))
    lon = q_lon(msg.get("lon", 0.0))
    bw.write_bits(_to_twos_complement(lat, _LAT_BITS), _LAT_BITS)
    bw.write_bits(_to_twos_complement(lon, _LON_BITS), _LON_BITS)

    depth_m = max(0.0, min(204.7, float(msg.get("depth_m", 0.0))))
    bw.write_bits(int(round(depth_m * 10.0)) & 0x7FF, 11)
    bw.write_bits(u_conf(msg.get("conf", 0.5)) & 0xFF, 8)
    return bw.getvalue()


def decode_payload(b: bytes) -> dict:
    if not isinstance(b, bytes):
        raise ValueError("payload must be bytes")
    if not b:
        raise ValueError("payload cannot be empty")

    type_id, pos = leb128_decode(b, 0)
    subj_id, pos = leb128_decode(b, pos)
    obj_id, pos = leb128_decode(b, pos)

    br = BitReader(b[pos:])
    lat_q = _from_twos_complement(br.read_bits(_LAT_BITS), _LAT_BITS)
    lon_q = _from_twos_complement(br.read_bits(_LON_BITS), _LON_BITS)
    depth_q = br.read_bits(11)
    conf_q = br.read_bits(8)

    return {
        "type": _INV_TYPE.get(type_id, f"TYPE_{type_id}"),
        "subject": _INV_SUBJECT.get(subj_id, f"SUBJ_{subj_id}"),
        "object": _INV_OBJECT.get(obj_id, f"OBJ_{obj_id}"),
        "lat": lat_q / 1_000_000.0,
        "lon": lon_q / 1_000_000.0,
        "depth_m": depth_q / 10.0,
        "conf": conf_q / 255.0,
    }


def parse_gist(gist_bytes: bytes, gist_bits: int) -> dict:
    if not isinstance(gist_bytes, bytes):
        raise ValueError("gist_bytes must be bytes")
    if gist_bits <= 0:
        raise ValueError("gist_bits must be positive")

    br = BitReader(gist_bytes)
    t = br.read_bits(3)
    obj = br.read_bits(7)
    lat = br.read_bits(10)
    lon = br.read_bits(10)
    conf = br.read_bits(3)

    return {
        "type": _INV_TYPE.get(t, f"TYPE_{t}"),
        "object": _INV_OBJECT.get(obj, f"OBJ_{obj}"),
        "lat_coarse": (lat / 1023.0) * 180.0 - 90.0,
        "lon_coarse": (lon / 1023.0) * 360.0 - 180.0,
        "conf_coarse": conf / 7.0,
    }
