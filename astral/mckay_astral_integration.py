"""McKay v2 domain-aware compression engine for deep-space links."""

from __future__ import annotations

import lzma
import math
import re
import struct
import warnings
import zlib
from typing import Dict

MCKAY_VERSION: int = 2

TRANSFORM_PASSTHROUGH = 0x00
TRANSFORM_TEXT = 0x01
TRANSFORM_TELEMETRY = 0x02
TRANSFORM_VOICE_C2 = 0x03
TRANSFORM_BINARY_FLOAT = 0x04

ENTROPY_LZMA = 0x00
ENTROPY_ZLIB = 0x01
ENTROPY_ZSTD = 0x02

HEADER_SIZE = 8
MAGIC = b"MK"

MISSION_ABBREVS: dict[str, str] = {
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
    "anomaly": "ANM",
    "downlink": "DLK",
    "uplink": "ULK",
    "payload": "PLD",
    "minutes": "MIN",
    "seconds": "SEC",
    "warning": "WRN",
    "critical": "CRT",
    "interface": "IFC",
    "transmit": "TX",
    "receive": "RX",
    "subsystem": "SSM",
}

_ABBREV_WORDS = sorted(MISSION_ABBREVS.keys())
_ABBREV_TO_ID = {word: idx for idx, word in enumerate(_ABBREV_WORDS)}
_ID_TO_ABBREV = {idx: word for word, idx in _ABBREV_TO_ID.items()}

try:
    import astral_compress as _ac

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


def _pack_u16(v: int) -> bytes:
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def _unpack_u16(b: bytes) -> int:
    return b[0] | (b[1] << 8)


def _detect_type(data: bytes) -> str:
    """Heuristic data type detection."""
    if len(data) < 4:
        return "BINARY"
    if data[:2] == b"VX":
        return "VOICE"

    sample = data[: min(256, len(data))]
    printable = sum(1 for b in sample if 0x20 <= b <= 0x7E or b in (0x09, 0x0A, 0x0D))
    if printable / len(sample) > 0.85:
        return "TEXT"

    if len(data) % 4 == 0 and len(data) >= 16:
        try:
            floats = struct.unpack(f">{len(data) // 4}f", data)
            if all(math.isfinite(f) for f in floats[:64]):
                return "BINARY"
        except Exception:
            pass

    return "BINARY"


def _compress_text(data: bytes) -> tuple[int, int, bytes]:
    """Returns (transform_id, entropy_coder, payload_bytes)."""
    if _RUST_AVAILABLE:
        try:
            return TRANSFORM_TEXT, ENTROPY_ZSTD, _ac.compress_text(data)
        except Exception:
            warnings.warn(
                "Rust text compression failed, falling back to Python", UserWarning
            )

    abbr_bytes = _text_abbrev_encode(data)

    candidates = [
        (ENTROPY_ZLIB, zlib.compress(abbr_bytes, 9)),
        (ENTROPY_LZMA, lzma.compress(abbr_bytes, preset=9)),
        (ENTROPY_ZLIB, zlib.compress(data, 9)),
        (ENTROPY_LZMA, lzma.compress(data, preset=9)),
    ]
    best_entropy, best = min(candidates, key=lambda x: len(x[1]))
    if len(best) >= len(data):
        return TRANSFORM_PASSTHROUGH, ENTROPY_LZMA, lzma.compress(data, preset=9)
    return TRANSFORM_TEXT, best_entropy, best


def _apply_case(word: str, case_flag: int) -> str:
    if case_flag == 1:
        return word.title()
    if case_flag == 2:
        return word.upper()
    return word


def _text_abbrev_encode(data: bytes) -> bytes:
    text = data.decode("utf-8")
    tokens = re.split(r"(\W+)", text)
    out_parts = []
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
    if _RUST_AVAILABLE:
        try:
            return _ac.decompress_text(data)
        except Exception:
            pass  # Fall back to Python implementation

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


def _auto_detect_channels(data: bytes) -> int:
    """
    Try divisors of n_floats from 1..32; pick the one with
    lowest average absolute consecutive difference.
    Falls back to 1.
    """
    n_floats = len(data) // 4
    if n_floats < 4:
        return 1
    floats = struct.unpack(f">{n_floats}f", data[: n_floats * 4])
    best_ch, best_score = 1, float("inf")
    for ch in range(1, min(33, n_floats + 1)):
        if n_floats % ch != 0:
            continue
        n_t = n_floats // ch
        score = 0.0
        for c in range(ch):
            vals = [floats[t * ch + c] for t in range(n_t)]
            rng = max(vals) - min(vals)
            if rng < 1e-30:
                continue
            diffs = [abs(vals[i] - vals[i - 1]) / rng for i in range(1, n_t)]
            score += sum(diffs) / max(len(diffs), 1)
        score /= ch
        if score < best_score:
            best_score = score
            best_ch = ch
    return best_ch


