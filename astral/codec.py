import os
import struct
from .container import make_atom, parse_atoms
from .container import HEADER_GIST, FOUNTAIN_PACKET
from .grammar import make_gist_bits, encode_payload, decode_payload
from .spacepacket import SpacePacketSequenceCounter, wrap as _sp_wrap
from .rs_fec import encode_stream as _rs_encode, decode_stream as _rs_decode
from .tmframe import encode_frames as _tm_encode, decode_frames as _tm_decode
from .grammar import parse_gist
from .textpack import encode_text, decode_text
from .commands import (
    encode_cmd,
    decode_cmd,
    encode_cmd_batch,
    decode_cmd_batch,
)
from .voice import encode_wav_to_bitstream
from .dict_update import split_words_from_atoms, make_dict_update_atoms
from .fountain import lt_encode_blocks, lt_decode_blocks

SYMBOL_SIZE = 16  # bytes per source block for the fountain code


def chunk_blocks(payload: bytes, symbol_size: int):
    blocks = []
    for i in range(0, len(payload), symbol_size):
        chunk = payload[i:i + symbol_size]
        if len(chunk) < symbol_size:
            chunk = chunk + bytes(symbol_size - len(chunk))
        blocks.append(chunk)
    if not blocks:
        blocks.append(bytes(symbol_size))
    return blocks


def pack_message(
    msg: dict, message_id: int | None = None, extra_fountain=0
):
    # Input validation
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dictionary")
    if "type" not in msg:
        raise ValueError("msg must contain 'type' field")
    if extra_fountain < 0:
        raise ValueError("extra_fountain must be non-negative")

    if message_id is None:
        message_id = (int.from_bytes(os.urandom(2), "little") or 1)

    # 1) Build gist and payload
    gist_bytes, gist_bits = make_gist_bits(msg)
    payload = encode_payload(msg)
    payload_len = len(payload)
    blocks = chunk_blocks(payload, SYMBOL_SIZE)
    K = len(blocks)
    fountain_seed = int.from_bytes(os.urandom(4), "little") or 1
    header = bytearray(21)
    header[0] = K & 0xFF
    header[1] = (K >> 8) & 0xFF
    header[2] = SYMBOL_SIZE & 0xFF
    header[3:7] = fountain_seed.to_bytes(4, "little")
    header[7] = payload_len & 0xFF
    header[8] = (payload_len >> 8) & 0xFF
    header[9] = gist_bits & 0xFF
    gist_room = 21 - 10
    header[10: 10 + min(len(gist_bytes), gist_room)] = gist_bytes[:gist_room]

    atoms = []
    atoms.append((0, HEADER_GIST, bytes(header)))

    # 4) Fountain packets - Increased redundancy for better recovery
    M = K + max(10, K) + int(extra_fountain)
    packets = lt_encode_blocks(
        blocks, seed=fountain_seed, num_packets=M
    )
    for i, (seed, degree, block) in enumerate(packets, start=1):
        p = bytearray(21)
        p[0:4] = seed.to_bytes(4, "little")
        p[4] = degree & 0xFF
        p[5:21] = block[:16]
        atoms.append((i, FOUNTAIN_PACKET, bytes(p)))

    total_atoms = len(atoms)
    out = bytearray()
    for idx, typ, payload21 in atoms:
        out += make_atom(idx, total_atoms, message_id, typ, payload21)
    return bytes(out)


