# Minimal CRC helpers (no deps)

# CRC-8 J1850: poly=0x1D, init=0xFF, xorout=0xFF (common variant)
def crc8_j1850(data: bytes) -> int:
    # Input validation
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    
    poly = 0x1D
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0xFF


# CRC-16-CCITT (False): poly=0x1021, init=0xFFFF, xorout=0x0000
def crc16_ccitt(data: bytes) -> int:
    # Input validation
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF
