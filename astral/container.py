from typing import NamedTuple

from .crc import crc8_j1850

SYNC0 = 0xA5
SYNC1 = 0xE6

ATOM_SIZE = 32
HEADER_GIST = 0
FOUNTAIN_PACKET = 1
DICT_UPDATE = 2
MCKAY_GIST = 3  # McKay compression metadata (see codec.pack_mckay_message)

# Atom format version, carried in byte 2.
#   1: original format
#   2: the header atom carries a CRC-32 of the assembled payload
ATOM_VERSION = 2


class Atom(NamedTuple):
    """One parsed atom. Indexes 0-4 match the historical 5-tuple layout."""

    atom_index: int
    total_atoms: int
    message_id: int
    atom_type: int
    payload: bytes
    version: int


def make_atom(
    atom_index,
    total_atoms,
    message_id,
    atom_type,
    payload21: bytes,
    version_flags=ATOM_VERSION,
) -> bytes:
    # Input validation
    if not isinstance(atom_index, int) or atom_index < 0 or atom_index > 65535:
        raise ValueError("atom_index must be 0-65535")
    if not isinstance(total_atoms, int) or total_atoms <= 0 or total_atoms > 65535:
        raise ValueError("total_atoms must be 1-65535")
    if not isinstance(message_id, int) or message_id < 0 or message_id > 65535:
        raise ValueError("message_id must be 0-65535")
    if not isinstance(atom_type, int) or atom_type < 0 or atom_type > 255:
        raise ValueError("atom_type must be 0-255")
    if not isinstance(payload21, bytes):
        raise ValueError("payload21 must be bytes")
    if len(payload21) > 21:
        raise ValueError("payload21 must be <= 21 bytes")
    b = bytearray(ATOM_SIZE)
    b[0] = SYNC0
    b[1] = SYNC1
    b[2] = version_flags & 0xFF
    b[3] = atom_index & 0xFF
    b[4] = (atom_index >> 8) & 0xFF
    b[5] = total_atoms & 0xFF
    b[6] = (total_atoms >> 8) & 0xFF
    b[7] = message_id & 0xFF
    b[8] = (message_id >> 8) & 0xFF
    b[9] = atom_type & 0xFF
    p = payload21 + bytes(21 - len(payload21))
    b[10:31] = p
    b[31] = crc8_j1850(bytes(b[:31])) & 0xFF
    return bytes(b)


def parse_atoms(stream: bytes):
    """
    Extract atoms from a received byte stream.

    The stream is scanned for the sync word rather than assumed to start on an
    atom boundary: a real link hands over bytes with slips, truncated leading
    fragments and gaps, and an aligned-only parser throws the whole message
    away when the stream is off by one byte. An atom is accepted only when its
    sync word and CRC-8 both check out, after which scanning resumes at the end
    of that atom.
    """
    if not isinstance(stream, bytes):
        raise ValueError("stream must be bytes")

    out = []
    i = 0
    limit = len(stream) - ATOM_SIZE
    while i <= limit:
        if stream[i] != SYNC0 or stream[i + 1] != SYNC1:
            i += 1
            continue
        chunk = stream[i : i + ATOM_SIZE]
        if (crc8_j1850(chunk[:31]) & 0xFF) != chunk[31]:
            i += 1
            continue
        out.append(
            Atom(
                atom_index=chunk[3] | (chunk[4] << 8),
                total_atoms=chunk[5] | (chunk[6] << 8),
                message_id=chunk[7] | (chunk[8] << 8),
                atom_type=chunk[9],
                payload=bytes(chunk[10:31]),
                version=chunk[2],
            )
        )
        i += ATOM_SIZE
    return out