def pack_text_with_dict(
    words: list[str],
    text: str,
    extra_fountain=0,
    message_id: int | None = None,
):
    if message_id is None:
        message_id = (int.from_bytes(os.urandom(2), "little") or 1)
    # 1) send dict update atoms
    msgmeta = {"type": "TEXT", "conf": 0.99}
    gist_bytes, gist_bits = make_gist_bits(msgmeta)
    # header with zero payload_len; follow with fountain payload atoms
    payload = encode_text(text)
    payload_len = len(payload)
    blocks = chunk_blocks(payload, SYMBOL_SIZE)
    K = len(blocks)
    fountain_seed = int.from_bytes(os.urandom(4), "little") or 1
    header = bytearray(21)
    header[0] = K & 0xFF
    header[1] = (K >> 8) & 0xFF
    header[2] = SYMBOL_SIZE & 0xFF
    header[3:7] = fountain_seed.to_bytes(4, "little")
    header[7] = payload_len & 0xFF
    header[8] = (payload_len >> 8) & 0xFF
    header[9] = gist_bits & 0xFF
    gist_room = 21 - 10
    header[10: 10 + min(len(gist_bytes), gist_room)] = gist_bytes[:gist_room]
    atoms = []
    atoms.append((0, HEADER_GIST, bytes(header)))
    # DICT_UPDATE atoms (insert right after header)
    for dp in make_dict_update_atoms(words):
        atoms.append((len(atoms), 2, dp))
    # Increased redundancy for better recovery
    M = K + max(10, K) + int(extra_fountain)
    packets = lt_encode_blocks(
        blocks, seed=fountain_seed, num_packets=M
    )
    for i, (seed, degree, block) in enumerate(packets, start=len(atoms)):
        p = bytearray(21)
        p[0:4] = seed.to_bytes(4, "little")
        p[4] = degree & 0xFF
        p[5:21] = block[:16]
        atoms.append((i, FOUNTAIN_PACKET, bytes(p)))
    total_atoms = len(atoms)
    out = bytearray()
    # reindex atoms sequentially from 0..N-1 with correct total
    for new_idx, (old_idx, typ, payload21) in enumerate(atoms):
        out += make_atom(new_idx, total_atoms, message_id, typ, payload21)
    return bytes(out)


def pack_text_message(
    text: str, extra_fountain=0, message_id: int | None = None
):
    # Input validation
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if extra_fountain < 0:
        raise ValueError("extra_fountain must be non-negative")

    msg = {"type": "TEXT", "conf": 0.99}
    return _pack_with_custom_payload(
        msg, encode_text(text), extra_fountain, message_id
    )


def pack_cmd_message(
    cmd: dict,
    extra_fountain=0,
    message_id: int | None = None,
    key: bytes | None = None,
):
    payload = encode_cmd(cmd, key=key)
    msg = {"type": "CMD", "conf": 0.99}
    return _pack_with_custom_payload(
        msg, payload, extra_fountain, message_id
    )


def pack_voice_message(
    wav_path: str,
    extra_fountain=0,
    message_id: int | None = None,
):
    payload = encode_wav_to_bitstream(wav_path)
    msg = {"type": "VOICE", "conf": 0.9}
    return _pack_with_custom_payload(
        msg, payload, extra_fountain, message_id
    )


def pack_cmd_batch(
    batch: dict,
    extra_fountain=0,
    message_id: int | None = None,
    key: bytes | None = None,
):
    payload = encode_cmd_batch(batch, key=key)
    msg = {"type": "CMD_BATCH", "conf": 0.99}
    return _pack_with_custom_payload(
        msg, payload, extra_fountain, message_id
    )


