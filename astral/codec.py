from __future__ import annotations

import math
import os
import struct
import zlib
from .container import make_atom, parse_atoms
from .container import HEADER_GIST, FOUNTAIN_PACKET, DICT_UPDATE, MCKAY_GIST
from .grammar import make_gist_bits, encode_payload, decode_payload
from .grammar import parse_gist
from .textpack import encode_text, decode_text
from .commands import (
    CommandAuthError,
    encode_cmd,
    decode_cmd,
    encode_cmd_batch,
    decode_cmd_batch,
)
from .voice import encode_wav_to_bitstream
from .dict_update import split_words_from_atoms, make_dict_update_atoms
from .fountain import lt_encode_blocks, lt_decode_blocks

SYMBOL_SIZE = 16  # bytes per source block for the fountain code
HEADER_REDUNDANCY = 4  # replicate header atoms to reduce header-loss failures
MAX_ATOMS = 65535  # atom_index/total_atoms are 16-bit fields
HEADER_FRACTION = 0.1  # header copies as a share of the fountain atom count


def header_redundancy_for(loss_rate: float, confidence: float = 0.99) -> int:
    """
    Header copies needed to keep the gist alive at a given loss rate.

    The gist and the fountain parameters live only in the header atom, so if
    every copy is dropped the receiver gets nothing at all. With independent
    losses, n copies survive with probability ``1 - loss_rate**n``; this
    returns the smallest n meeting ``confidence``.

    At 80% loss, 99% confidence needs 21 copies, which is 672 bytes: cheap
    next to losing the message.
    """
    if not 0.0 <= loss_rate < 1.0:
        raise ValueError("loss_rate must be in [0, 1)")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if loss_rate == 0.0:
        return 1
    return max(1, math.ceil(math.log(1.0 - confidence) / math.log(loss_rate)))


def fountain_atom_count(
    K: int,
    min_redundancy: int = 10,
    redundancy: float = 1.0,
    extra_fountain: int = 0,
) -> int:
    """
    Number of fountain atoms to emit for K source blocks.

    ``redundancy`` is the proportional overhead: 1.0 (the default) sends twice
    the payload, 0.3 sends 30% extra. The decoder peels and then eliminates,
    so large messages recover from about 5% overhead; small ones need more,
    which is what ``min_redundancy`` guarantees.
    """
    if redundancy < 0:
        raise ValueError("redundancy must be non-negative")
    return (
        K
        + max(int(min_redundancy), math.ceil(K * float(redundancy)))
        + int(extra_fountain)
    )


def _resolve_header_redundancy(header_redundancy, fountain_atoms: int) -> int:
    """Default header replication, scaled with message size."""
    if header_redundancy is not None:
        n = int(header_redundancy)
        if n < 1:
            raise ValueError("header_redundancy must be >= 1")
        return n
    return max(HEADER_REDUNDANCY, math.ceil(HEADER_FRACTION * fountain_atoms))


GIST_ROOM = 5  # bytes the 33-bit gist occupies in the header atom
PAYLOAD_CRC_OFFSET = 16  # header bytes 16-19: CRC-32 of the assembled payload


def max_payload_bytes(min_redundancy: int = 10, extra_fountain: int = 0) -> int:
    """
    Largest payload that still fits inside the 16-bit atom counters.

    A payload of K blocks is sent as ``HEADER_REDUNDANCY`` header atoms plus
    ``K + max(min_redundancy, K) + extra_fountain`` fountain atoms, so the
    limit is roughly a quarter of a million source blocks' worth once the
    redundancy is accounted for.
    """
    budget = MAX_ATOMS - HEADER_REDUNDANCY - int(extra_fountain)
    if budget <= 0:
        return 0
    # K + max(min_redundancy, K) <= budget. For K >= min_redundancy that is
    # 2K <= budget.
    k = budget // 2
    if k < int(min_redundancy):
        k = max(0, budget - int(min_redundancy))
    return k * SYMBOL_SIZE


def _check_payload_size(
    payload_len: int, min_redundancy: int = 10, extra_fountain: int = 0
) -> None:
    limit = max_payload_bytes(min_redundancy, extra_fountain)
    if payload_len > limit:
        raise ValueError(
            f"payload too large for a single ASTRAL message: {payload_len} "
            f"bytes (max {limit} at this redundancy). Split it across "
            f"messages."
        )


