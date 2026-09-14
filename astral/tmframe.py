"""
CCSDS TM Transfer Frames (CCSDS 132.0-B-3) with the CCSDS 131.0-B-5
pseudo-randomizer.

Two data-field modes are supported:

``VCA`` (default)
    The data field carries an opaque Virtual Channel Access SDU, which is what
    a raw ASTRAL atom stream is. The Sync Flag is set to 1 and the fields the
    standard leaves undefined in that case are zeroed.

``PACKET``
    The data field carries CCSDS Space Packets. The Sync Flag is 0, the
    Segment Length ID is '11' as required, and the First Header Pointer is the
    real offset of the first packet that starts in the frame (0x7FF when none
    does, 0x7FE for an idle frame), so a standard ground-station packet
    extractor can reassemble the stream.
"""

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

# First Header Pointer special values (CCSDS 132.0-B-3, 4.1.2.7.6)
FHP_NO_PACKET_START = 0x7FF
FHP_IDLE_DATA = 0x7FE

MODE_VCA = "VCA"
MODE_PACKET = "PACKET"

# Idle Space Packet (APID 0x7FF) used as packet-mode fill.
_IDLE_PACKET_HEADER = struct.pack(">HHH", 0x07FF, 0b11 << 14, 0)


def _pn_sequence(n: int) -> bytes:
    """
    First ``n`` bytes of the CCSDS pseudo-randomizer sequence.

    CCSDS 131.0-B-5 §9: h(x) = x^8 + x^7 + x^5 + x^3 + 1, all-ones initial
    state, MSB of each byte transmitted first. The recurrence that polynomial
    defines is a(n) = a(n-1) ^ a(n-3) ^ a(n-5) ^ a(n-8).

    Test vector (CCSDS Table 9-1): the sequence starts
    ``FF 48 0E C0 9A 0D 70 BC``.
    """
    n_bits = n * 8
    bits = [1] * 8
    while len(bits) < n_bits:
        i = len(bits)
        bits.append(bits[i - 1] ^ bits[i - 3] ^ bits[i - 5] ^ bits[i - 8])

    out = bytearray(n)
    for i in range(n):
        byte_val = 0
        for b in range(8):
            byte_val = (byte_val << 1) | bits[i * 8 + b]
        out[i] = byte_val
    return bytes(out)


# One period of the sequence is 255 bytes; cache a generous chunk and tile it.
_PN_PERIOD = 255
_PN_CACHE = _pn_sequence(_PN_PERIOD)


def apply_prng(data: bytes) -> bytes:
    """
    XOR ``data`` with the CCSDS pseudo-randomizer sequence.

    Self-inverse: ``apply_prng(apply_prng(x)) == x``.

    Test vector: ``apply_prng(bytes(8)) == bytes.fromhex("ff480ec09a0d70bc")``.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    n = len(data)
    reps = -(-n // _PN_PERIOD)
    seq = (_PN_CACHE * reps)[:n]
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


def _data_field_status(mode: str, first_header_pointer: int) -> int:
    """
    Build the 16-bit Transfer Frame Data Field Status (CCSDS 132.0-B-3 4.1.2.7).

    Bit 0   secondary header flag (0: no secondary header)
    Bit 1   sync flag (0: packets/idle data, 1: VCA_SDU)
    Bit 2   packet order flag (0; undefined when sync flag is 1)
    Bits 3-4 segment length ID ('11' when sync flag is 0; undefined otherwise)
    Bits 5-15 first header pointer (undefined when sync flag is 1)
    """
    if mode == MODE_VCA:
        # Sync flag 1; every field the standard marks undefined is zeroed.
        return 1 << 14
    if not (0 <= first_header_pointer <= 0x7FF):
        raise ValueError("first_header_pointer must be 0..0x7FF")
    return (0b11 << 11) | first_header_pointer


def _build_frame(
    chunk: bytes,
    scid: int,
    vcid: int,
    mc_count: int,
    vc_count: int,
    status: int,
    randomise: bool,
) -> bytes:
    word1 = ((scid & 0x3FF) << 4) | ((vcid & 0x7) << 1)
    header = struct.pack(">HBBH", word1, mc_count, vc_count, status)

    frame = header + chunk
    frame += struct.pack(">H", crc16_ccitt(frame))

    # CCSDS 131.0-B-5: the randomizer covers the whole transfer frame,
    # header and FECF included. The ASM is never randomized.
    if randomise:
        frame = apply_prng(frame)
    return ASM + frame


def encode_frames(
    data: bytes,
    scid: int,
    vcid: int = 0,
    counter: TmFrameCounter | None = None,
    randomise: bool = True,
    mode: str = MODE_VCA,
) -> bytes:
    """
    Segment ``data`` into TM Transfer Frames.

    Parameters
    ----------
    data : bytes
        Payload. In ``MODE_VCA`` this is an opaque SDU (an ASTRAL atom
        stream); in ``MODE_PACKET`` it must be a concatenation of complete
        CCSDS Space Packets.
    scid : int
        Spacecraft ID, 0..1023.
    vcid : int
        Virtual Channel ID, 0..7.
    counter : TmFrameCounter, optional
        Frame counters; a fresh one is used if omitted.
    randomise : bool
        Apply the CCSDS pseudo-randomizer. Default True.
    mode : str
        ``MODE_VCA`` (default) or ``MODE_PACKET``.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not (0 <= scid <= 1023):
        raise ValueError("scid must be in range 0..1023")
    if not (0 <= vcid <= 7):
        raise ValueError("vcid must be in range 0..7")
    if mode not in (MODE_VCA, MODE_PACKET):
        raise ValueError(f"mode must be {MODE_VCA!r} or {MODE_PACKET!r}")
    if len(data) == 0:
        return b""

    if mode == MODE_PACKET:
        return _encode_packet_frames(data, scid, vcid, counter, randomise)

    if counter is None:
        counter = TmFrameCounter()

    out = bytearray()
    status = _data_field_status(MODE_VCA, 0)
    for i in range(0, len(data), FRAME_DATA_SIZE):
        chunk = data[i : i + FRAME_DATA_SIZE]
        if len(chunk) < FRAME_DATA_SIZE:
            chunk = chunk + bytes([FILL_BYTE] * (FRAME_DATA_SIZE - len(chunk)))
        mc_count, vc_count = counter.next(vcid)
        out += _build_frame(
            chunk, scid, vcid, mc_count, vc_count, status, randomise
        )
    return bytes(out)


def split_space_packets(data: bytes) -> list[bytes]:
    """
    Split a concatenation of CCSDS Space Packets using their length fields.

    Raises ValueError if the stream is truncated or malformed, which is what
    makes packet-mode framing safe to attempt.
    """
    packets = []
    pos = 0
    while pos < len(data):
        if pos + 6 > len(data):
            raise ValueError("truncated Space Packet primary header")
        (length_field,) = struct.unpack(">H", data[pos + 4 : pos + 6])
        total = 6 + length_field + 1
        if pos + total > len(data):
            raise ValueError("Space Packet length field runs past end of data")
        packets.append(data[pos : pos + total])
        pos += total
    return packets


def _idle_packet(size: int) -> bytes:
    """An idle Space Packet (APID 0x7FF) of exactly ``size`` bytes."""
    if size < 7:
        raise ValueError("idle packet needs at least 7 bytes")
    body = bytes(size - 6)
    return struct.pack(">HHH", 0x07FF, 0b11 << 14, size - 6 - 1) + body