def _compress_telemetry(data: bytes, channels: int) -> tuple[int, int, bytes]:
    """Returns (TRANSFORM_TELEMETRY, entropy_coder, payload_bytes)."""
    if _RUST_AVAILABLE:
        try:
            return (
                TRANSFORM_TELEMETRY,
                ENTROPY_ZSTD,
                _ac.compress_telemetry(data, channels),
            )
        except Exception:
            warnings.warn(
                "Rust telemetry compression failed, falling back to Python", UserWarning
            )

    n_floats = len(data) // 4
    n_samples = n_floats // channels
    floats = struct.unpack(f">{n_floats}f", data[: n_floats * 4])

    ch_vals = [
        [floats[t * channels + ch] for t in range(n_samples)] for ch in range(channels)
    ]

    meta = bytearray()
    firsts = bytearray()
    deltas = bytearray()

    for vals in ch_vals:
        mn = min(vals)
        span = max(vals) - mn
        if span < 1e-30:
            span = 1.0
        meta.extend(struct.pack(">ff", mn, span))

        # Changed from Q16 (65535) to Q12 (4095) for better precision
        q = [max(0, min(4095, round((v - mn) / span * 4095))) for v in vals]
        firsts.extend(struct.pack(">H", q[0]))
        d = [max(-2048, min(2047, q[i] - q[i - 1])) for i in range(1, len(q))]
        if d:
            deltas.extend(struct.pack(f">{len(d)}h", *d))

    payload = bytes(meta) + bytes(firsts) + bytes(deltas)
    return TRANSFORM_TELEMETRY, ENTROPY_LZMA, lzma.compress(payload, preset=9)


def _decompress_telemetry(
    payload_bytes: bytes, original_length: int, channels: int, entropy_coder: int
) -> bytes:
    """Inverse of _compress_telemetry."""
    if _RUST_AVAILABLE and entropy_coder == ENTROPY_ZSTD:
        try:
            return _ac.decompress_telemetry(payload_bytes, original_length, channels)
        except Exception:
            warnings.warn(
                "Rust telemetry decompression failed, falling back to Python",
                UserWarning,
            )

    # Decompress using the appropriate entropy coder
    if entropy_coder == ENTROPY_LZMA:
        payload = lzma.decompress(payload_bytes)
    elif entropy_coder == ENTROPY_ZSTD:
        try:
            import zstd

            payload = zstd.decompress(payload_bytes)
        except ImportError:
            raise ValueError(
                "zstd decompression not available - install with: pip install zstd"
            )
    else:
        raise ValueError(
            f"Unsupported entropy coder for telemetry: 0x{entropy_coder:02X}"
        )

    n_floats = original_length // 4
    n_samples = n_floats // channels

    meta_sz = channels * 8
    first_sz = channels * 2
    delta_sz = channels * max(0, n_samples - 1) * 2

    meta_b = payload[:meta_sz]
    first_b = payload[meta_sz : meta_sz + first_sz]
    delta_b = payload[meta_sz + first_sz : meta_sz + first_sz + delta_sz]

    result = []
    for ch in range(channels):
        mn, span = struct.unpack(">ff", meta_b[ch * 8 : (ch + 1) * 8])
        q0 = struct.unpack(">H", first_b[ch * 2 : (ch + 1) * 2])[0]

        q = [q0]
        if n_samples > 1:
            off = ch * (n_samples - 1) * 2
            ds = struct.unpack(
                f">{n_samples - 1}h",
                delta_b[off : off + (n_samples - 1) * 2],
            )
            for d in ds:
                q.append(
                    max(0, min(4095, q[-1] + d))
                )  # Changed from 65535 to 4095 for Q12

        result.append(
            [mn + v / 4095.0 * span for v in q]
        )  # Changed from 65535 to 4095 for Q12

    out = bytearray()
    for t in range(n_samples):
        for ch in range(channels):
            out.extend(struct.pack(">f", result[ch][t]))
    return bytes(out)


