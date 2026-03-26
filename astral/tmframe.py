from __future__ import annotations

import struct

from .crc import crc16_ccitt

ASM_WORD = 0x1ACFFC1D
ASM = bytes([0x1A, 0xCF, 0xFC, 0x1D])
FRAME_SIZE = 1115
FRAME_HEADER_SIZE = 6
FECF_SIZE = 2
FRAME_DATA_SIZE = FRAME_SIZE - FRAME_HEADER_SIZE - FECF_SIZE
WIRE_FRAME_SIZE = len(ASM) + FRAME_SIZE
FILL_BYTE = 0xE0


def apply_prng(data: bytes) -> bytes:
    """
    CCSDS pseudo-randomizer (CCSDS 131.0-B-5 §9).
    Polynomial h(x) = x^8 + x^7 + x^5 + x^3 + 1, init=0xFF.
    Self-inverse: apply_prng(apply_prng(data)) == data.
    Test vector: apply_prng(bytes(8)) ==
    bytes([0xFF, 0x1A, 0xAF, 0x66, 0x52, 0x23, 0x1E, 0x10])
    """
    state = 0xFF
    seq = bytearray()
    for _ in range(len(data)):
        byte_val = 0
        for bit_pos in range(8):
            out_bit = (state >> 7) & 1
            byte_val |= out_bit << (7 - bit_pos)
            # Feedback taps: bits 7, 6, 4, 2 of current state
            feedback = ((state >> 7) ^ (state >> 6) ^ (state >> 4) ^ (state >> 2)) & 1
            state = ((state << 1) | feedback) & 0xFF
        seq.append(byte_val)
    return bytes(a ^ b for a, b in zip(data, seq))


class TmFrameCounter:
    def __init__(self):
        self._mc: int = 0
        self._vc: dict[int, int] = {}

    def next(self, vcid: int) -> tuple[int, int]:
        """
        Advance and return (mc_frame_count, vc_frame_count) for vcid.
        Both counters wrap modulo 256.
        """
        mc = self._mc
        self._mc = (mc + 1) % 256
        vc = self._vc.get(vcid, 0)
        self._vc[vcid] = (vc + 1) % 256
        return mc, vc

    def reset(self, vcid: int | None = None) -> None:
        """Reset counters for one VCID, or all if vcid is None."""
        if vcid is None:
            self._mc = 0
            self._vc.clear()
        else:
            self._mc = 0
            self._vc[vcid] = 0


def encode_frames(
    data: bytes,
    scid: int,
    vcid: int = 0,
    counter: TmFrameCounter | None = None,
    randomise: bool = True,
) -> bytes:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not (0 <= scid <= 1023):
        raise ValueError("scid must be in range 0..1023")
    if not (0 <= vcid <= 7):
        raise ValueError("vcid must be in range 0..7")
    if len(data) == 0:
        return b""

    if counter is None:
        counter = TmFrameCounter()

    out = bytearray()
    for i in range(0, len(data), FRAME_DATA_SIZE):
        chunk = data[i : i + FRAME_DATA_SIZE]
        if len(chunk) < FRAME_DATA_SIZE:
            chunk = chunk + bytes([FILL_BYTE] * (FRAME_DATA_SIZE - len(chunk)))

        mc_count, vc_count = counter.next(vcid)

        word1 = ((scid & 0x3FF) << 4) | ((vcid & 0x7) << 1)
        word3 = (0b11 << 11) | 0x7FF
        header = struct.pack(">HBBH", word1, mc_count, vc_count, word3)

        crc_value = crc16_ccitt(header + chunk)
        fecf = struct.pack(">H", crc_value)

        payload = chunk + fecf
        if randomise:
            payload = apply_prng(payload)

        out += ASM
        out += header
        out += payload

    return bytes(out)


def decode_frames(wire: bytes, randomise: bool = True) -> tuple[bytes, dict]:
    if not isinstance(wire, bytes):
        raise TypeError("wire must be bytes")

    out = bytearray()
    n_frames = 0
    n_crc_errors = 0

    max_start = len(wire) - WIRE_FRAME_SIZE + 1
    for i in range(0, max_start, WIRE_FRAME_SIZE):
        chunk = wire[i : i + WIRE_FRAME_SIZE]
        if chunk[:4] != ASM:
            continue

        frame = chunk[4:]
        header = frame[:FRAME_HEADER_SIZE]
        payload = frame[FRAME_HEADER_SIZE:]

        if randomise:
            payload = apply_prng(payload)

        data_field = payload[:FRAME_DATA_SIZE]
        fecf_recv = struct.unpack(">H", payload[FRAME_DATA_SIZE:])[0]
        crc_expected = crc16_ccitt(header + data_field)

        if crc_expected != fecf_recv:
            n_crc_errors += 1
            continue

        out += data_field
        n_frames += 1

    return bytes(out), {"n_frames": n_frames, "n_crc_errors": n_crc_errors}


def make_idle_frame(
    scid: int,
    vcid: int = 0,
    counter: TmFrameCounter | None = None,
    randomise: bool = True,
) -> bytes:
    if counter is None:
        counter = TmFrameCounter()
    data_field = bytes([FILL_BYTE] * FRAME_DATA_SIZE)
    return encode_frames(
        data_field,
        scid=scid,
        vcid=vcid,
        counter=counter,
        randomise=randomise,
    )
