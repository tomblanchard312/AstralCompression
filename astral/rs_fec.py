import reedsolo

ATOM_SIZE = 32
PARITY_E16 = 32
PARITY_E8 = 16
CODEWORD_SIZE = {
    16: ATOM_SIZE + PARITY_E16,
    8: ATOM_SIZE + PARITY_E8,
}

_RS_E16 = reedsolo.RSCodec(nsym=32, fcr=112, prim=0x187, generator=2, c_exp=8)
_RS_E8 = reedsolo.RSCodec(nsym=16, fcr=112, prim=0x187, generator=2, c_exp=8)


def encode_stream(atom_stream: bytes, e: int = 16) -> bytes:
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