def _command_auth_state(message):
    """True/False for CMD messages, None when the message is not a command."""
    if not isinstance(message, dict):
        return None
    body = message.get("cmd") or message.get("batch")
    if not isinstance(body, dict):
        return None
    return bool(body.get("authenticated"))


def _majority(candidates):
    """
    The most common value among replicated atoms, ties broken by first seen.

    Replication exists so the gist survives loss; it also gives corruption
    detection for free, because a damaged copy is outvoted by its twins.
    """
    if len(candidates) == 1:
        return candidates[0]
    counts: dict = {}
    for value in candidates:
        counts[value] = counts.get(value, 0) + 1
    best = max(counts.values())
    for value in candidates:  # first-seen order among the winners
        if counts[value] == best:
            return value
    return candidates[0]


def _normalise_header(header: bytes) -> bytes:
    """The header with its own checksum slot zeroed, for CRC purposes."""
    return (
        header[:PAYLOAD_CRC_OFFSET]
        + b"\x00\x00\x00\x00"
        + header[PAYLOAD_CRC_OFFSET + 4 :]
    )


def integrity_crc(header: bytes, payload: bytes) -> int:
    """
    The end-to-end checksum: CRC-32 over the header and the payload together.

    Covering the payload alone is not enough. The header carries the fields
    that decide how the payload is interpreted (the gist type selects the
    decoder, K and the seed drive reassembly), so a corrupt header atom that
    happened to pass its own CRC-8 could turn an intact TEXT payload into a
    fabricated STATUS report while the payload checksum still matched. Binding
    the two together means any single corruption in either is detected.
    """
    return zlib.crc32(payload, zlib.crc32(_normalise_header(header)))


def _build_header(
    gist_bytes: bytes,
    gist_bits: int,
    K: int,
    fountain_seed: int,
    payload_len: int,
    payload: bytes = b"",
) -> bytes:
    """
    Assemble the 21-byte HEADER_GIST payload shared by every pack_* call.

    Layout: K(2) symbol_size(1) seed(4) payload_len(3) gist_bits(1)
    gist(5) integrity_crc32(4), little-endian throughout.
    """
    header = bytearray(21)
    header[0] = K & 0xFF
    header[1] = (K >> 8) & 0xFF
    header[2] = SYMBOL_SIZE & 0xFF
    header[3:7] = fountain_seed.to_bytes(4, "little")
    header[7] = payload_len & 0xFF
    header[8] = (payload_len >> 8) & 0xFF
    header[9] = (payload_len >> 16) & 0xFF
    header[10] = gist_bits & 0xFF
    header[11 : 11 + min(len(gist_bytes), GIST_ROOM)] = gist_bytes[:GIST_ROOM]
    crc = integrity_crc(bytes(header), payload)
    header[PAYLOAD_CRC_OFFSET : PAYLOAD_CRC_OFFSET + 4] = crc.to_bytes(4, "little")
    return bytes(header)


def _fountain_atom_payloads(blocks, fountain_seed: int, M: int):
    """Yield the 21-byte payloads for M fountain atoms."""
    for seed, degree, block in lt_encode_blocks(
        blocks, seed=fountain_seed, num_packets=M
    ):
        p = bytearray(21)
        p[0:4] = seed.to_bytes(4, "little")
        p[4] = degree & 0xFF
        p[5:21] = block[:16]
        yield bytes(p)


def chunk_blocks(payload: bytes, symbol_size: int):
    blocks = []
    for i in range(0, len(payload), symbol_size):
        chunk = payload[i : i + symbol_size]
        if len(chunk) < symbol_size:
            chunk = chunk + bytes(symbol_size - len(chunk))
        blocks.append(chunk)
    if not blocks:
        blocks.append(bytes(symbol_size))
    return blocks


def pack_message(
    msg: dict,
    message_id: int | None = None,
    extra_fountain: int = 0,
    min_redundancy: int = 10,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
) -> bytes:
    # Input validation
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dictionary")
    if "type" not in msg:
        raise ValueError("msg must contain 'type' field")
    if extra_fountain < 0:
        raise ValueError("extra_fountain must be non-negative")

    if message_id is None:
        message_id = int.from_bytes(os.urandom(2), "little") or 1

    return _pack_with_custom_payload(
        msg,
        encode_payload(msg),
        extra_fountain,
        message_id,
        min_redundancy,
        header_redundancy=header_redundancy,
        redundancy=redundancy,
    )


