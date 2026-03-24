"""
CCSDS 133.0-B-2 Space Packet Protocol wrapper for ASTRAL atom streams.

This module provides a thin outer envelope that wraps ASTRAL atom streams
in CCSDS Space Packet headers. Ground stations (COSMOS, OpenMCT, SatNOGS,
gr-satellites) can parse and route these packets without custom logic.

Nothing about the ASTRAL codec, fountain code, or atom format is changed;
this is purely a framing layer.
"""

import struct

# APID assignments: (APID, packet_type) where packet_type 0=TM, 1=TC
APID_MAP: dict[str, tuple[int, int]] = {
    "DETECT":    (0x010, 0),
    "STATUS":    (0x011, 0),
    "TEXT":      (0x012, 0),
    "VOICE":     (0x013, 0),
    "CMD":       (0x100, 1),
    "CMD_BATCH": (0x101, 1),
}

APID_IDLE = 0x7FF

# Build reverse map once at module level: APID → message type string
_APID_TO_TYPE: dict[int, str] = {
    apid: msg_type for msg_type, (apid, _) in APID_MAP.items()
}


class SpacePacketSequenceCounter:
    """
    Maintains one 14-bit modular sequence counter per APID.

    The counter wraps at 16384 (2^14). Thread safety is not required.
    """

    def __init__(self):
        self._counts: dict[int, int] = {}

    def next(self, apid: int) -> int:
        """
        Return the current count for apid and advance it mod 16384.

        Parameters
        ----------
        apid : int
            APID (0–0x7FF).

        Returns
        -------
        int
            The current count (0–16383) before increment.
        """
        c = self._counts.get(apid, 0)
        self._counts[apid] = (c + 1) % 16384
        return c

    def reset(self, apid: int | None = None) -> None:
        """
        Reset counter for one APID, or all APIDs if apid is None.

        Parameters
        ----------
        apid : int, optional
            If provided, reset only this APID. If None, reset all.
        """
        if apid is None:
            self._counts.clear()
        else:
            self._counts[apid] = 0


def wrap(
    astral_stream: bytes,
    msg_type: str,
    counter: SpacePacketSequenceCounter,
) -> bytes:
    """
    Wrap an ASTRAL atom stream in a CCSDS Space Packet header.

    The ASTRAL stream becomes the User Data Field. The 6-byte primary
    header is prepended in big-endian format.

    Parameters
    ----------
    astral_stream : bytes
        Raw ASTRAL atom stream (output of any pack_* function).
    msg_type : str
        ASTRAL message type key, e.g. "DETECT", "CMD".
        Must be a key in APID_MAP.
    counter : SpacePacketSequenceCounter
        Sequence counter; counter.next(apid) is called once.

    Returns
    -------
    bytes
        Complete CCSDS Space Packet (6-byte header + astral_stream).

    Raises
    ------
    ValueError
        If msg_type is not in APID_MAP, astral_stream is not bytes or empty,
        or astral_stream exceeds 65536 bytes.
    """
    # Validate inputs
    if not isinstance(astral_stream, bytes) or len(astral_stream) == 0:
        raise ValueError("astral_stream must be non-empty bytes")

    if msg_type not in APID_MAP:
        raise ValueError(f"unknown msg_type '{msg_type}'")

    if len(astral_stream) > 65536:
        raise ValueError(
            f"astral_stream size {len(astral_stream)} exceeds "
            f"maximum 65536 bytes"
        )

    apid, packet_type = APID_MAP[msg_type]
    seq_count = counter.next(apid)

    # Build 6-byte header in big-endian
    word1 = (0b000 << 13) | (packet_type << 12) | (0 << 11) | apid
    word2 = (0b11 << 14) | seq_count
    data_length = len(astral_stream) - 1

    header = struct.pack(">HHH", word1, word2, data_length)
    return header + astral_stream


def unwrap(packet: bytes) -> dict:
    """
    Parse a CCSDS Space Packet and return header fields and payload.

    This is the inverse of wrap().

    Parameters
    ----------
    packet : bytes
        A complete CCSDS Space Packet (header + data).

    Returns
    -------
    dict
        A dict with keys:
        - version: int (always 0 for CCSDS version 1)
        - packet_type: int (0=TM, 1=TC)
        - sec_hdr_flag: int (0 or 1)
        - apid: int (0x000–0x7FF)
        - seq_flags: int (should be 3 for standalone)
        - seq_count: int (0–16383)
        - msg_type: str (looked up from APID_MAP; "UNKNOWN" if not found)
        - astral_stream: bytes (the payload)

    Raises
    ------
    ValueError
        If packet is not bytes, too short (< 6 bytes),
        version bits are not 0b000, or data_length mismatch.
    """
    if not isinstance(packet, bytes) or len(packet) < 6:
        raise ValueError(
            "packet must be bytes with len >= 6"
        )

    w1, w2, data_length = struct.unpack(">HHH", packet[:6])

    version = (w1 >> 13) & 0x7
    if version != 0b000:
        raise ValueError(
            f"packet version must be 0b000, got {version:03b}"
        )

    packet_type = (w1 >> 12) & 0x1
    sec_hdr_flag = (w1 >> 11) & 0x1
    apid = w1 & 0x7FF

    seq_flags = (w2 >> 14) & 0x3
    seq_count = w2 & 0x3FFF

    # Verify data_length consistency
    expected_data_len = len(packet) - 6
    if expected_data_len != data_length + 1:
        raise ValueError(
            f"data_length field says {data_length + 1} bytes, "
            f"but packet has {expected_data_len} bytes"
        )

    astral_stream = packet[6:]

    msg_type = _APID_TO_TYPE.get(apid, "UNKNOWN")

    return {
        "version": version,
        "packet_type": packet_type,
        "sec_hdr_flag": sec_hdr_flag,
        "apid": apid,
        "seq_flags": seq_flags,
        "seq_count": seq_count,
        "msg_type": msg_type,
        "astral_stream": astral_stream,
    }


def make_idle_packet() -> bytes:
    """
    Create a minimal CCSDS idle Space Packet.

    Idle packets are used by ground stations to maintain link
    synchronisation when there is no data to send.

    Returns
    -------
    bytes
        A 7-byte idle packet with APID=0x7FF, one zero byte payload.
    """
    word1 = APID_IDLE  # version=000, type=0, sec_hdr=0, apid=0x7FF
    word2 = 0b11 << 14  # seq_flags=11, seq_count=0
    data_length = 0  # one byte of data field, value = len - 1 = 0
    header = struct.pack(">HHH", word1, word2, data_length)
    return header + b"\x00"
