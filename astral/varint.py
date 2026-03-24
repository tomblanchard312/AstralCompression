def zigzag_encode(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def zigzag_decode(z: int) -> int:
    return (z >> 1) ^ -(z & 1)


def leb128_encode(u: int) -> bytes:
    # Input validation
    if not isinstance(u, int):
        raise TypeError("u must be an integer")
    if u < 0:
        raise ValueError("LEB128 expects unsigned")
    out = bytearray()
    while True:
        b = u & 0x7F
        u >>= 7
        if u:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def leb128_decode(data: bytes, start: int = 0):
    # Input validation
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not isinstance(start, int) or start < 0:
        raise TypeError("start must be a non-negative integer")
    if start >= len(data):
        raise ValueError("start position beyond data length")

    shift = 0
    val = 0
    i = start
    while True:
        if i >= len(data):
            raise EOFError("LEB128 decode overflow")
        b = data[i]
        i += 1
        val |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 63:
            raise ValueError("LEB128 too large")
    return val, i