def pack_text_with_dict(
    words: list[str],
    text: str,
    extra_fountain: int = 0,
    message_id: int | None = None,
    min_redundancy: int = 10,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
) -> bytes:
    if message_id is None:
        message_id = int.from_bytes(os.urandom(2), "little") or 1

    msgmeta = {"type": "TEXT", "conf": 0.99}
    payload = encode_text(text)
    _check_payload_size(len(payload), min_redundancy, extra_fountain)
    gist_bytes, gist_bits = make_gist_bits(msgmeta)
    blocks = chunk_blocks(payload, SYMBOL_SIZE)
    K = len(blocks)
    fountain_seed = int.from_bytes(os.urandom(4), "little") or 1
    header = _build_header(
        gist_bytes, gist_bits, K, fountain_seed, len(payload), payload
    )

    # The header is replicated: if every copy is lost there is no gist and no
    # fountain parameters, so this is the floor on surviving heavy loss.
    # Adaptive redundancy floor lets links trade robustness vs throughput.
    M = fountain_atom_count(K, min_redundancy, redundancy, extra_fountain)
    n_header = _resolve_header_redundancy(header_redundancy, M)
    atoms = [(HEADER_GIST, header) for _ in range(n_header)]
    # DICT_UPDATE atoms travel right behind the header.
    for dp in make_dict_update_atoms(words):
        atoms.append((DICT_UPDATE, dp))
    for p in _fountain_atom_payloads(blocks, fountain_seed, M):
        atoms.append((FOUNTAIN_PACKET, p))

    return _emit_atoms(atoms, message_id)


def pack_text_message(
    text: str,
    extra_fountain: int = 0,
    message_id: int | None = None,
    min_redundancy: int = 10,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
) -> bytes:
    # Input validation
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if extra_fountain < 0:
        raise ValueError("extra_fountain must be non-negative")

    msg = {"type": "TEXT", "conf": 0.99}
    return _pack_with_custom_payload(
        msg,
        encode_text(text),
        extra_fountain,
        message_id,
        min_redundancy,
        header_redundancy=header_redundancy,
        redundancy=redundancy,
    )


def pack_cmd_message(
    cmd: dict,
    extra_fountain: int = 0,
    message_id: int | None = None,
    key: bytes | None = None,
    min_redundancy: int = 10,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
    counter: int = 0,
) -> bytes:
    payload = encode_cmd(cmd, key=key, counter=counter)
    msg = {"type": "CMD", "conf": 0.99}
    return _pack_with_custom_payload(
        msg,
        payload,
        extra_fountain,
        message_id,
        min_redundancy,
        header_redundancy=header_redundancy,
        redundancy=redundancy,
    )


def pack_voice_message(
    wav_path: str,
    extra_fountain: int = 0,
    message_id: int | None = None,
    min_redundancy: int = 10,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
) -> bytes:
    payload = encode_wav_to_bitstream(wav_path)
    msg = {"type": "VOICE", "conf": 0.9}
    return _pack_with_custom_payload(
        msg,
        payload,
        extra_fountain,
        message_id,
        min_redundancy,
        header_redundancy=header_redundancy,
        redundancy=redundancy,
    )


def pack_cmd_batch(
    batch: dict,
    extra_fountain: int = 0,
    message_id: int | None = None,
    key: bytes | None = None,
    min_redundancy: int = 10,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
    counter: int = 0,
) -> bytes:
    payload = encode_cmd_batch(batch, key=key, counter=counter)
    msg = {"type": "CMD_BATCH", "conf": 0.99}
    return _pack_with_custom_payload(
        msg,
        payload,
        extra_fountain,
        message_id,
        min_redundancy,
        header_redundancy=header_redundancy,
        redundancy=redundancy,
    )