def _encode_packet_frames(
    data: bytes,
    scid: int,
    vcid: int,
    counter: TmFrameCounter | None,
    randomise: bool,
) -> bytes:
    packets = split_space_packets(data)
    if counter is None:
        counter = TmFrameCounter()

    out = bytearray()
    field = bytearray()
    # Offset of the first packet that STARTS in the field being built, if any.
    first_start: int | None = None

    def flush() -> None:
        nonlocal field, first_start
        chunk = bytes(field)
        if len(chunk) < FRAME_DATA_SIZE:
            remaining = FRAME_DATA_SIZE - len(chunk)
            if remaining >= 7:
                # Standard fill is an idle packet, which keeps the data field
                # parseable as a packet sequence all the way to its end.
                if first_start is None:
                    first_start = len(chunk)
                chunk += _idle_packet(remaining)
            else:
                chunk += bytes([FILL_BYTE] * remaining)
        fhp = FHP_NO_PACKET_START if first_start is None else first_start
        mc_count, vc_count = counter.next(vcid)
        out.extend(
            _build_frame(
                chunk,
                scid,
                vcid,
                mc_count,
                vc_count,
                _data_field_status(MODE_PACKET, fhp),
                randomise,
            )
        )
        field = bytearray()
        first_start = None

    for pkt in packets:
        offset = 0
        while offset < len(pkt):
            if offset == 0 and first_start is None:
                first_start = len(field)
            space = FRAME_DATA_SIZE - len(field)
            take = min(space, len(pkt) - offset)
            field.extend(pkt[offset : offset + take])
            offset += take
            if len(field) == FRAME_DATA_SIZE:
                flush()

    if field:
        flush()
    return bytes(out)


def decode_frames(wire: bytes, randomise: bool = True) -> tuple[bytes, dict]:
    """
    Recover data fields from a stream of TM Transfer Frames.

    The stream is searched for the ASM rather than assumed to start on a frame
    boundary, so a capture that begins mid-frame or contains gaps still
    decodes. Frames whose FECF does not verify are dropped.
    """
    if not isinstance(wire, bytes):
        raise TypeError("wire must be bytes")

    out = bytearray()
    n_frames = 0
    n_crc_errors = 0

    i = 0
    limit = len(wire) - WIRE_FRAME_SIZE
    while i <= limit:
        if wire[i : i + 4] != ASM:
            i += 1
            continue

        frame = wire[i + 4 : i + WIRE_FRAME_SIZE]
        if randomise:
            frame = apply_prng(frame)

        header = frame[:FRAME_HEADER_SIZE]
        data_field = frame[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + FRAME_DATA_SIZE]
        (fecf_recv,) = struct.unpack(">H", frame[FRAME_HEADER_SIZE + FRAME_DATA_SIZE :])

        if crc16_ccitt(header + data_field) != fecf_recv:
            n_crc_errors += 1
            # A bad CRC may mean this was not really a frame start.
            i += 1
            continue

        out += data_field
        n_frames += 1
        i += WIRE_FRAME_SIZE

    return bytes(out), {"n_frames": n_frames, "n_crc_errors": n_crc_errors}


def frame_info(wire: bytes, randomise: bool = True) -> list[dict]:
    """Decode frame headers only. Useful for inspecting a capture."""
    info = []
    i = 0
    limit = len(wire) - WIRE_FRAME_SIZE
    while i <= limit:
        if wire[i : i + 4] != ASM:
            i += 1
            continue
        frame = wire[i + 4 : i + WIRE_FRAME_SIZE]
        if randomise:
            frame = apply_prng(frame)
        word1, mc, vc, status = struct.unpack(">HBBH", frame[:FRAME_HEADER_SIZE])
        sync_flag = (status >> 14) & 1
        info.append(
            {
                "offset": i,
                "scid": (word1 >> 4) & 0x3FF,
                "vcid": (word1 >> 1) & 0x7,
                "mc_frame_count": mc,
                "vc_frame_count": vc,
                "sync_flag": sync_flag,
                "first_header_pointer": None if sync_flag else status & 0x7FF,
            }
        )
        i += WIRE_FRAME_SIZE
    return info


def make_idle_frame(
    scid: int,
    vcid: int = 0,
    counter: TmFrameCounter | None = None,
    randomise: bool = True,
) -> bytes:
    """An OID (only idle data) transfer frame for link keep-alive."""
    if counter is None:
        counter = TmFrameCounter()
    mc_count, vc_count = counter.next(vcid)
    return _build_frame(
        bytes([FILL_BYTE] * FRAME_DATA_SIZE),
        scid,
        vcid,
        mc_count,
        vc_count,
        _data_field_status(MODE_PACKET, FHP_IDLE_DATA),
        randomise,
    )
