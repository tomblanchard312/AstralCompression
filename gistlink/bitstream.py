class BitWriter:
    def __init__(self):
        self.buf = 0
        self.nbits = 0
        self.out = bytearray()

    def write_bits(self, value: int, n: int):
        if n == 0:
            return
        if not isinstance(value, int):
            raise TypeError("value must be an integer")
        if not isinstance(n, int) or n < 0:
            raise TypeError("n must be a non-negative integer")
        if value < 0 or value >= (1 << n):
            raise ValueError(f"value {value} out of range for {n} bits")
        self.buf |= (value & ((1 << n) - 1)) << self.nbits
        self.nbits += n
        while self.nbits >= 8:
            self.out.append(self.buf & 0xFF)
            self.buf >>= 8
            self.nbits -= 8

    def write_bytes(self, b: bytes):
        # Input validation
        if not isinstance(b, bytes):
            raise TypeError("b must be bytes")

        self.align_byte()
        self.out.extend(b)

    def align_byte(self):
        if self.nbits > 0:
            self.out.append(self.buf & 0xFF)
        self.buf = 0
        self.nbits = 0

    def getvalue(self) -> bytes:
        try:
            self.align_byte()
            return bytes(self.out)
        except Exception as e:
            raise RuntimeError(f"Failed to get bitstream value: {e}")


class BitReader:
    def __init__(self, data: bytes):
        # Input validation
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        self.data = data
        self.pos = 0
        self.buf = 0
        self.nbits = 0

    def read_bits(self, n: int) -> int:
        # Input validation
        if not isinstance(n, int) or n < 0:
            raise TypeError("n must be a non-negative integer")

        while self.nbits < n:
            if self.pos >= len(self.data):
                raise EOFError("not enough bits")
            self.buf |= self.data[self.pos] << self.nbits
            self.pos += 1
            self.nbits += 8
        val = self.buf & ((1 << n) - 1)
        self.buf >>= n
        self.nbits -= n
        return val

    def read_bytes(self, n: int) -> bytes:
        # Input validation
        if not isinstance(n, int) or n < 0:
            raise TypeError("n must be a non-negative integer")

        self.align_byte()
        if self.pos + n > len(self.data):
            raise EOFError("not enough bytes")
        b = self.data[self.pos : self.pos + n]
        self.pos += n
        return b

    def align_byte(self):
        if self.nbits > 0:
            self.pos += 1  # only advance if there are unconsumed buffered bits
        self.buf = 0
        self.nbits = 0
