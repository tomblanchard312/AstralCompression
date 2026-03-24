from __future__ import annotations

import math
import os
import random
import struct
import wave
from typing import List

FRAME_MS = 20
FS = 8000
FRAME_SAMPLES = int(FS * FRAME_MS / 1000)
LPC_ORDER = 10
BITS_PER_FRAME = 1 + 6 + 5 + (LPC_ORDER * 4)


def _read_wav_mono_16(path: str) -> List[float]:
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if not os.path.exists(path):
        raise FileNotFoundError(f"WAV file not found: {path}")

    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        fs = w.getframerate()
        n = w.getnframes()
        sampwidth = w.getsampwidth()
        raw = w.readframes(n)

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM supported")

    samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    if nch == 2:
        samples = [
            (samples[i * 2] + samples[i * 2 + 1]) // 2
            for i in range(len(samples) // 2)
        ]

    if fs != FS:
        ratio = fs / FS
        out: List[int] = []
        i = 0.0
        while int(i) < len(samples):
            out.append(samples[int(i)])
            i += ratio
        samples = out

    return [float(s) for s in samples]


def _autocorr(frame: List[float], order: int) -> List[float]:
    n = len(frame)
    return [sum(frame[i] * frame[i + k] for i in range(n - k)) for k in range(order + 1)]


def _levinson_durbin(r: List[float], order: int) -> List[float]:
    a = [0.0] * (order + 1)
    a[0] = 1.0
    e = max(r[0], 1e-10)
    for m in range(1, order + 1):
        lam = 0.0
        for j in range(1, m):
            lam += a[j] * r[m - j]
        k = -(r[m] + lam) / e
        a_new = a[:]
        for j in range(1, m):
            a_new[j] = a[j] + k * a[m - j]
        a_new[m] = k
        a = a_new
        e = max(e * (1.0 - k * k), 1e-10)
    return a


def compute_lsf(frame: List[float], order: int = LPC_ORDER) -> List[float]:
    r = _autocorr(frame, order)
    if r[0] < 1e-10:
        return [math.pi * (i + 1) / (order + 1) for i in range(order)]

    a = _levinson_durbin(r, order)

    p = [a[i] + a[order - i] for i in range(order + 1)]
    q = [a[i] - a[order - i] for i in range(order + 1)]

    def cheby_roots(poly: List[float], n_lsf: int) -> List[float]:
        roots: List[float] = []

        def eval_poly(x: float) -> float:
            return sum(poly[i] * math.cos(i * x) for i in range(len(poly)))

        step = math.pi / 256.0
        prev_x = 0.0
        prev = eval_poly(prev_x)
        x = step
        while len(roots) < n_lsf and x <= math.pi:
            val = eval_poly(x)
            if prev * val <= 0:
                lo = prev_x
                hi = x
                for _ in range(10):
                    mid = (lo + hi) / 2.0
                    vmid = eval_poly(mid)
                    if prev * vmid <= 0:
                        hi = mid
                    else:
                        lo = mid
                roots.append((lo + hi) / 2.0)
            prev_x = x
            prev = val
            x += step

        while len(roots) < n_lsf:
            roots.append(math.pi * (len(roots) + 1) / (n_lsf + 1))
        return roots[:n_lsf]

    lsf_p = cheby_roots(p, order // 2)
    lsf_q = cheby_roots(q, order // 2)
    return sorted(lsf_p + lsf_q)


def lsf_to_lpc(lsf: List[float]) -> List[float]:
    order = len(lsf)
    if order == 0:
        return []

    lsf_sorted = sorted(max(0.001, min(math.pi - 0.001, v)) for v in lsf)

    # Stable approximation suitable for low-bitrate synthesis.
    a = [0.0] * order
    for i in range(order):
        a[i] = -0.75 * math.cos(lsf_sorted[i])
    return a


def _detect_pitch(frame: List[float]) -> tuple[int, int]:
    best_lag = 40
    best_corr = 0.0
    for lag in range(20, 84):
        corr = 0.0
        e1 = 0.0
        e2 = 0.0
        for i in range(len(frame) - lag):
            s1 = frame[i]
            s2 = frame[i + lag]
            corr += s1 * s2
            e1 += s1 * s1
            e2 += s2 * s2
        if e1 > 0 and e2 > 0:
            n = corr / math.sqrt(e1 * e2)
            if n > best_corr:
                best_corr = n
                best_lag = lag
    voiced = 1 if best_corr > 0.3 else 0
    return voiced, best_lag


def _pack_bits(bits: List[int]) -> bytes:
    out = bytearray()
    cur = 0
    pos = 0
    for b in bits:
        cur |= (b & 1) << pos
        pos += 1
        if pos == 8:
            out.append(cur)
            cur = 0
            pos = 0
    if pos:
        out.append(cur)
    return bytes(out)


def _unpack_bits(data: bytes, nbits: int) -> List[int]:
    bits: List[int] = []
    for byte_val in data:
        for i in range(8):
            bits.append((byte_val >> i) & 1)
            if len(bits) == nbits:
                return bits
    return bits


def _quantize_lsf(lsf: List[float]) -> List[int]:
    q: List[int] = []
    for v in lsf[:LPC_ORDER]:
        i = int(round((v / math.pi) * 15.0))
        q.append(max(0, min(15, i)))
    while len(q) < LPC_ORDER:
        q.append(0)
    return q


def _dequantize_lsf(q: List[int]) -> List[float]:
    return [max(0.001, min(math.pi - 0.001, (v / 15.0) * math.pi)) for v in q]


def encode_wav_to_bitstream(path: str) -> bytes:
    samples = _read_wav_mono_16(path)
    if not samples:
        raise ValueError("No audio samples found")

    while len(samples) % FRAME_SAMPLES != 0:
        samples.append(0.0)

    # Pre-emphasis.
    pre: List[float] = []
    prev = 0.0
    for x in samples:
        y = x - 0.95 * prev
        pre.append(y)
        prev = x

    bits: List[int] = []
    frames = [pre[i : i + FRAME_SAMPLES] for i in range(0, len(pre), FRAME_SAMPLES)]
    for fr in frames:
        voiced, lag = _detect_pitch(fr)
        pitch_q = max(0, min(63, lag - 20))

        rms = math.sqrt(sum(x * x for x in fr) / max(1, len(fr)))
        lg = math.log10(max(rms, 1e-6))
        gain_q = int(round(((lg + 5.0) / 5.0) * 31.0))
        gain_q = max(0, min(31, gain_q))

        lsf = compute_lsf(fr, LPC_ORDER)
        lsf_q = _quantize_lsf(lsf)

        bits.append(voiced)
        bits.extend((pitch_q >> i) & 1 for i in range(6))
        bits.extend((gain_q >> i) & 1 for i in range(5))
        for q in lsf_q:
            bits.extend((q >> i) & 1 for i in range(4))

    out = bytearray()
    out.extend(b"VX")
    out.append(0x02)
    out.extend(len(frames).to_bytes(4, "little"))
    out.extend(len(bits).to_bytes(4, "little"))
    out.extend(_pack_bits(bits))
    return bytes(out)


def _decode_v1(data: bytes, out_path: str) -> None:
    # Minimal compatibility bridge for older streams.
    raise ValueError("Voice bitstream version 1 is no longer supported for decode")


def decode_bitstream_to_wav(data: bytes, out_path: str) -> None:
    if not isinstance(data, bytes):
        raise ValueError("data must be bytes")
    if len(data) < 11:
        raise ValueError("data too short for voice bitstream")
    if data[:2] != b"VX":
        raise ValueError("Invalid voice bitstream magic")

    version = data[2]
    if version == 1:
        _decode_v1(data, out_path)
        return
    if version != 2:
        raise ValueError(f"Unsupported voice bitstream version: {version}")

    nframes = int.from_bytes(data[3:7], "little")
    nbits = int.from_bytes(data[7:11], "little")
    bits = _unpack_bits(data[11:], nbits)

    samples: List[int] = []
    pos = 0
    for _ in range(nframes):
        if pos + BITS_PER_FRAME > len(bits):
            break

        voiced = bits[pos]
        pos += 1

        pitch_q = 0
        for i in range(6):
            pitch_q |= bits[pos] << i
            pos += 1
        lag = pitch_q + 20

        gain_q = 0
        for i in range(5):
            gain_q |= bits[pos] << i
            pos += 1
        gain = 10.0 ** ((gain_q / 31.0) * 5.0 - 5.0)

        lsf_q: List[int] = []
        for _j in range(LPC_ORDER):
            q = 0
            for i in range(4):
                q |= bits[pos] << i
                pos += 1
            lsf_q.append(q)
        lpc = lsf_to_lpc(_dequantize_lsf(lsf_q))

        exc: List[float] = []
        for n in range(FRAME_SAMPLES):
            if voiced:
                exc.append(1.0 if lag > 0 and n % lag == 0 else 0.0)
            else:
                exc.append((random.random() * 2.0) - 1.0)

        frame = [0.0] * FRAME_SAMPLES
        for n in range(FRAME_SAMPLES):
            y = gain * exc[n]
            for k in range(1, min(LPC_ORDER, n) + 1):
                y -= lpc[k - 1] * frame[n - k]
            frame[n] = y

        # De-emphasis.
        deemph: List[float] = []
        prev = 0.0
        for x in frame:
            y = x + 0.95 * prev
            deemph.append(y)
            prev = y

        max_abs = max(max(abs(v) for v in deemph), 1e-6)
        scale = 16000.0 / max_abs
        samples.extend(int(max(-32768, min(32767, round(v * scale)))) for v in deemph)

    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(struct.pack("<" + "h" * len(samples), *samples))