def _compress_voice(data: bytes, voice_bps: int) -> tuple[int, int, bytes]:
    """
    Re-encode a VX voice bitstream using Codec2.
    Falls back to TRANSFORM_PASSTHROUGH if pycodec2 is unavailable.
    """
    try:
        from importlib import import_module

        Codec2 = import_module("pycodec2.pycodec2").Codec2
        import numpy as np  # type: ignore
    except ImportError:
        warnings.warn(
            "pycodec2 not available; install with: pip install pycodec2 "
            "(also requires libcodec2-dev system library). "
            "Falling back to LZMA passthrough for voice.",
            ImportWarning,
            stacklevel=4,
        )
        return TRANSFORM_PASSTHROUGH, ENTROPY_LZMA, lzma.compress(data, preset=9)

    if len(data) < 11 or data[:2] != b"VX":
        return TRANSFORM_PASSTHROUGH, ENTROPY_LZMA, lzma.compress(data, preset=9)

    try:
        from astral.voice import decode_bitstream_to_wav as _dec_wav
        import os
        import tempfile
        import wave

        tmp_path = None
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        _dec_wav(data, tmp_path)
        wf = wave.open(tmp_path, "rb")
        raw_pcm = wf.readframes(wf.getnframes())
        wf.close()
        os.remove(tmp_path)
        samples_orig = np.frombuffer(raw_pcm, dtype=np.int16).copy()
    except Exception:
        return TRANSFORM_PASSTHROUGH, ENTROPY_LZMA, lzma.compress(data, preset=9)

    valid_bps = {700, 1200, 1300, 1400, 1600, 2400, 3200}
    if voice_bps not in valid_bps:
        voice_bps = 1200

    c2 = Codec2(voice_bps)
    spf = c2.samples_per_frame()
    bpf = c2.bytes_per_frame()

    n_pad = (math.ceil(len(samples_orig) / spf) * spf) - len(samples_orig)
    samples_padded = np.concatenate([samples_orig, np.zeros(n_pad, dtype=np.int16)])

    encoded = c2.encode(samples_padded)
    n_frames_c2 = len(samples_padded) // spf

    hdr = struct.pack(">HII", voice_bps, n_frames_c2, len(samples_orig))
    padded_enc = encoded + bytes(n_frames_c2 * bpf - len(encoded))
    return TRANSFORM_VOICE_C2, 0xFF, hdr + padded_enc


def _decompress_voice(payload_bytes: bytes) -> bytes:
    """
    Decode a Codec2 voice payload back to raw PCM int16 bytes.
    Returns raw 8kHz 16-bit mono PCM.
    """
    try:
        from importlib import import_module

        Codec2 = import_module("pycodec2.pycodec2").Codec2
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise ValueError(
            "pycodec2 required to decompress VOICE data. "
            "Install with: pip install pycodec2"
        ) from exc

    voice_bps, _n_frames, n_orig = struct.unpack_from(">HII", payload_bytes)
    c2_bytes = payload_bytes[10:]
    c2 = Codec2(voice_bps)
    dec = c2.decode(c2_bytes)
    raw_pcm = dec[:n_orig].astype(np.int16)
    return raw_pcm.tobytes()


def _compress_binary(data: bytes) -> tuple[int, int, bytes]:
    """
    For float32 arrays: reorder bytes to group exponents/mantissas.
    Otherwise plain LZMA.
    """
    if _RUST_AVAILABLE and len(data) >= 16 and len(data) % 4 == 0:
        try:
            return TRANSFORM_BINARY_FLOAT, ENTROPY_ZSTD, _ac.compress_binary_float(data)
        except Exception:
            warnings.warn(
                "Rust binary float compression failed, falling back to Python",
                UserWarning,
            )

    if len(data) >= 16 and len(data) % 4 == 0:
        n = len(data) // 4
        b0 = bytes(data[i * 4 + 0] for i in range(n))
        b1 = bytes(data[i * 4 + 1] for i in range(n))
        b2 = bytes(data[i * 4 + 2] for i in range(n))
        b3 = bytes(data[i * 4 + 3] for i in range(n))
        reordered = b0 + b1 + b2 + b3
        reordered_lzma = lzma.compress(reordered, preset=9)
        plain_lzma = lzma.compress(data, preset=9)
        if len(reordered_lzma) < len(plain_lzma):
            return TRANSFORM_BINARY_FLOAT, ENTROPY_LZMA, reordered_lzma
        return TRANSFORM_PASSTHROUGH, ENTROPY_LZMA, plain_lzma

    return TRANSFORM_PASSTHROUGH, ENTROPY_LZMA, lzma.compress(data, preset=9)


