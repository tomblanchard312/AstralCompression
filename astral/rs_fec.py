"""
Reed-Solomon forward error correction.

Two distinct things live here, and they are not interchangeable:

CCSDS channel coding (``encode_codeblock`` / ``decode_codeblock``)
    RS(255,223) and RS(255,239) over GF(2^8) with the CCSDS 131.0-B-5
    generator (fcr=112, primitive polynomial 0x187, conventional basis) and
    symbol interleaving. This is the code a standard ground station applies to
    TM Transfer Frames: interleave depth 5 over a 1115-byte frame yields the
    usual 1275-byte codeblock.

ASTRAL atom protection (``encode_stream`` / ``decode_stream``)
    A shorter RS code applied per 32-byte atom, so that a corrupted atom can be
    repaired instead of being discarded by its CRC. These are RS(48,32) and
    RS(64,32): the same field and generator, but not a CCSDS codeblock.

Requires the optional ``reedsolo`` dependency: ``pip install
astral-compression[rs]``.
"""

from __future__ import annotations

try:
    import reedsolo
except ImportError as _exc:  # pragma: no cover - exercised by the extras test
    raise ImportError(
        "Reed-Solomon support requires the 'reedsolo' package. "
        "Install it with: pip install astral-compression[rs]",
        name="reedsolo",
    ) from _exc

ATOM_SIZE = 32
PARITY_E16 = 32
PARITY_E8 = 16
CODEWORD_SIZE = {
    16: ATOM_SIZE + PARITY_E16,
    8: ATOM_SIZE + PARITY_E8,
}

# CCSDS 131.0-B-5 channel coding parameters.
CCSDS_N = 255
CCSDS_K = {223: 32, 239: 16}  # message length -> parity symbols
CCSDS_DEFAULT_INTERLEAVE = 5

_RS_PARAMS = dict(fcr=112, prim=0x187, generator=2, c_exp=8)

_RS_E16 = reedsolo.RSCodec(nsym=32, **_RS_PARAMS)
_RS_E8 = reedsolo.RSCodec(nsym=16, **_RS_PARAMS)
_RS_CCSDS = {k: reedsolo.RSCodec(nsym=nsym, **_RS_PARAMS) for k, nsym in CCSDS_K.items()}


def encode_stream(atom_stream: bytes, e: int = 16) -> bytes:
    """
    Protect each 32-byte ASTRAL atom with its own RS codeword.

    ``e=16`` gives RS(64,32) and corrects up to 16 corrupt bytes per atom;
    ``e=8`` gives RS(48,32) and corrects up to 8.
    """
    if not isinstance(atom_stream, bytes):
        raise TypeError("atom_stream must be bytes")
    if len(atom_stream) % ATOM_SIZE != 0:
        raise ValueError(
            f"atom_stream length must be a multiple of {ATOM_SIZE}, "
            f"got {len(atom_stream)}"
        )
    if e not in (8, 16):
        raise ValueError("e must be 8 or 16")
    if len(atom_stream) == 0:
        return b""

    codec = _RS_E16 if e == 16 else _RS_E8
    out = bytearray()
    for i in range(0, len(atom_stream), ATOM_SIZE):
        out += codec.encode(atom_stream[i : i + ATOM_SIZE])
    return bytes(out)


def decode_stream(rs_stream: bytes, e: int = 16) -> tuple[bytes, int, int]:
    """
    Decode atom codewords, correcting what is correctable.

    Returns ``(atom_stream, n_corrected_symbols, n_uncorrectable_atoms)``.
    Uncorrectable atoms are dropped; the fountain layer treats them as
    erasures.
    """
    if not isinstance(rs_stream, bytes):
        raise TypeError("rs_stream must be bytes")
    if e not in (8, 16):
        raise ValueError("e must be 8 or 16")
    if len(rs_stream) == 0:
        return b"", 0, 0

    codec = _RS_E16 if e == 16 else _RS_E8
    cw_size = CODEWORD_SIZE[e]
    out = bytearray()
    n_corrected = 0
    n_uncorrectable = 0

    for i in range(0, len(rs_stream), cw_size):
        cw = rs_stream[i : i + cw_size]
        if len(cw) < cw_size:
            break
        try:
            data, _, errata = codec.decode(cw)
            out += data
            if errata:
                n_corrected += len(errata)
        except reedsolo.ReedSolomonError:
            n_uncorrectable += 1

    return bytes(out), n_corrected, n_uncorrectable


def codeword_size(e: int = 16) -> int:
    if e not in (8, 16):
        raise ValueError("e must be 8 or 16")
    return CODEWORD_SIZE[e]


