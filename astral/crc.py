# Minimal CRC helpers (no deps).
#
# Both are table-driven. A 32-byte atom is CRC'd on every send and on every
# candidate offset while the receiver hunts for the sync word, so the
# bit-at-a-time version showed up as the dominant cost of packing and parsing
# a large message. The tables are built once at import from the same
# definitions and produce identical values.


def _build_crc8_table(poly: int) -> tuple:
    table = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return tuple(table)


def _build_crc16_table(poly: int) -> tuple:
    table = []
    for b in range(256):
        crc = b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return tuple(table)


_CRC8_J1850_TABLE = _build_crc8_table(0x1D)
_CRC16_CCITT_TABLE = _build_crc16_table(0x1021)


# CRC-8 J1850: poly=0x1D, init=0xFF, xorout=0xFF (common variant)
def crc8_j1850(data: bytes) -> int:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes")

    table = _CRC8_J1850_TABLE
    crc = 0xFF
    for b in data:
        crc = table[crc ^ b]
    return crc ^ 0xFF


# CRC-16-CCITT (False): poly=0x1021, init=0xFFFF, xorout=0x0000
def crc16_ccitt(data: bytes) -> int:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes")

    table = _CRC16_CCITT_TABLE
    crc = 0xFFFF
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ b]
    return crc
