"""McKay v2 domain-aware compression engine for deep-space links."""

from __future__ import annotations

import lzma
import math
import re
import struct
import zlib
from typing import Dict, List, Tuple

MCKAY_VERSION = 2

TRANSFORM_PASSTHROUGH = 0x00
TRANSFORM_TEXT = 0x01
TRANSFORM_TELEMETRY = 0x02
TRANSFORM_VOICE = 0x03
TRANSFORM_BINARY_FLOAT = 0x04
TRANSFORM_IMAGE_DCT = 0x05

MISSION_ABBREVS = {
    "nominal": "NOM",
    "satellite": "SAT",
    "battery": "BAT",
    "temperature": "TMP",
    "attitude": "ATT",
    "telemetry": "TLM",
    "command": "CMD",
    "systems": "SYS",
    "percent": "PCT",
    "contact": "CNT",
    "entering": "ENT",
    "established": "EST",
    "investigation": "INV",
    "anomaly": "ANM",
    "downlink": "DLK",
    "uplink": "ULK",
    "payload": "PLD",
    "minutes": "MIN",
    "seconds": "SEC",
    "warning": "WRN",
    "critical": "CRT",
    "interface": "IFC",
    "subsystem": "SSM",
    "transmit": "TX",
    "receive": "RX",
}

_ABBREV_WORDS = sorted(MISSION_ABBREVS.keys())
_ABBREV_TO_ID = {word: idx for idx, word in enumerate(_ABBREV_WORDS)}
_ID_TO_ABBREV = {idx: word for word, idx in _ABBREV_TO_ID.items()}


def _pack_header(transform_id: int, original_len: int) -> bytes:
    if original_len > 0xFFFF:
        raise ValueError("McKay v2 header supports payloads up to 65535 bytes")
    return b"MK" + bytes([MCKAY_VERSION, transform_id]) + struct.pack("<H", original_len)


def _is_text_like(data: bytes) -> bool:
    if not data:
        return True
    sample = data[: min(2048, len(data))]
    printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b <= 126)
    return (printable / len(sample)) > 0.85


def _looks_float32_be(data: bytes) -> bool:
    if len(data) < 16 or len(data) % 4 != 0:
        return False
    vals = struct.unpack(f">{len(data) // 4}f", data)
    return all(math.isfinite(v) for v in vals)


def _apply_case(word: str, case_flag: int) -> str:
    if case_flag == 1:
        return word.title()
    if case_flag == 2:
        return word.upper()
    return word


def _text_abbrev_encode(data: bytes) -> bytes:
    text = data.decode("utf-8")
    tokens = re.split(r"(\W+)", text)
    out_parts: List[str] = []
    marker = "\x1e"
    for token in tokens:
        if token.isalpha():
            lower = token.lower()
            if lower in _ABBREV_TO_ID:
                if token.islower():
                    flag = 0
                elif token.istitle():
                    flag = 1
                elif token.isupper():
                    flag = 2
                else:
                    out_parts.append(token)
                    continue
                out_parts.append(f"{marker}{_ABBREV_TO_ID[lower]:02x}{flag}")
                continue
        out_parts.append(token)
    return "".join(out_parts).encode("utf-8")


def _text_abbrev_decode(data: bytes) -> bytes:
    text = data.decode("utf-8")

    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(1), 16)
        flag = int(match.group(2))
        base = _ID_TO_ABBREV.get(idx)
        if base is None:
            return match.group(0)
        return _apply_case(base, flag)

    restored = re.sub(r"\x1e([0-9a-f]{2})([012])", repl, text)
    return restored.encode("utf-8")


def _byte_reorder_float32(data: bytes) -> bytes:
    n = len(data) // 4
    b0 = bytearray(n)
    b1 = bytearray(n)
    b2 = bytearray(n)
    b3 = bytearray(n)
    for i in range(n):
        off = i * 4
        b0[i] = data[off]
        b1[i] = data[off + 1]
        b2[i] = data[off + 2]
        b3[i] = data[off + 3]
    return bytes(b0 + b1 + b2 + b3)


def _byte_unreorder_float32(data: bytes) -> bytes:
    n = len(data) // 4
    q = n
    a, b, c, d = data[:q], data[q : 2 * q], data[2 * q : 3 * q], data[3 * q : 4 * q]
    out = bytearray(len(data))
    for i in range(n):
        off = i * 4
        out[off] = a[i]
        out[off + 1] = b[i]
        out[off + 2] = c[i]
        out[off + 3] = d[i]
    return bytes(out)


def _choose_channels(values: List[float]) -> int:
    n = len(values)
    best = 1
    best_score = float("-inf")
    for ch in range(1, min(32, n) + 1):
        if n % ch != 0:
            continue
        samples_per = n // ch
        if samples_per < 8:
            continue
        score = 0.0
        for c in range(ch):
            series = values[c::ch]
            diffs = [abs(series[i] - series[i - 1]) for i in range(1, len(series))]
            score -= sum(diffs) / max(1, len(diffs))
        if score > best_score:
            best_score = score
            best = ch
    return best