def _pack_with_custom_payload(
    msgmeta: dict,
    payload: bytes,
    extra_fountain: int = 0,
    message_id: int | None = None,
    min_redundancy: int = 10,
    extra_atoms=None,
    header_redundancy=None,
    redundancy: float = 1.0,
) -> bytes:
    if message_id is None:
        message_id = int.from_bytes(os.urandom(2), "little") or 1
    if extra_fountain < 0:
        raise ValueError("extra_fountain must be non-negative")
    _check_payload_size(len(payload), min_redundancy, extra_fountain)

    gist_bytes, gist_bits = make_gist_bits(msgmeta)
    blocks = chunk_blocks(payload, SYMBOL_SIZE)
    K = len(blocks)
    fountain_seed = int.from_bytes(os.urandom(4), "little") or 1
    header = _build_header(
        gist_bytes, gist_bits, K, fountain_seed, len(payload), payload
    )

    M = fountain_atom_count(K, min_redundancy, redundancy, extra_fountain)
    n_header = _resolve_header_redundancy(header_redundancy, M)
    atoms = [(HEADER_GIST, header) for _ in range(n_header)]
    for atom_type, p in extra_atoms or []:
        # Metadata gists are replicated alongside the header; anything else
        # (a dictionary update, say) is sent once.
        copies = n_header if atom_type == MCKAY_GIST else 1
        atoms.extend([(atom_type, p)] * copies)
    for p in _fountain_atom_payloads(blocks, fountain_seed, M):
        atoms.append((FOUNTAIN_PACKET, p))

    return _emit_atoms(atoms, message_id)


def _emit_atoms(atoms, message_id: int) -> bytes:
    """Serialise ``(atom_type, payload21)`` pairs, numbering them 0..N-1."""
    total_atoms = len(atoms)
    if total_atoms > MAX_ATOMS:
        raise ValueError(
            f"message needs {total_atoms} atoms, exceeding the 16-bit limit "
            f"of {MAX_ATOMS}"
        )
    out = bytearray()
    for idx, (typ, payload21) in enumerate(atoms):
        out += make_atom(idx, total_atoms, message_id, typ, payload21)
    return bytes(out)


MCKAY_DATA_TYPES = {
    "AUTO": 0,
    "TEXT": 1,
    "TELEMETRY": 2,
    "VOICE": 3,
    "BINARY": 4,
    "IMAGE": 5,
}
_INV_MCKAY_DATA_TYPES = {v: k for k, v in MCKAY_DATA_TYPES.items()}


def _mckay_gist_atom(
    mckay_version: int,
    transform_id: int,
    data_type: str,
    original_size: int,
    compressed_size: int,
    channels: int,
    entropy_coder: int,
) -> bytes:
    """
    Build the 21-byte MCKAY_GIST payload.

    This is the atom that makes the gist-first claim real for compressed
    payloads: it is replicated like the header, so a receiver that recovers no
    fountain packets at all still learns what was sent, how big it was and how
    hard it was squeezed.
    """
    p = bytearray(21)
    p[0] = mckay_version & 0xFF
    p[1] = transform_id & 0xFF
    p[2] = MCKAY_DATA_TYPES.get(data_type, 0) & 0xFF
    p[3:7] = (original_size & 0xFFFFFFFF).to_bytes(4, "little")
    p[7:11] = (compressed_size & 0xFFFFFFFF).to_bytes(4, "little")
    p[11] = channels & 0xFF
    p[12] = entropy_coder & 0xFF
    return bytes(p)


def _parse_mckay_gist(p: bytes) -> dict:
    original_size = int.from_bytes(p[3:7], "little")
    compressed_size = int.from_bytes(p[7:11], "little")
    return {
        "mckay_version": p[0],
        "transform_id": p[1],
        "data_type": _INV_MCKAY_DATA_TYPES.get(p[2], f"TYPE_{p[2]}"),
        "original_size": original_size,
        "compressed_size": compressed_size,
        "channels": p[11],
        "entropy_coder": p[12],
        "ratio": round(original_size / compressed_size, 3) if compressed_size else 0.0,
    }


