# Fixed bit manipulation functions
class BitWriter:
    def __init__(self):
        self.data = bytearray()
        self.bit_buffer = 0
        self.bit_count = 0

    def write_bits(self, value: int, num_bits: int):
        if num_bits <= 0:
            return

        # Mask to ensure we only use the specified number of bits
        mask = (1 << num_bits) - 1
        value &= mask

        # Add bits to buffer
        self.bit_buffer |= value << self.bit_count
        self.bit_count += num_bits

        # Flush complete bytes
        while self.bit_count >= 8:
            self.data.append(self.bit_buffer & 0xFF)
            self.bit_buffer >>= 8
            self.bit_count -= 8

    def write_bytes(self, data: bytes):
        # Flush any pending bits
        if self.bit_count > 0:
            self.data.append(self.bit_buffer & 0xFF)
            self.bit_buffer = 0
            self.bit_count = 0

        self.data.extend(data)

    def getvalue(self) -> bytes:
        # Flush any remaining bits
        if self.bit_count > 0:
            self.data.append(self.bit_buffer & 0xFF)
        return bytes(self.data)


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.byte_pos = 0
        self.bit_buffer = 0
        self.bits_available = 0

    def read_bits(self, num_bits: int) -> int:
        if num_bits <= 0:
            return 0

        result = 0
        bits_read = 0

        while bits_read < num_bits:
            # Load more bits if needed
            if self.bits_available == 0:
                if self.byte_pos >= len(self.data):
                    raise EOFError("Not enough data to read bits")
                self.bit_buffer = self.data[self.byte_pos]
                self.byte_pos += 1
                self.bits_available = 8

            # Read bits from buffer
            bits_to_read = min(num_bits - bits_read, self.bits_available)
            mask = (1 << bits_to_read) - 1

            result |= (self.bit_buffer & mask) << bits_read

            self.bit_buffer >>= bits_to_read
            self.bits_available -= bits_to_read
            bits_read += bits_to_read

        return result


# Fixed varint implementation
def leb128_encode(u: int) -> bytes:
    if not isinstance(u, int):
        raise TypeError("u must be an integer")
    if u < 0:
        raise ValueError("LEB128 expects unsigned")

    if u == 0:
        return bytes([0])

    out = bytearray()
    while u > 0:
        b = u & 0x7F
        u >>= 7
        if u > 0:
            out.append(b | 0x80)
        else:
            out.append(b)
    return bytes(out)


def leb128_decode(data: bytes, start: int = 0):
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not isinstance(start, int) or start < 0:
        raise TypeError("start must be a non-negative integer")
    if start >= len(data):
        raise ValueError("start position beyond data length")

    shift = 0
    val = 0
    i = start

    while i < len(data):
        b = data[i]
        i += 1
        val |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 63:
            raise ValueError("LEB128 too large")
    else:
        raise EOFError("LEB128 decode overflow")

    return val, i


# Fixed dictionary mappings
DICT_SUBJECT = {
    "KESTREL-1": 1,
    "KESTREL-2": 2,
    "ORION-A": 3,
}
DICT_OBJECT = {
    "H2O_ICE": 1,
    "CH4_ICE": 2,
    "BASALT": 3,
    "UNKNOWN": 4,
}
DICT_TYPE = {
    "DETECT": 1,
    "STATUS": 2,
    "TEXT": 3,
    "VOICE": 4,
    "CMD": 5,
    "CMD_BATCH": 6,
}


def q_lat(value: float) -> int:
    if not isinstance(value, (int, float)):
        raise TypeError("value must be a number")

    # Clamp to valid latitude range first
    value = max(-90.0, min(90.0, float(value)))
    v = int(round(value * 1_000_000))
    return max(-90_000_000, min(90_000_000, v))


def q_lon(value: float) -> int:
    if not isinstance(value, (int, float)):
        raise TypeError("value must be a number")

    v = int(round(float(value) * 1_000_000))
    # Proper longitude wrapping
    while v < -180_000_000:
        v += 360_000_000
    while v >= 180_000_000:
        v -= 360_000_000
    return v


def u_conf(c: float) -> int:
    if not isinstance(c, (int, float)):
        raise TypeError("c must be a number")

    c = max(0.0, min(1.0, float(c)))
    return int(round(c * 255.0))


def make_gist_bits(msg: dict) -> tuple[bytes, int]:
    """Fixed gist creation with proper bit packing"""
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dictionary")
    if "type" not in msg:
        raise ValueError("msg must contain 'type' field")

    t = DICT_TYPE.get(msg["type"], 0) & 0x7
    obj = DICT_OBJECT.get(msg.get("object", "UNKNOWN"), 0) & 0x7F

    # Validate and quantize coordinates
    lat = msg.get("lat", 0.0)
    if not isinstance(lat, (int, float)) or lat < -90.0 or lat > 90.0:
        lat = 0.0
    lat_q = int(round((float(lat) + 90.0) / 180.0 * 1023)) & 0x3FF

    lon = msg.get("lon", 0.0)
    if not isinstance(lon, (int, float)) or lon < -180.0 or lon > 180.0:
        lon = 0.0
    lon_q = int(round((float(lon) + 180.0) / 360.0 * 1023)) & 0x3FF

    conf = msg.get("conf", 0.5)
    if not isinstance(conf, (int, float)) or conf < 0.0 or conf > 1.0:
        conf = 0.5
    conf_q = int(round(float(conf) * 7.0)) & 0x7

    bw = BitWriter()
    bw.write_bits(t, 3)
    bw.write_bits(obj, 7)
    bw.write_bits(lat_q, 10)
    bw.write_bits(lon_q, 10)
    bw.write_bits(conf_q, 3)

    return bw.getvalue(), 33


