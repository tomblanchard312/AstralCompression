from .crc import crc8_j1850

SYNC0 = 0xA5
SYNC1 = 0xE6

ATOM_SIZE = 32
HEADER_GIST = 0
FOUNTAIN_PACKET = 1
DICT_UPDATE = 2  # reserved


def make_atom(
    atom_index,
    total_atoms,
    message_id,
    atom_type,
    payload21: bytes,
    version_flags=0x01,
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
    # Input validation
    if not isinstance(stream, bytes):
        raise ValueError("stream must be bytes")

    out = []
    for i in range(0, len(stream), ATOM_SIZE):
        chunk = stream[i : i + ATOM_SIZE]
        if len(chunk) < ATOM_SIZE:
            break
        if chunk[0] != SYNC0 or chunk[1] != SYNC1:
            continue
        try:
            crc = crc8_j1850(chunk[:31])
            if (crc & 0xFF) != chunk[31]:
                continue
            idx = chunk[3] | (chunk[4] << 8)
            total = chunk[5] | (chunk[6] << 8)
            msg_id = chunk[7] | (chunk[8] << 8)
            typ = chunk[9]
            payload21 = bytes(chunk[10:31])
            out.append((idx, total, msg_id, typ, payload21))
        except (IndexError, ValueError):
            # Skip malformed atoms
            continue
    return out