def _check_ccsds_params(k: int, interleave: int) -> None:
    if k not in CCSDS_K:
        raise ValueError(f"k must be one of {sorted(CCSDS_K)}, got {k}")
    if not isinstance(interleave, int) or interleave < 1 or interleave > 8:
        raise ValueError("interleave depth must be 1..8 (CCSDS allows 1,2,3,4,5,8)")


def codeblock_sizes(k: int = 223, interleave: int = CCSDS_DEFAULT_INTERLEAVE):
    """Return ``(data_size, codeblock_size)`` for a CCSDS RS codeblock."""
    _check_ccsds_params(k, interleave)
    return k * interleave, CCSDS_N * interleave


def encode_codeblock(
    data: bytes, k: int = 223, interleave: int = CCSDS_DEFAULT_INTERLEAVE
) -> bytes:
    """
    Encode one CCSDS RS codeblock.

    ``data`` must be exactly ``k * interleave`` bytes. Symbols are interleaved
    to depth ``interleave``, matching CCSDS 131.0-B-5: the j-th codeword takes
    every ``interleave``-th byte starting at j, and the resulting codewords are
    re-interleaved on output so that a burst error is spread across codewords.
    """
    _check_ccsds_params(k, interleave)
    data_size, block_size = codeblock_sizes(k, interleave)
    if len(data) != data_size:
        raise ValueError(
            f"data must be exactly {data_size} bytes for RS({CCSDS_N},{k}) "
            f"interleave {interleave}, got {len(data)}"
        )

    codec = _RS_CCSDS[k]
    codewords = []
    for j in range(interleave):
        message = data[j::interleave]
        codewords.append(bytes(codec.encode(message)))

    out = bytearray(block_size)
    for j, cw in enumerate(codewords):
        out[j::interleave] = cw
    return bytes(out)


def decode_codeblock(
    block: bytes, k: int = 223, interleave: int = CCSDS_DEFAULT_INTERLEAVE
) -> tuple[bytes, int, bool]:
    """
    Decode one CCSDS RS codeblock.

    Returns ``(data, n_corrected_symbols, ok)``. When a constituent codeword is
    beyond correction, ``ok`` is False and the data returned for that codeword
    is its received (still corrupt) message part, so callers can decide whether
    to use it or drop the block.
    """
    _check_ccsds_params(k, interleave)
    _data_size, block_size = codeblock_sizes(k, interleave)
    if len(block) != block_size:
        raise ValueError(
            f"codeblock must be exactly {block_size} bytes, got {len(block)}"
        )

    codec = _RS_CCSDS[k]
    messages = []
    n_corrected = 0
    ok = True
    for j in range(interleave):
        cw = block[j::interleave]
        try:
            msg, _, errata = codec.decode(cw)
            messages.append(bytes(msg))
            n_corrected += len(errata) if errata else 0
        except reedsolo.ReedSolomonError:
            ok = False
            messages.append(bytes(cw[:k]))

    out = bytearray(k * interleave)
    for j, msg in enumerate(messages):
        out[j::interleave] = msg
    return bytes(out), n_corrected, ok


def encode_codeblocks(
    data: bytes, k: int = 223, interleave: int = CCSDS_DEFAULT_INTERLEAVE
) -> bytes:
    """
    Encode a byte stream as a sequence of CCSDS RS codeblocks.

    The final block is zero-padded to the block data size. Use
    ``decode_codeblocks(..., original_length=len(data))`` to trim it back.
    """
    data_size, _ = codeblock_sizes(k, interleave)
    out = bytearray()
    for i in range(0, len(data), data_size):
        chunk = data[i : i + data_size]
        if len(chunk) < data_size:
            chunk = chunk + bytes(data_size - len(chunk))
        out += encode_codeblock(chunk, k, interleave)
    return bytes(out)


def decode_codeblocks(
    stream: bytes,
    k: int = 223,
    interleave: int = CCSDS_DEFAULT_INTERLEAVE,
    original_length: int | None = None,
) -> tuple[bytes, dict]:
    """
    Decode a sequence of CCSDS RS codeblocks.

    Returns ``(data, stats)`` where stats reports blocks seen, symbols
    corrected, and blocks that exceeded the correction capability.
    """
    _data_size, block_size = codeblock_sizes(k, interleave)
    out = bytearray()
    n_blocks = 0
    n_corrected = 0
    n_uncorrectable = 0

    for i in range(0, len(stream) - block_size + 1, block_size):
        data, corrected, ok = decode_codeblock(stream[i : i + block_size], k, interleave)
        out += data
        n_blocks += 1
        n_corrected += corrected
        if not ok:
            n_uncorrectable += 1

    if original_length is not None:
        out = out[:original_length]

    return bytes(out), {
        "n_blocks": n_blocks,
        "n_corrected_symbols": n_corrected,
        "n_uncorrectable_blocks": n_uncorrectable,
    }