def _telemetry_encode(data: bytes) -> bytes:
    vals = list(struct.unpack(f">{len(data) // 4}f", data))
    channels = _choose_channels(vals)
    count = len(vals)

    transposed: List[List[float]] = []
    for c in range(channels):
        transposed.append(vals[c::channels])

    meta = bytearray()
    deltas = bytearray()
    for ch in transposed:
        mn = min(ch)
        mx = max(ch)
        scale = 4095.0 / (mx - mn) if mx > mn else 1.0
        meta.extend(struct.pack(">ff", mn, scale))

        q = [int(round((v - mn) * scale)) if mx > mn else 0 for v in ch]
        q = [max(0, min(4095, x)) for x in q]
        prev = q[0] if q else 0
        deltas.extend(struct.pack(">h", prev))
        for i in range(1, len(q)):
            d = q[i] - prev
            prev = q[i]
            deltas.extend(struct.pack(">h", max(-32768, min(32767, d))))

    payload = bytearray()
    payload.append(channels)
    payload.extend(struct.pack(">I", count))
    payload.extend(meta)
    payload.extend(deltas)
    return lzma.compress(bytes(payload), preset=9)


def _telemetry_decode(payload: bytes, original_len: int) -> bytes:
    raw = lzma.decompress(payload)
    pos = 0
    channels = raw[pos]
    pos += 1
    count = struct.unpack(">I", raw[pos : pos + 4])[0]
    pos += 4
    samples_per = count // channels if channels else 0

    mins: List[float] = []
    scales: List[float] = []
    for _ in range(channels):
        mn, sc = struct.unpack(">ff", raw[pos : pos + 8])
        pos += 8
        mins.append(mn)
        scales.append(sc if sc != 0 else 1.0)

    channel_values: List[List[float]] = []
    for c in range(channels):
        qvals: List[int] = []
        prev = 0
        for i in range(samples_per):
            d = struct.unpack(">h", raw[pos : pos + 2])[0]
            pos += 2
            if i == 0:
                prev = d
            else:
                prev = prev + d
            qvals.append(prev)

        mn = mins[c]
        sc = scales[c]
        if sc == 0:
            sc = 1.0
        channel_values.append([mn + (q / sc) for q in qvals])

    out_vals: List[float] = [0.0] * count
    for c in range(channels):
        for t, v in enumerate(channel_values[c]):
            out_vals[t * channels + c] = v

    out = struct.pack(f">{len(out_vals)}f", *out_vals)
    return out[:original_len]


def _dct1d(x: List[float]) -> List[float]:
    n = 8
    out: List[float] = []
    for k in range(n):
        s = 0.0
        for idx in range(n):
            s += x[idx] * math.cos(math.pi * k * (2 * idx + 1) / (2 * n))
        scale = math.sqrt(1 / n) if k == 0 else math.sqrt(2 / n)
        out.append(scale * s)
    return out


def dct2_8x8(block: List[List[float]]) -> List[List[float]]:
    rows = [_dct1d(block[i]) for i in range(8)]
    cols = [[rows[r][c] for r in range(8)] for c in range(8)]
    return [_dct1d(col) for col in cols]


def _zigzag_indices() -> List[Tuple[int, int]]:
    order: List[Tuple[int, int]] = []
    for s in range(15):
        if s % 2 == 0:
            r = min(s, 7)
            c = s - r
            while r >= 0 and c <= 7:
                order.append((r, c))
                r -= 1
                c += 1
        else:
            c = min(s, 7)
            r = s - c
            while c >= 0 and r <= 7:
                order.append((r, c))
                r += 1
                c -= 1
    return order


_ZZ = _zigzag_indices()
_Q = [
    [16, 11, 10, 16, 24, 40, 51, 61],
    [12, 12, 14, 19, 26, 58, 60, 55],
    [14, 13, 16, 24, 40, 57, 69, 56],
    [14, 17, 22, 29, 51, 87, 80, 62],
    [18, 22, 37, 56, 68, 109, 103, 77],
    [24, 35, 55, 64, 81, 104, 113, 92],
    [49, 64, 78, 87, 103, 121, 120, 101],
    [72, 92, 95, 98, 112, 100, 103, 99],
]


