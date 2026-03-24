# Compact satellite command encoding with optional HMAC authentication.
# Uses stdlib only.

import hmac
import hashlib
from .bitstream import BitWriter
from .varint import leb128_encode, leb128_decode

# Command IDs (extend as needed)
CMD_IDS = {
    "SET_MODE": 1,  # args: mode(0=SAFE,1=NORMAL,2=SCIENCE)
    "POINT": 2,  # args: az_deg *1e4 (signed), el_deg *1e4 (signed)
    "BURN": 3,  # args: thruster_id(varint), duration_ms(varint)
    "SCHED_WAKE": 4,  # args: tai_offset_s(varint)
    "REBOOT": 5,  # args: none
    "UPLOAD_CHUNK": 6,  # args: seq(varint), len(varint), bytes
    "APPLY_UPDATE": 7,  # args: none
}

MODE_IDS = {"SAFE": 0, "NORMAL": 1, "SCIENCE": 2}


def _write_sbits(bw: BitWriter, val: int, bits: int):
    mask = (1 << bits) - 1
    if val < 0:
        val = ((-val) ^ mask) + 1
    bw.write_bits(val & mask, bits)


def encode_cmd(cmd: dict, key: bytes | None = None) -> bytes:
    """cmd example:
    {"name":"POINT","az":-12.3456,"el":30.0}
    or {"name":"SET_MODE","mode":"SCIENCE"}
    If key provided, appends HMAC-SHA256 (32 bytes) trailer.
    """
    # Input validation
    if not isinstance(cmd, dict):
        raise ValueError("cmd must be a dictionary")
    if "name" not in cmd:
        raise ValueError("cmd must contain 'name' field")

    name = cmd.get("name")
    cid = CMD_IDS.get(name, 0)
    if cid == 0:
        raise ValueError(f"unknown command name: {name}")

    out = bytearray()
    out += bytes([(1 & 0x07) | (0 << 3)])  # version header
    out += leb128_encode(cid)

    if name == "SET_MODE":
        mode = MODE_IDS.get(cmd.get("mode", "SAFE"), 0)
        out.append(mode & 0xFF)
    elif name == "POINT":
        az = int(round(cmd.get("az", 0.0) * 10000))
        el = int(round(cmd.get("el", 0.0) * 10000))
        # pack as 3 bytes each signed 24-bit
        for v in (az, el):
            if v < 0:
                v = (1 << 24) + v
            out += bytes([(v) & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])
    elif name == "BURN":
        out += leb128_encode(int(cmd.get("thruster_id", 0)))
        out += leb128_encode(int(cmd.get("duration_ms", 0)))
    elif name == "SCHED_WAKE":
        out += leb128_encode(int(cmd.get("tai_offset_s", 0)))
    elif name == "REBOOT":
        pass
    elif name == "UPLOAD_CHUNK":
        payload = cmd.get("data", b"")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        out += leb128_encode(int(cmd.get("seq", 0)))
        out += leb128_encode(len(payload))
        out += payload
    elif name == "APPLY_UPDATE":
        pass

    if key:
        mac = hmac.new(key, out, hashlib.sha256).digest()
        out += mac
    return bytes(out)