def _pack_with_custom_payload(
    msgmeta: dict, payload: bytes, extra_fountain=0, message_id=None
):
    if message_id is None:
        message_id = (int.from_bytes(os.urandom(2), "little") or 1)
    gist_bytes, gist_bits = make_gist_bits(msgmeta)
    payload_len = len(payload)
    blocks = chunk_blocks(payload, SYMBOL_SIZE)
    K = len(blocks)
    fountain_seed = int.from_bytes(os.urandom(4), "little") or 1
    header = bytearray(21)
    header[0] = K & 0xFF
    header[1] = (K >> 8) & 0xFF
    header[2] = SYMBOL_SIZE & 0xFF
    header[3:7] = fountain_seed.to_bytes(4, "little")
    header[7] = payload_len & 0xFF
    header[8] = (payload_len >> 8) & 0xFF
    header[9] = gist_bits & 0xFF
    gist_room = 21 - 10
    header[10:10 + min(len(gist_bytes), gist_room)] = gist_bytes[:gist_room]
    atoms = []
    atoms.append((0, HEADER_GIST, bytes(header)))
    M = K + max(10, K) + int(extra_fountain)
    packets = lt_encode_blocks(
        blocks, seed=fountain_seed, num_packets=M
    )
    for i, (seed, degree, block) in enumerate(packets, start=1):
        p = bytearray(21)
        p[0:4] = seed.to_bytes(4, "little")
        p[4] = degree & 0xFF
        p[5:21] = block[:16]
        atoms.append((i, FOUNTAIN_PACKET, bytes(p)))
    total_atoms = len(atoms)
    out = bytearray()
    for idx, typ, payload21 in atoms:
        out += make_atom(
            idx, total_atoms, message_id, typ, payload21
        )
    return bytes(out)


def unpack_stream(stream: bytes):
    # Input validation
    if not isinstance(stream, bytes):
        raise ValueError("stream must be bytes")
    if len(stream) == 0:
        return {"error": "empty stream"}

    atoms = parse_atoms(stream)
    if not atoms:
        return {"error": "no valid atoms"}

    msg_id = atoms[0][2]
    header_atom = None
    fountain_atoms = []
    dict_atoms = []
    total_atoms = atoms[0][1]
    for idx, total, mid, typ, payload in atoms:
        if mid != msg_id:
            continue
        total_atoms = total
        if typ == 0 and header_atom is None:
            header_atom = payload
        elif typ == 1:
            fountain_atoms.append(payload)
        elif typ == 2:
            dict_atoms.append(payload)

    if header_atom is None:
        return {"error": "missing header/gist atom"}

    K = header_atom[0] | (header_atom[1] << 8)
    symbol_size = header_atom[2]
    payload_len = header_atom[7] | (header_atom[8] << 8)
    gist_bits = header_atom[9]
    gist_room = 21 - 10
    # Calculate how many bytes the gist bits actually occupy
    gist_bytes_needed = (gist_bits + 7) // 8
    gist_bytes = header_atom[10:10 + min(gist_bytes_needed, gist_room)]

    gist = parse_gist(gist_bytes, gist_bits)

    packets = []
    for p in fountain_atoms:
        seed = int.from_bytes(p[0:4], "little")
        degree = p[4]
        block = bytes(p[5:21])
        packets.append((seed, degree, block))

    complete = False
    recovered_fraction = 0.0
    message = None

    if packets:
        recovered, frac = lt_decode_blocks(packets, K, symbol_size)
        recovered_fraction = frac
        if recovered is not None:
            payload = b"".join(recovered)[:payload_len]
            try:
                mtype = gist.get("type")
                if dict_atoms:
                    extra_words = split_words_from_atoms(dict_atoms)
                else:
                    extra_words = []
                if mtype == "TEXT":
                    if extra_words:
                        message = {
                            "type": "TEXT",
                            "text": decode_text(payload),
                            "extra_words": extra_words,
                        }
                    else:
                        message = {"type": "TEXT",
                                   "text": decode_text(payload)}
                elif mtype == "VOICE":
                    message = {"type": "VOICE", "bytes": payload}
                elif mtype == "CMD":
                    message = {"type": "CMD", "cmd": decode_cmd(payload)}
                elif mtype == "CMD_BATCH":
                    message = {"type": "CMD_BATCH",
                               "batch": decode_cmd_batch(payload)}
                else:
                    message = decode_payload(payload)
                complete = True
            except Exception:
                complete = False
                message = None

    return {
        "message_id": msg_id,
        "total_atoms": total_atoms,
        "received_atoms": len(atoms),
        "gist": gist,
        "complete": complete,
        "recovered_fraction": recovered_fraction,
        "message": message,
    }