def _image_encode(data: bytes) -> bytes:
    if len(data) < 4:
        return zlib.compress(data, 9)
    w, h = struct.unpack(">HH", data[:4])
    px = data[4:]
    if w == 0 or h == 0 or len(px) < w * h:
        return zlib.compress(data, 9)

    out = bytearray(struct.pack(">HH", w, h))
    for by in range(0, h, 8):
        for bx in range(0, w, 8):
            block = [[0.0] * 8 for _ in range(8)]
            for y in range(8):
                for x in range(8):
                    iy = min(h - 1, by + y)
                    ix = min(w - 1, bx + x)
                    block[y][x] = float(px[iy * w + ix]) - 128.0
            coeff = dct2_8x8(block)
            qblock = [[int(round(coeff[y][x] / _Q[y][x])) for x in range(8)] for y in range(8)]
            for y, x in _ZZ:
                out.extend(struct.pack(">h", qblock[y][x]))
    return zlib.compress(bytes(out), 9)


class McKayCompressor:
    def __init__(self) -> None:
        self._last_stats: Dict[str, float] = {}

    def compress(self, data: bytes, data_type: str = "AUTO") -> bytes:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if len(data) > 0xFFFF:
            raise ValueError("McKay v2 supports payloads up to 65535 bytes")

        if data_type == "AUTO":
            if _is_text_like(data):
                data_type = "TEXT"
            elif _looks_float32_be(data):
                data_type = "BINARY"
            else:
                data_type = "BINARY"

        transform_id = TRANSFORM_PASSTHROUGH
        payload = data

        if data_type == "TEXT":
            raw_z = zlib.compress(data, 9)
            try:
                mapped = _text_abbrev_encode(data)
                mapped_z = zlib.compress(mapped, 9)
                if len(mapped_z) <= len(raw_z):
                    payload = b"\x01" + mapped_z
                else:
                    payload = b"\x00" + raw_z
            except UnicodeDecodeError:
                payload = b"\x00" + raw_z
            transform_id = TRANSFORM_TEXT
        elif data_type == "TELEMETRY" and _looks_float32_be(data):
            payload = _telemetry_encode(data)
            transform_id = TRANSFORM_TELEMETRY
        elif data_type == "VOICE":
            payload = zlib.compress(data, 9)
            transform_id = TRANSFORM_VOICE
        elif data_type == "IMAGE":
            payload = _image_encode(data)
            transform_id = TRANSFORM_IMAGE_DCT
        elif data_type == "BINARY" and _looks_float32_be(data):
            reordered = _byte_reorder_float32(data)
            payload = lzma.compress(reordered, preset=9)
            transform_id = TRANSFORM_BINARY_FLOAT
        else:
            payload = lzma.compress(data, preset=9)
            transform_id = TRANSFORM_PASSTHROUGH

        if len(payload) > len(data):
            transform_id = TRANSFORM_PASSTHROUGH
            payload = data

        out = _pack_header(transform_id, len(data)) + payload
        self._last_stats = {
            "original_size": float(len(data)),
            "compressed_size": float(len(out)),
            "ratio": (len(data) / len(out)) if len(out) else 1.0,
        }
        return out

    def decompress(self, data: bytes) -> bytes:
        if not isinstance(data, bytes) or len(data) < 6:
            raise ValueError("invalid McKay payload")
        if data[:2] != b"MK" or data[2] != MCKAY_VERSION:
            raise ValueError("invalid McKay header")
        transform_id = data[3]
        original_len = struct.unpack("<H", data[4:6])[0]
        payload = data[6:]

        if transform_id == TRANSFORM_PASSTHROUGH:
            return payload[:original_len]
        if transform_id == TRANSFORM_TEXT:
            if not payload:
                return b""
            mode = payload[0]
            body = payload[1:]
            text_bytes = zlib.decompress(body)
            if mode == 1:
                return _text_abbrev_decode(text_bytes)
            return text_bytes
        if transform_id == TRANSFORM_TELEMETRY:
            return _telemetry_decode(payload, original_len)
        if transform_id == TRANSFORM_VOICE:
            return zlib.decompress(payload)[:original_len]
        if transform_id == TRANSFORM_BINARY_FLOAT:
            reordered = lzma.decompress(payload)
            return _byte_unreorder_float32(reordered)[:original_len]
        if transform_id == TRANSFORM_IMAGE_DCT:
            return zlib.decompress(payload)[:original_len]

        raise ValueError("unknown McKay transform id")

    def stats(self) -> Dict[str, float]:
        return dict(self._last_stats)


def compress(data: bytes, data_type: str = "AUTO") -> bytes:
    return McKayCompressor().compress(data, data_type)


def decompress(data: bytes) -> bytes:
    return McKayCompressor().decompress(data)


McKayASTRALCompressor = McKayCompressor


class McKayASTRALIntegration:
    def __init__(self) -> None:
        self.mckay_compressor = McKayCompressor()

    def compress_and_encode(self, data, data_type: str = "AUTO", extra_fountain: int = 0):
        payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
        return self.mckay_compressor.compress(payload, data_type)

    def decompress_and_decode(self, data: bytes) -> bytes:
        return self.mckay_compressor.decompress(data)