def _decompress_binary_float(
    payload_bytes: bytes, original_length: int, entropy_coder: int
) -> bytes:
    """Inverse of float byte-reorder."""
    if _RUST_AVAILABLE and entropy_coder == ENTROPY_ZSTD:
        try:
            return _ac.decompress_binary_float(payload_bytes, original_length)
        except Exception:
            warnings.warn(
                "Rust binary float decompression failed, falling back to Python",
                UserWarning,
            )

    # Decompress using the appropriate entropy coder
    if entropy_coder == ENTROPY_LZMA:
        reordered = lzma.decompress(payload_bytes)
    elif entropy_coder == ENTROPY_ZSTD:
        try:
            import zstd

            reordered = zstd.decompress(payload_bytes)
        except ImportError:
            raise ValueError(
                "zstd decompression not available - install with: pip install zstd"
            )
    else:
        raise ValueError(
            f"Unsupported entropy coder for binary float: 0x{entropy_coder:02X}"
        )

    n = original_length // 4
    out = bytearray(original_length)
    for i in range(n):
        out[i * 4 + 0] = reordered[0 * n + i]
        out[i * 4 + 1] = reordered[1 * n + i]
        out[i * 4 + 2] = reordered[2 * n + i]
        out[i * 4 + 3] = reordered[3 * n + i]
    return bytes(out)


def compress(
    data: bytes,
    data_type: str = "AUTO",
    voice_bps: int = 1200,
    channels: int = 0,
) -> bytes:
    """Compress data using the McKay v2 domain-aware pipeline."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    if len(data) == 0:
        payload = lzma.compress(b"", preset=9)
        return (
            MAGIC
            + bytes([MCKAY_VERSION, TRANSFORM_PASSTHROUGH])
            + _pack_u16(0)
            + bytes([0, ENTROPY_LZMA])
            + payload
        )

    if data_type == "AUTO":
        data_type = _detect_type(data)

    if data_type == "TEXT":
        tid, entropy_coder, payload = _compress_text(data)
    elif data_type == "TELEMETRY":
        ch = channels if channels > 0 else _auto_detect_channels(data)
        tid, entropy_coder, payload = _compress_telemetry(data, ch)
        channels = ch
    elif data_type == "VOICE":
        tid, entropy_coder, payload = _compress_voice(data, voice_bps)
    elif data_type == "BINARY":
        tid, entropy_coder, payload = _compress_binary(data)
    else:
        tid, entropy_coder, payload = (
            TRANSFORM_PASSTHROUGH,
            ENTROPY_LZMA,
            lzma.compress(data, preset=9),
        )

    if len(payload) >= len(data):
        tid = TRANSFORM_PASSTHROUGH
        entropy_coder = ENTROPY_LZMA
        payload = lzma.compress(data, preset=9)
    if len(payload) >= len(data):
        tid = TRANSFORM_PASSTHROUGH
        entropy_coder = 0xFF  # No entropy coding
        payload = data

    orig_len = min(len(data), 0xFFFF)
    header = (
        MAGIC
        + bytes([MCKAY_VERSION, tid])
        + _pack_u16(orig_len)
        + bytes([min(channels, 255), entropy_coder])
    )
    return header + payload


def decompress(data: bytes) -> bytes:
    """Decompress a McKay v2 compressed stream."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Too short for McKay v2 header: {len(data)} bytes")
    if data[:2] != MAGIC:
        raise ValueError(f"Bad magic: {data[:2]!r} (expected b'MK')")

    _version = data[2]
    tid = data[3]
    orig_len = _unpack_u16(data[4:6])
    channels = data[6]
    entropy_coder = data[7]
    payload = data[HEADER_SIZE:]

    # Select decompressor based on entropy coder
    def _entropy_decompress(payload: bytes) -> bytes:
        # Try the indicated entropy coder first
        try:
            if entropy_coder == ENTROPY_LZMA:
                return lzma.decompress(payload)
            elif entropy_coder == ENTROPY_ZLIB:
                return zlib.decompress(payload)
            elif entropy_coder == ENTROPY_ZSTD:
                import zstd

                return zstd.decompress(payload)
            elif entropy_coder == 0xFF:
                # No entropy coding
                return payload
            else:
                raise ValueError(f"Unknown entropy coder: 0x{entropy_coder:02X}")
        except Exception as e:
            # If primary coder fails, try alternatives for robustness
            warning_msg = f"Primary entropy coder 0x{entropy_coder:02X} failed: {e}, trying alternatives"
            warnings.warn(warning_msg, UserWarning)

            # Try other coders in order of preference
            alternatives = []
            if entropy_coder != ENTROPY_ZSTD:
                alternatives.append((ENTROPY_ZSTD, "zstd"))
            if entropy_coder != ENTROPY_LZMA:
                alternatives.append((ENTROPY_LZMA, "lzma"))
            if entropy_coder != ENTROPY_ZLIB:
                alternatives.append((ENTROPY_ZLIB, "zlib"))

            for alt_coder, alt_name in alternatives:
                try:
                    if alt_coder == ENTROPY_LZMA:
                        return lzma.decompress(payload)
                    elif alt_coder == ENTROPY_ZLIB:
                        return zlib.decompress(payload)
                    elif alt_coder == ENTROPY_ZSTD:
                        import zstd

                        return zstd.decompress(payload)
                except Exception:
                    continue

            # If all alternatives fail, raise the original error
            raise ValueError(
                f"All entropy decompression attempts failed, original error: {e}"
            )

    if tid == TRANSFORM_PASSTHROUGH:
        try:
            return _entropy_decompress(payload)
        except Exception:
            return payload

    if tid == TRANSFORM_TEXT:
        # Try Rust decompression first (handles both entropy and abbreviation decoding)
        if _RUST_AVAILABLE:
            try:
                return _ac.decompress_text(payload)
            except Exception:
                warnings.warn(
                    "Rust text decompression failed, falling back to Python",
                    UserWarning,
                )

        # Fallback to Python: entropy decompress then abbreviation decode
        try:
            plain = _entropy_decompress(payload)
            return _text_abbrev_decode(plain)
        except Exception:
            raise ValueError("TEXT payload decompression failed")

    if tid == TRANSFORM_TELEMETRY:
        if channels == 0:
            channels = 1
        return _decompress_telemetry(payload, orig_len, channels, entropy_coder)

    if tid == TRANSFORM_VOICE_C2:
        return _decompress_voice(payload)

    if tid == TRANSFORM_BINARY_FLOAT:
        return _decompress_binary_float(payload, orig_len, entropy_coder)

    raise ValueError(f"Unknown McKay v2 transform ID: 0x{tid:02X}")


