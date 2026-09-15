"""
Reed-Solomon forward error correction.

Two distinct things live here, and they are not interchangeable:

CCSDS channel coding (``encode_codeblock`` / ``decode_codeblock``)
    RS(255,223) and RS(255,239) over GF(2^8) with the CCSDS 131.0-B generator:
    field polynomial 0x187, first consecutive root 112, and primitive element
    alpha^11, so the generator polynomial roots are alpha^(11*(112+i)). These
    match Phil Karn's libfec (FCR=112, PRIM=11), which is what gr-satellites
    and most ground stations use, and were verified against an independent
    construction of the generator polynomial.

    Symbols are carried in the **dual basis** by default, as the standard
    specifies, using Berlekamp's transform; pass ``basis="conventional"`` for
    the libfec ``encode_rs_8`` representation instead. Getting this wrong is
    the classic CCSDS RS interop failure: the maths is identical but the byte
    values on the wire are not.

    Interleave depth 5 over a 1115-byte frame yields the usual 1275-byte
    codeblock.

GistLink atom protection (``encode_stream`` / ``decode_stream``)
    A shorter RS code applied per 32-byte atom, so that a corrupted atom can be
    repaired instead of being discarded by its CRC. These are RS(48,32) and
    RS(64,32): the same field and generator, but not a CCSDS codeblock.

Requires the optional ``reedsolo`` dependency: ``pip install
gistlink[rs]``.
"""

from __future__ import annotations

try:
    import reedsolo
except ImportError as _exc:  # pragma: no cover - exercised by the extras test
    raise ImportError(
        "Reed-Solomon support requires the 'reedsolo' package. "
        "Install it with: pip install gistlink[rs]",
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

# CCSDS 131.0-B: field polynomial 0x187, FCR 112, primitive element alpha^11.
# reedsolo takes the primitive element itself, so pass alpha^11 = 173 rather
# than alpha = 2. With generator=2 the roots are alpha^112..alpha^143, which is
# a valid RS code but NOT the CCSDS one and will not decode at a ground station.
CCSDS_FIELD_POLY = 0x187
CCSDS_FCR = 112
CCSDS_PRIM_EXP = 11
CCSDS_GENERATOR = 173  # alpha^11 in GF(2^8) mod 0x187

_RS_PARAMS = dict(
    fcr=CCSDS_FCR, prim=CCSDS_FIELD_POLY, generator=CCSDS_GENERATOR, c_exp=8
)

BASIS_DUAL = "dual"
BASIS_CONVENTIONAL = "conventional"

# Berlekamp dual-basis transform (CCSDS 131.0-B Annex; seed from Phil Karn's
# gen_ccsds_tal.c). Taltab maps conventional -> dual, Tal1tab is its inverse.
_TAL = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)


def _build_tal_tables():
    taltab = [0] * 256
    tal1tab = [0] * 256
    for i in range(256):
        acc = 0
        for k in range(8):
            if i & (1 << k):
                acc ^= _TAL[7 - k]
        taltab[i] = acc
        tal1tab[acc] = i
    return bytes(taltab), bytes(tal1tab)


_TALTAB, _TAL1TAB = _build_tal_tables()


def to_dual_basis(data: bytes) -> bytes:
    """Conventional representation -> dual basis (CCSDS wire order)."""
    return bytes(_TALTAB[b] for b in data)


def from_dual_basis(data: bytes) -> bytes:
    """Dual basis (CCSDS wire order) -> conventional representation."""
    return bytes(_TAL1TAB[b] for b in data)


_RS_E16 = reedsolo.RSCodec(nsym=32, **_RS_PARAMS)
_RS_E8 = reedsolo.RSCodec(nsym=16, **_RS_PARAMS)
_RS_CCSDS = {k: reedsolo.RSCodec(nsym=nsym, **_RS_PARAMS) for k, nsym in CCSDS_K.items()}