def pack_mckay_message(
    data: bytes,
    data_type: str = "AUTO",
    message_id: int | None = None,
    extra_fountain: int = 0,
    min_redundancy: int = 10,
    voice_bps: int = 1200,
    channels: int = 0,
    header_redundancy: int | None = None,
    redundancy: float = 1.0,
) -> bytes:
    """
    Compress with McKay, then send the result as gist-first atomized packets.

    This is the full McKay + ASTRAL path: domain-aware compression, a
    replicated metadata gist, and a fountain-coded body, so a lossy link
    yields the gist first and the exact payload once enough atoms arrive.

    Parameters
    ----------
    data : bytes
        Payload to compress and transmit.
    data_type : str
        ``AUTO`` (detect), ``TEXT``, ``TELEMETRY``, ``VOICE``, ``BINARY`` or
        ``IMAGE``.
    message_id, extra_fountain, min_redundancy
        As for :func:`pack_message`.
    voice_bps, channels
        Passed through to the McKay compressor.

    Returns
    -------
    bytes
        An ASTRAL atom stream.
    """
    from . import mckay_astral_integration as mckay

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if data_type not in MCKAY_DATA_TYPES:
        raise ValueError(
            f"data_type must be one of {sorted(MCKAY_DATA_TYPES)}, got {data_type!r}"
        )

    compressed = mckay.compress(
        data, data_type=data_type, voice_bps=voice_bps, channels=channels
    )
    version, transform_id, orig_len, ch, entropy_coder, _payload = mckay._parse_header(
        compressed
    )
    resolved_type = data_type
    if resolved_type == "AUTO":
        resolved_type = mckay._detect_type(data) if data else "BINARY"

    gist_atom = _mckay_gist_atom(
        version,
        transform_id,
        resolved_type,
        orig_len,
        len(compressed),
        ch,
        entropy_coder,
    )
    msgmeta = {"type": "MCKAY", "conf": 0.99}
    return _pack_with_custom_payload(
        msgmeta,
        compressed,
        extra_fountain,
        message_id,
        min_redundancy,
        extra_atoms=[(MCKAY_GIST, gist_atom)],
        header_redundancy=header_redundancy,
        redundancy=redundancy,
    )


def unpack_mckay_stream(stream: bytes) -> dict:
    """
    Decode a stream produced by :func:`pack_mckay_message`.

    Returns the same keys as :func:`unpack_stream` plus ``mckay`` (the
    metadata gist, present whenever any MCKAY_GIST atom survived) and
    ``data`` (the decompressed bytes, present only on full recovery).
    """
    result = unpack_stream(stream)
    if "error" in result:
        return result
    if result.get("mckay") is None:
        result["error"] = "not a McKay stream: no MCKAY_GIST atom recovered"
    return result