def stats(compressed: bytes) -> dict:
    """Return compression statistics for a McKay v2 stream."""
    if len(compressed) < HEADER_SIZE:
        return {"error": "too short"}
    tid = compressed[3]
    orig_len = _unpack_u16(compressed[4:6])
    payload = compressed[HEADER_SIZE:]
    names = {
        TRANSFORM_PASSTHROUGH: "passthrough",
        TRANSFORM_TEXT: "text",
        TRANSFORM_TELEMETRY: "telemetry",
        TRANSFORM_VOICE_C2: "voice_codec2",
        TRANSFORM_BINARY_FLOAT: "binary_float",
    }
    comp_size = len(payload)
    ratio = orig_len / comp_size if comp_size > 0 else 0.0
    return {
        "transform": names.get(tid, f"unknown_0x{tid:02X}"),
        "original_size": orig_len,
        "compressed_size": comp_size,
        "ratio": round(ratio, 3),
        "savings_pct": round((1 - 1 / ratio) * 100, 1) if ratio > 0 else 0.0,
    }


class McKayCompressor:
    def __init__(self, voice_bps: int = 1200) -> None:
        self.voice_bps = voice_bps

    def compress(self, data: bytes, data_type: str = "AUTO") -> bytes:
        return compress(data, data_type=data_type, voice_bps=self.voice_bps)

    def decompress(self, data: bytes) -> bytes:
        return decompress(data)

    def stats(self, compressed: bytes) -> dict:
        return stats(compressed)


McKayASTRALCompressor = McKayCompressor


class McKayASTRALIntegration:
    def __init__(self, voice_bps: int = 1200) -> None:
        self.mckay_compressor = McKayCompressor(voice_bps=voice_bps)

    def compress_and_encode(
        self, data, data_type: str = "AUTO", extra_fountain: int = 0
    ):
        _ = extra_fountain
        payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
        return self.mckay_compressor.compress(payload, data_type)

    def decompress_and_decode(self, data: bytes) -> bytes:
        return self.mckay_compressor.decompress(data)