def decode_cmd(b: bytes, key: bytes | None = None) -> dict:
    # Input validation
    if not isinstance(b, bytes):
        raise ValueError("b must be bytes")
    if key is not None and not isinstance(key, bytes):
        raise ValueError("key must be bytes or None")

    pos = 0
    pos += 1  # skip version header byte
    cid, pos = leb128_decode(b, pos)

    inv_cmd = {v: k for k, v in CMD_IDS.items()}
    name = inv_cmd.get(cid, f"CMD_{cid}")
    out = {"name": name}

    if name == "SET_MODE":
        mode = b[pos]
        pos += 1
        inv_mode = {v: k for k, v in MODE_IDS.items()}
        out["mode"] = inv_mode.get(mode, f"M{mode}")  # type: ignore
    elif name == "POINT":

        def read24():
            nonlocal pos
            v = b[pos] | (b[pos + 1] << 8) | (b[pos + 2] << 16)
            pos += 3
            if v & (1 << 23):
                v -= 1 << 24
            return v

        az = read24() / 10000.0
        el = read24() / 10000.0
        out.update({"az": az, "el": el})  # type: ignore
    elif name == "BURN":
        thr, pos = leb128_decode(b, pos)
        dur, pos = leb128_decode(b, pos)
        out.update({"thruster_id": thr, "duration_ms": dur})  # type: ignore
    elif name == "SCHED_WAKE":
        off, pos = leb128_decode(b, pos)
        out["tai_offset_s"] = off
    elif name == "REBOOT":
        pass
    elif name == "UPLOAD_CHUNK":
        seq, pos = leb128_decode(b, pos)
        ln, pos = leb128_decode(b, pos)
        data = b[pos:pos + ln]
        pos += ln
        out.update({"seq": seq, "data": data})  # type: ignore
    elif name == "APPLY_UPDATE":
        pass

    if key:
        # verify MAC if present
        if len(b) - pos >= 32:
            mac = b[-32:]
            body = b[:len(b) - 32]
            expected = hmac.new(key, body, hashlib.sha256).digest()
            ok = hmac.compare_digest(expected, mac)
            out["auth_ok"] = ok  # type: ignore
        else:
            out["auth_ok"] = False  # type: ignore

    return out


def encode_cmd_batch(batch: dict, key: bytes | None = None) -> bytes:
    # Input validation
    if not isinstance(batch, dict):
        raise ValueError("batch must be a dictionary")
    if "items" not in batch:
        raise ValueError("batch must contain 'items' field")
    if not isinstance(batch["items"], list):
        raise ValueError("batch items must be a list")
    if key is not None and not isinstance(key, bytes):
        raise ValueError("key must be bytes or None")

    """batch example:
    { "policy": {"rollback_on_fail": true},
      "items": [
        {"tai_offset_s": 5, "cmd": {"name":"SET_MODE","mode":"SCIENCE"}},
        {"tai_offset_s": 30, "cmd": {"name":"POINT","az":1.0,"el":5.0}}
      ]
    }"""
    policy = batch.get("policy", {})
    items = batch.get("items", [])
    flags = 0
    if policy.get("rollback_on_fail"):
        flags |= 1
    if policy.get("halt_on_error"):
        flags |= 2
    out = bytearray()
    out.append(1)  # version
    out.append(flags)
    out += leb128_encode(len(items))
    for it in items:
        off = int(it.get("tai_offset_s", 0))
        out += leb128_encode(off)
        cmd = it.get("cmd", {})
        # MAC whole batch at the end (optional)
        body = encode_cmd(cmd, key=None)  # type: ignore
        out += leb128_encode(len(body))
        out += body
    if key:
        mac = hmac.new(key, out, hashlib.sha256).digest()
        out += mac
    return bytes(out)


def decode_cmd_batch(b: bytes, key: bytes | None = None) -> dict:
    # Input validation
    if not isinstance(b, bytes):
        raise ValueError("b must be bytes")
    if key is not None and not isinstance(key, bytes):
        raise ValueError("key must be bytes or None")

    pos = 0
    pos += 1  # skip version
    flags = b[pos]
    pos += 1
    n, pos = leb128_decode(b, pos)
    items = []
    for _ in range(n):
        off, pos = leb128_decode(b, pos)
        ln, pos = leb128_decode(b, pos)
        body = b[pos:pos + ln]
        pos += ln
        items.append({"tai_offset_s": off, "cmd": decode_cmd(body)})
    out = {
        "policy": {
            "rollback_on_fail": bool(flags & 1),
            "halt_on_error": bool(flags & 2),
        },
        "items": items,
    }
    if key and len(b) - pos >= 32:
        mac = b[-32:]
        body = b[: len(b) - 32]
        expected = hmac.new(key, body, hashlib.sha256).digest()
        ok = hmac.compare_digest(expected, mac)
        out["auth_ok"] = ok  # type: ignore
    return out