def pack_message_sp(
    msg: dict,
    counter: SpacePacketSequenceCounter,
    message_id: int | None = None,
    extra_fountain: int = 0,
) -> bytes:
    """
    Pack a telemetry/command message and wrap it in a CCSDS Space Packet.

    Parameters
    ----------
    msg : dict
        Message dict with at least a ``type`` key.
    counter : SpacePacketSequenceCounter
        Sequence counter; advanced once per call.
    message_id : int, optional
        ASTRAL message ID (16-bit). Generated randomly if omitted.
    extra_fountain : int
        Extra fountain redundancy packets.

    Returns
    -------
    bytes
        Complete CCSDS Space Packet (6-byte header + ASTRAL atom stream).
    """
    astral_stream = pack_message(
        msg, message_id=message_id, extra_fountain=extra_fountain
    )
    return _sp_wrap(astral_stream, msg["type"], counter)


def unpack_stream_sp(packet: bytes) -> dict:
    """
    Unwrap a CCSDS Space Packet and decode the enclosed ASTRAL stream.

    Parameters
    ----------
    packet : bytes
        A complete CCSDS Space Packet as produced by ``pack_message_sp``
        or any conforming encoder.

    Returns
    -------
    dict
        A merged dict with Space Packet header fields and the decoded
        ASTRAL payload::

            {
                "apid":             int,
                "packet_type":      int,
                "seq_count":        int,
                "msg_type":         str,
                # --- all keys from unpack_stream() ---
                "message_id":       int,
                "total_atoms":      int,
                "received_atoms":   int,
                "gist":             dict,
                "complete":         bool,
                "recovered_fraction": float,
                "message":          dict | None,
            }

    The function never raises — if the Space Packet header is invalid it
    returns ``{"error": "<reason>"}``; if ASTRAL decoding fails the
    ``unpack_stream`` error key is preserved.
    """
    from .spacepacket import unwrap as _sp_unwrap

    try:
        sp = _sp_unwrap(packet)
    except (ValueError, struct.error) as exc:
        return {"error": f"space packet parse error: {exc}"}
    astral_result = unpack_stream(sp["astral_stream"])
    return {
        "apid": sp["apid"],
        "packet_type": sp["packet_type"],
        "seq_count": sp["seq_count"],
        "msg_type": sp["msg_type"],
        **astral_result,
    }


def pack_message_rs(
    msg: dict,
    message_id: int | None = None,
    extra_fountain: int = 0,
    e: int = 16,
) -> bytes:
    """
    Pack a message and protect every atom with CCSDS Reed-Solomon FEC.

    Parameters
    ----------
    msg : dict
        Message dict with at least a ``type`` key.
    message_id : int, optional
        ASTRAL message ID. Generated randomly if omitted.
    extra_fountain : int
        Extra fountain redundancy packets.
    e : int
        RS error-correction strength: ``8`` (corrects <=8 byte errors/atom)
        or ``16`` (corrects <=16 byte errors/atom). Default ``16``.

    Returns
    -------
    bytes
        RS-protected byte stream: one 64-byte (E=16) or 48-byte (E=8)
        codeword per source atom.
    """
    astral_stream = pack_message(
        msg, message_id=message_id, extra_fountain=extra_fountain
    )
    return _rs_encode(astral_stream, e=e)