def unpack_stream(stream: bytes, key: bytes | None = None, replay_guard=None):
    """
    Decode an ASTRAL stream.

    ``key`` and ``replay_guard`` apply to CMD and CMD_BATCH messages. With a
    key, a command that fails authentication or freshness is reported as an
    error instead of being returned. Without one, commands are still decoded
    for inspection but are tagged ``authenticated: False``: never act on such
    a command.
    """
    # Input validation
    if not isinstance(stream, bytes):
        raise ValueError("stream must be bytes")
    if len(stream) == 0:
        return {"error": "empty stream"}

    atoms = parse_atoms(stream)
    if not atoms:
        return {"error": "no valid atoms"}

    msg_id = atoms[0].message_id
    header_candidates = []
    mckay_candidates = []
    fountain_atoms = []
    dict_atoms = []
    total_atoms = atoms[0].total_atoms
    atom_version = atoms[0].version
    for _idx, total, mid, typ, payload, version in atoms:
        if mid != msg_id:
            continue
        total_atoms = total
        if typ == HEADER_GIST:
            atom_version = version
            header_candidates.append(payload)
        elif typ == FOUNTAIN_PACKET:
            fountain_atoms.append(payload)
        elif typ == DICT_UPDATE:
            dict_atoms.append(payload)
        elif typ == MCKAY_GIST:
            mckay_candidates.append(payload)

    if not header_candidates:
        return {"error": "missing header/gist atom"}

    # The header is replicated, so take the copy the majority agree on rather
    # than whichever arrived first: one corrupt copy that slipped past its
    # CRC-8 then loses the vote instead of deciding how the payload is read.
    header_atom = _majority(header_candidates)
    mckay_atom = _majority(mckay_candidates) if mckay_candidates else None

    K = header_atom[0] | (header_atom[1] << 8)
    symbol_size = header_atom[2]
    payload_len = header_atom[7] | (header_atom[8] << 8) | (header_atom[9] << 16)
    gist_bits = header_atom[10]
    # Calculate how many bytes the gist bits actually occupy
    gist_bytes_needed = (gist_bits + 7) // 8
    gist_bytes = header_atom[11 : 11 + min(gist_bytes_needed, GIST_ROOM)]

    gist = parse_gist(gist_bytes, gist_bits)

    # Atom format 2 onward carries a CRC-32 of the assembled payload, so a
    # corrupt atom that slipped past its own CRC-8 cannot be reported as a
    # clean decode.
    expected_crc = None
    if atom_version >= 2:
        expected_crc = int.from_bytes(
            header_atom[PAYLOAD_CRC_OFFSET : PAYLOAD_CRC_OFFSET + 4], "little"
        )

    packets = []
    for p in fountain_atoms:
        seed = int.from_bytes(p[0:4], "little")
        degree = p[4]
        block = bytes(p[5:21])
        packets.append((seed, degree, block))

    complete = False
    recovered_fraction = 0.0
    message = None
    mckay_gist = _parse_mckay_gist(mckay_atom) if mckay_atom is not None else None
    decompressed = None
    integrity = None

    if packets:
        recovered, frac = lt_decode_blocks(packets, K, symbol_size)
        recovered_fraction = frac
        if recovered is not None:
            payload = b"".join(recovered)[:payload_len]
            if expected_crc is not None:
                integrity = integrity_crc(header_atom, payload) == expected_crc
                if not integrity:
                    # The blocks solved, but they do not reconstruct the payload
                    # that was sent. Reporting this as a decode would hand the
                    # caller silently wrong data.
                    return {
                        "message_id": msg_id,
                        "total_atoms": total_atoms,
                        "received_atoms": len(atoms),
                        "gist": gist,
                        "mckay": mckay_gist,
                        "complete": False,
                        "recovered_fraction": recovered_fraction,
                        "message": None,
                        "data": None,
                        "integrity_ok": False,
                        "error": (
                            "integrity check failed: the atoms reassembled "
                            "but the CRC-32 over the header and payload does "
                            "not match. At least one atom was corrupt."
                        ),
                    }
            try:
                mtype = gist.get("type")
                if dict_atoms:
                    extra_words = split_words_from_atoms(dict_atoms)
                else:
                    extra_words = []
                if mckay_gist is not None:
                    from . import mckay_astral_integration as mckay

                    decompressed = mckay.decompress(payload)
                    message = {
                        "type": "MCKAY",
                        "data_type": mckay_gist["data_type"],
                        "bytes": decompressed,
                    }
                elif mtype == "TEXT":
                    if extra_words:
                        message = {
                            "type": "TEXT",
                            "text": decode_text(payload),
                            "extra_words": extra_words,
                        }
                    else:
                        message = {"type": "TEXT", "text": decode_text(payload)}
                elif mtype == "VOICE":
                    message = {"type": "VOICE", "bytes": payload}
                elif mtype == "CMD":
                    message = {
                        "type": "CMD",
                        "cmd": decode_cmd(
                            payload,
                            key=key,
                            require_auth=key is not None,
                            replay_guard=replay_guard,
                        ),
                    }
                elif mtype == "CMD_BATCH":
                    message = {
                        "type": "CMD_BATCH",
                        "batch": decode_cmd_batch(
                            payload,
                            key=key,
                            require_auth=key is not None,
                            replay_guard=replay_guard,
                        ),
                    }
                else:
                    message = decode_payload(payload)
                complete = True
            except CommandAuthError as exc:
                # A command that cannot be authenticated must not look like a
                # decode that merely failed for lack of atoms.
                return {
                    "message_id": msg_id,
                    "total_atoms": total_atoms,
                    "received_atoms": len(atoms),
                    "gist": gist,
                    "mckay": mckay_gist,
                    "complete": False,
                    "recovered_fraction": recovered_fraction,
                    "message": None,
                    "data": None,
                    "integrity_ok": integrity,
                    "command_authenticated": False,
                    "error": f"command authentication failed: {exc}",
                }
            except Exception:
                complete = False
                message = None

    return {
        "message_id": msg_id,
        "total_atoms": total_atoms,
        "received_atoms": len(atoms),
        "gist": gist,
        "mckay": mckay_gist,
        "complete": complete,
        "recovered_fraction": recovered_fraction,
        "message": message,
        "data": decompressed,
        "integrity_ok": integrity,
        "command_authenticated": _command_auth_state(message),
    }


def pack_message_sp(
    msg: dict,
    counter,
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
    from .spacepacket import SpacePacketSequenceCounter, wrap as _sp_wrap  # noqa: F401

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
    from .rs_fec import encode_stream as _rs_encode

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
    from .rs_fec import decode_stream as _rs_decode

    try:
        astral_stream, n_corrected, n_uncorrectable = _rs_decode(rs_stream, e=e)
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
    from .tmframe import encode_frames as _tm_encode

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
    from .tmframe import encode_frames as _tm_encode  # noqa: F401
    from .tmframe import decode_frames as _tm_decode

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