def encode_stream(atom_stream: bytes, e: int = 16) -> bytes:
    """
    Protect each 32-byte GistLink atom with its own RS codeword.

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


def _check_ccsds_params(k: int, interleave: int, basis: str = BASIS_DUAL) -> None:
    if k not in CCSDS_K:
        raise ValueError(f"k must be one of {sorted(CCSDS_K)}, got {k}")
    if not isinstance(interleave, int) or interleave < 1 or interleave > 8:
        raise ValueError("interleave depth must be 1..8 (CCSDS allows 1,2,3,4,5,8)")
    if basis not in (BASIS_DUAL, BASIS_CONVENTIONAL):
        raise ValueError(
            f"basis must be {BASIS_DUAL!r} or {BASIS_CONVENTIONAL!r}, got {basis!r}"
        )


def codeblock_sizes(k: int = 223, interleave: int = CCSDS_DEFAULT_INTERLEAVE):
    """Return ``(data_size, codeblock_size)`` for a CCSDS RS codeblock."""
    _check_ccsds_params(k, interleave)
    return k * interleave, CCSDS_N * interleave


def encode_codeblock(
    data: bytes,
    k: int = 223,
    interleave: int = CCSDS_DEFAULT_INTERLEAVE,
    basis: str = BASIS_DUAL,
) -> bytes:
    """
    Encode one CCSDS RS codeblock.

    ``data`` must be exactly ``k * interleave`` bytes, in the representation
    named by ``basis``. Symbols are interleaved to depth ``interleave``: the
    j-th codeword takes every ``interleave``-th byte starting at j, and the
    codewords are re-interleaved on output so a burst is spread across them.

    In the default dual basis this mirrors libfec's ``encode_rs_ccsds``: the
    data is mapped to the conventional representation, encoded, and the parity
    mapped back, leaving the data bytes untouched on the wire.
    """
    _check_ccsds_params(k, interleave, basis)
    data_size, block_size = codeblock_sizes(k, interleave)
    if len(data) != data_size:
        raise ValueError(
            f"data must be exactly {data_size} bytes for RS({CCSDS_N},{k}) "
            f"interleave {interleave}, got {len(data)}"
        )

    codec = _RS_CCSDS[k]
    n_parity = CCSDS_K[k]
    codewords = []
    for j in range(interleave):
        message = data[j::interleave]
        if basis == BASIS_DUAL:
            conventional = from_dual_basis(message)
            parity = bytes(codec.encode(conventional))[-n_parity:]
            codewords.append(message + to_dual_basis(parity))
        else:
            codewords.append(bytes(codec.encode(message)))

    out = bytearray(block_size)
    for j, cw in enumerate(codewords):
        out[j::interleave] = cw
    return bytes(out)


def decode_codeblock(
    block: bytes,
    k: int = 223,
    interleave: int = CCSDS_DEFAULT_INTERLEAVE,
    basis: str = BASIS_DUAL,
) -> tuple[bytes, int, bool]:
    """
    Decode one CCSDS RS codeblock.

    Returns ``(data, n_corrected_symbols, ok)``. When a constituent codeword is
    beyond correction, ``ok`` is False and the data returned for that codeword
    is its received (still corrupt) message part, so callers can decide whether
    to use it or drop the block.
    """
    _check_ccsds_params(k, interleave, basis)
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
        if basis == BASIS_DUAL:
            cw = from_dual_basis(cw)
        try:
            msg, _, errata = codec.decode(cw)
            msg = bytes(msg)
            if basis == BASIS_DUAL:
                msg = to_dual_basis(msg)
            messages.append(msg)
            n_corrected += len(errata) if errata else 0
        except reedsolo.ReedSolomonError:
            ok = False
            recovered = bytes(cw[:k])
            if basis == BASIS_DUAL:
                recovered = to_dual_basis(recovered)
            messages.append(recovered)

    out = bytearray(k * interleave)
    for j, msg in enumerate(messages):
        out[j::interleave] = msg
    return bytes(out), n_corrected, ok


def encode_codeblocks(
    data: bytes,
    k: int = 223,
    interleave: int = CCSDS_DEFAULT_INTERLEAVE,
    basis: str = BASIS_DUAL,
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
        out += encode_codeblock(chunk, k, interleave, basis)
    return bytes(out)


def decode_codeblocks(
    stream: bytes,
    k: int = 223,
    interleave: int = CCSDS_DEFAULT_INTERLEAVE,
    original_length: int | None = None,
    basis: str = BASIS_DUAL,
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
        data, corrected, ok = decode_codeblock(
            stream[i : i + block_size], k, interleave, basis
        )
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