def unpack_stream_rs(rs_stream: bytes, e: int = 16) -> dict:
    """
    Decode an RS-protected stream produced by ``pack_message_rs``.

    Corrects bit errors, drops uncorrectable atoms (the fountain code
    recovers from the resulting erasures), then decodes the ASTRAL payload.

    Parameters
    ----------
    rs_stream : bytes
        RS-protected stream as produced by ``pack_message_rs`` or any
        conforming encoder.
    e : int
        Must match the value used during encoding.

    Returns
    -------
    dict
        All keys from ``unpack_stream()``, plus:

        ``rs_e`` : int
            The E value used for decoding.
        ``rs_corrected_symbols`` : int
            Total RS symbols corrected across all codewords.
        ``rs_uncorrectable_atoms`` : int
            Atoms dropped because their codeword had more than E errors.

        The function never raises. If RS decoding fails entirely it returns
        ``{"error": "<reason>", "rs_e": e}``.
    """
    try:
        astral_stream, n_corrected, n_uncorrectable = _rs_decode(
            rs_stream, e=e
        )
    except (TypeError, ValueError) as exc:
        return {"error": f"rs decode error: {exc}", "rs_e": e}

    astral_result = unpack_stream(astral_stream)
    return {
        "rs_e": e,
        "rs_corrected_symbols": n_corrected,
        "rs_uncorrectable_atoms": n_uncorrectable,
        **astral_result,
    }


def pack_message_tm(
    msg: dict,
    scid: int,
    vcid: int = 0,
    message_id: int | None = None,
    extra_fountain: int = 0,
    randomise: bool = True,
    counter=None,
) -> bytes:
    """
    Pack a message and segment it into CCSDS TM Transfer Frames.

    Parameters
    ----------
    msg : dict
        Message dict with at least a ``type`` key.
    scid : int
        Spacecraft ID (10-bit, 0-1023).
    vcid : int
        Virtual Channel ID (3-bit, 0-7). Default 0.
    message_id : int, optional
        ASTRAL message ID. Generated randomly if omitted.
    extra_fountain : int
        Extra fountain redundancy packets.
    randomise : bool
        Apply CCSDS pseudo-randomizer to frame data fields. Default True.
    counter : TmFrameCounter, optional
        Frame sequence counter. A fresh one is created if omitted.

    Returns
    -------
    bytes
        Concatenated wire-format TM Transfer Frames (each 1119 bytes),
        ready to hand to the modulator.
    """
    astral_stream = pack_message(
        msg, message_id=message_id, extra_fountain=extra_fountain
    )
    return _tm_encode(
        astral_stream,
        scid=scid,
        vcid=vcid,
        counter=counter,
        randomise=randomise,
    )


def unpack_frames_tm(
    wire: bytes,
    original_length: int | None = None,
    randomise: bool = True,
) -> dict:
    """
    Decode TM Transfer Frames and recover the enclosed ASTRAL stream.

    Frames with CRC errors are dropped; the fountain code recovers from
    the resulting atom erasures.

    Parameters
    ----------
    wire : bytes
        Raw wire bytes as received from the demodulator.
    original_length : int, optional
        If provided, trim the recovered data field to this many bytes before
        passing to ``unpack_stream``. Useful when the caller knows the exact
        ASTRAL stream length. If omitted, the full concatenated data fields
        (including any fill bytes) are passed to ``unpack_stream``.
    randomise : bool
        Must match the value used during encoding. Default True.

    Returns
    -------
    dict
        All keys from ``unpack_stream()``, plus:

        ``"tm_n_frames"`` : int
            Number of frames successfully decoded.
        ``"tm_n_crc_errors"`` : int
            Number of frames dropped due to CRC failure.

        Never raises. CRC-failed frames are dropped silently; if the
        remaining atoms are insufficient for fountain recovery the result
        will have ``"complete": False``.
    """
    try:
        data, stats = _tm_decode(wire, randomise=randomise)
    except (TypeError, ValueError) as exc:
        return {
            "tm_n_frames": 0,
            "tm_n_crc_errors": 0,
            "error": f"tm decode error: {exc}",
        }

    if original_length is not None:
        data = data[:original_length]
    astral_result = unpack_stream(data)
    return {
        "tm_n_frames": stats["n_frames"],
        "tm_n_crc_errors": stats["n_crc_errors"],
        **astral_result,
    }