def encode_payload(msg: dict) -> bytes:
    """Fixed payload encoding with proper signed integer handling"""
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dictionary")
    if "type" not in msg:
        raise ValueError("msg must contain 'type' field")

    bw = BitWriter()

    # Encode varints
    bw.write_bytes(leb128_encode(DICT_TYPE.get(msg["type"], 0)))
    subj_id = DICT_SUBJECT.get(msg.get("subject", "KESTREL-1"), 0)
    bw.write_bytes(leb128_encode(subj_id))
    obj_id = DICT_OBJECT.get(msg.get("object", "UNKNOWN"), 0)
    bw.write_bytes(leb128_encode(obj_id))

    # Fixed signed integer encoding
    lat = q_lat(msg.get("lat", 0.0))
    lon = q_lon(msg.get("lon", 0.0))

    def write_signed_bits(val: int, bits: int):
        # Convert to unsigned representation for bit packing
        if val < 0:
            val = (1 << bits) + val  # Two's complement
        bw.write_bits(val & ((1 << bits) - 1), bits)

    write_signed_bits(lat, 28)
    write_signed_bits(lon, 29)

    depth_m = msg.get("depth_m", 0.0)
    depth_q = int(round(max(0.0, min(204.7, float(depth_m))) * 10.0)) & 0x7FF
    bw.write_bits(depth_q, 11)

    conf_q = u_conf(msg.get("conf", 0.5))
    bw.write_bits(conf_q, 8)

    return bw.getvalue()


def decode_payload(b: bytes) -> dict:
    """Fixed payload decoding with proper error handling"""
    if not isinstance(b, bytes):
        raise ValueError("payload must be bytes")
    if len(b) == 0:
        raise ValueError("payload cannot be empty")

    try:
        # Read varints
        type_id, pos = leb128_decode(b, 0)
        subj_id, pos = leb128_decode(b, pos)
        obj_id, pos = leb128_decode(b, pos)

        # Read bit fields
        br = BitReader(b[pos:])

        def read_signed_bits(bits: int) -> int:
            v = br.read_bits(bits)
            # Convert from unsigned to signed
            if v >= (1 << (bits - 1)):
                v = v - (1 << bits)
            return v

        lat_q = read_signed_bits(28)
        lon_q = read_signed_bits(29)
        depth_q = br.read_bits(11)
        conf_q = br.read_bits(8)

    except (IndexError, ValueError, EOFError) as e:
        raise ValueError(f"Failed to decode payload: {e}")

    # Dequantize values
    lat = lat_q / 1_000_000.0
    lon = lon_q / 1_000_000.0
    depth_m = depth_q / 10.0
    conf = conf_q / 255.0

    # Create reverse dictionaries
    inv_type = {v: k for k, v in DICT_TYPE.items()}
    inv_subj = {v: k for k, v in DICT_SUBJECT.items()}
    inv_obj = {v: k for k, v in DICT_OBJECT.items()}

    return {
        "type": inv_type.get(type_id, f"TYPE_{type_id}"),
        "subject": inv_subj.get(subj_id, f"SUBJ_{subj_id}"),
        "object": inv_obj.get(obj_id, f"OBJ_{obj_id}"),
        "lat": lat,
        "lon": lon,
        "depth_m": depth_m,
        "conf": conf,
    }


def parse_gist(gist_bytes: bytes, gist_bits: int) -> dict:
    """Fixed gist parsing"""
    if not isinstance(gist_bytes, bytes):
        raise ValueError("gist_bytes must be bytes")
    if not isinstance(gist_bits, int) or gist_bits <= 0:
        raise ValueError("gist_bits must be a positive integer")

    try:
        br = BitReader(gist_bytes)
        t = br.read_bits(3)
        obj = br.read_bits(7)
        lat = br.read_bits(10)
        lon = br.read_bits(10)
        conf = br.read_bits(3)
    except (EOFError, ValueError) as e:
        raise ValueError(f"Failed to parse gist bits: {e}")

    inv_type = {
        1: "DETECT",
        2: "STATUS",
        3: "TEXT",
        4: "VOICE",
        5: "CMD",
        6: "CMD_BATCH",
    }
    inv_obj = {1: "H2O_ICE", 2: "CH4_ICE", 3: "BASALT", 4: "UNKNOWN"}

    try:
        lat_coarse = (lat / 1023.0) * 180.0 - 90.0
        lon_coarse = (lon / 1023.0) * 360.0 - 180.0
        conf_coarse = conf / 7.0

        return {
            "type": inv_type.get(t, f"TYPE_{t}"),
            "object": inv_obj.get(obj, f"OBJ_{obj}"),
            "lat_coarse": lat_coarse,
            "lon_coarse": lon_coarse,
            "conf_coarse": conf_coarse,
        }
    except (TypeError, ZeroDivisionError) as e:
        raise ValueError(f"Failed to create gist result: {e}")
