import math
import struct

from astral.crc import crc16_ccitt
from astral.codec import (
    pack_message,
    pack_message_tm,
    unpack_frames_tm,
    unpack_stream,
)
from astral.tmframe import (
    ASM,
    FECF_SIZE,
    FILL_BYTE,
    FRAME_DATA_SIZE,
    FRAME_HEADER_SIZE,
    FRAME_SIZE,
    WIRE_FRAME_SIZE,
    TmFrameCounter,
    apply_prng,
    decode_frames,
    encode_frames,
    make_idle_frame,
)

# 1. Constants
assert ASM == bytes([0x1A, 0xCF, 0xFC, 0x1D])
assert FRAME_SIZE == 1115
assert FRAME_HEADER_SIZE == 6
assert FECF_SIZE == 2
assert FRAME_DATA_SIZE == 1107
assert WIRE_FRAME_SIZE == 1119
assert FILL_BYTE == 0xE0
assert FRAME_SIZE == FRAME_HEADER_SIZE + FRAME_DATA_SIZE + FECF_SIZE

# 2. PRNG test vector (CCSDS-mandated)
prng_out = apply_prng(bytes(8))
expected = bytes([0xFF, 0x1A, 0xAF, 0x66, 0x52, 0x23, 0x1E, 0x10])
assert prng_out == expected, f"PRNG test vector failed: {prng_out.hex()}"

# 3. PRNG is self-inverse
test_payload = b"CCSDS TM Synchronization and Channel Coding Standard 131.0-B"
assert apply_prng(apply_prng(test_payload)) == test_payload

# 4. PRNG period is 255 bytes
seq510 = apply_prng(bytes(510))
assert seq510[:255] == seq510[255:], "PRNG period must be 255 bytes"

# 5. TmFrameCounter
c = TmFrameCounter()
mc0, vc0 = c.next(vcid=0)
assert mc0 == 0 and vc0 == 0
mc1, vc1 = c.next(vcid=0)
assert mc1 == 1 and vc1 == 1
_, vc_ch1_0 = c.next(vcid=1)
assert vc_ch1_0 == 0, "VCID 1 counter must start independently at 0"
# Wrap at 256
c2 = TmFrameCounter()
c2._mc = 255
mc_w, _ = c2.next(vcid=0)
assert mc_w == 255
mc_w2, _ = c2.next(vcid=0)
assert mc_w2 == 0, "MC counter must wrap at 256"
# reset
c3 = TmFrameCounter()
c3.next(vcid=0)
c3.next(vcid=0)
c3.reset()
mc_r, vc_r = c3.next(vcid=0)
assert mc_r == 0 and vc_r == 0, "reset() must clear all counters"

# 6. encode_frames output size
# Single-frame: data <= 1107 bytes -> 1 wire frame = 1119 bytes
small_data = bytes(500)
wire_small = encode_frames(small_data, scid=42, vcid=0)
assert len(wire_small) == WIRE_FRAME_SIZE, f"expected {WIRE_FRAME_SIZE}, got {len(wire_small)}"

# Multi-frame: data > 1107 bytes -> ceil(len/1107) frames
large_data = bytes(2500)
wire_large = encode_frames(large_data, scid=42, vcid=0)
expected_frames = math.ceil(len(large_data) / FRAME_DATA_SIZE)
assert len(wire_large) == expected_frames * WIRE_FRAME_SIZE

# 7. Wire frame structure
c4 = TmFrameCounter()
wire_one = encode_frames(bytes(FRAME_DATA_SIZE), scid=0x2A, vcid=3, counter=c4, randomise=False)
assert len(wire_one) == WIRE_FRAME_SIZE
# Check ASM
assert wire_one[:4] == ASM, f"ASM missing: {wire_one[:4].hex()}"
# Check primary header fields (randomise=False so header is clear)
frame = wire_one[4:]
word1, mc, vc, word3 = struct.unpack(">HBBH", frame[:6])
assert (word1 >> 4) & 0x3FF == 0x2A, "SCID wrong"
assert (word1 >> 1) & 0x7 == 3, "VCID wrong"
assert (word1 >> 14) & 0x3 == 0, "TFVN must be 0"
assert mc == 0 and vc == 0, "first frame counts must be 0"
assert (word3 >> 11) & 0x3 == 0b11, "seg_len_id must be 0b11"
assert word3 & 0x7FF == 0x7FF, "FHP must be 0x7FF"
# Check FECF (last 2 bytes, randomise=False)
hdr_bytes = frame[:FRAME_HEADER_SIZE]
data_bytes = frame[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + FRAME_DATA_SIZE]
fecf_bytes = frame[FRAME_HEADER_SIZE + FRAME_DATA_SIZE :]
expected_crc = crc16_ccitt(hdr_bytes + data_bytes)
actual_crc = struct.unpack(">H", fecf_bytes)[0]
assert expected_crc == actual_crc, "FECF CRC wrong"

# 8. Randomisation: header NOT randomised, data+FECF IS randomised
c5 = TmFrameCounter()
data_pattern = bytes(range(256)) * (FRAME_DATA_SIZE // 256 + 1)
data_pattern = data_pattern[:FRAME_DATA_SIZE]
wire_rnd = encode_frames(data_pattern, scid=1, vcid=0, counter=c5, randomise=True)
wire_clr = encode_frames(data_pattern, scid=1, vcid=0, counter=TmFrameCounter(), randomise=False)
# Headers (bytes 4-9) must be identical (not randomised)
assert wire_rnd[4:10] == wire_clr[4:10], "header must not be randomised"
# Data fields must differ (randomised vs clear)
assert wire_rnd[10:] != wire_clr[10:], "data field must be randomised"

# 9. Clean encode/decode roundtrip
payload = bytes(range(200)) + b"ASTRAL test payload"
wire_rt = encode_frames(payload, scid=42, vcid=0)
data_rt, stats_rt = decode_frames(wire_rt)
assert stats_rt["n_frames"] == 1
assert stats_rt["n_crc_errors"] == 0
assert data_rt[: len(payload)] == payload, "payload not recovered"

# 10. CRC error -> frame dropped
wire_err = bytearray(wire_rt)
wire_err[4 + FRAME_HEADER_SIZE + 100] ^= 0xFF  # corrupt data field
data_err, stats_err = decode_frames(bytes(wire_err))
assert stats_err["n_crc_errors"] == 1
assert stats_err["n_frames"] == 0
assert data_err == b"", "corrupted frame must yield no data"

# 11. Bad ASM -> frame skipped
wire_bad_asm = bytearray(wire_rt)
wire_bad_asm[0] ^= 0xFF  # break ASM
data_bad, stats_bad = decode_frames(bytes(wire_bad_asm))
# Frame with bad ASM is skipped; 0 frames decoded, 0 CRC errors counted
assert stats_bad["n_frames"] == 0
assert stats_bad["n_crc_errors"] == 0

# 12. Multi-frame encode/decode
big_payload = bytes(i % 256 for i in range(FRAME_DATA_SIZE * 3 - 50))
wire_big = encode_frames(big_payload, scid=7, vcid=2)
data_big, stats_big = decode_frames(wire_big)
assert stats_big["n_frames"] == 3
assert stats_big["n_crc_errors"] == 0
assert data_big[: len(big_payload)] == big_payload

# 13. Idle frame
idle = make_idle_frame(scid=42, vcid=0)
assert len(idle) == WIRE_FRAME_SIZE, f"idle frame must be {WIRE_FRAME_SIZE} bytes"
idle_data, idle_stats = decode_frames(idle)
assert idle_stats["n_frames"] == 1
assert idle_stats["n_crc_errors"] == 0
# Data field should be all FILL_BYTE (after de-randomisation)
assert all(b == FILL_BYTE for b in idle_data), "idle data field must be FILL_BYTE"

# 14. pack_message_tm / unpack_frames_tm end-to-end
msg = {
    "type": "DETECT",
    "subject": "KESTREL-2",
    "object": "H2O_ICE",
    "lat": -43.7,
    "lon": 130.2,
    "depth_m": 18.0,
    "conf": 0.94,
}
wire_msg = pack_message_tm(msg, scid=42, vcid=0, extra_fountain=5)
assert len(wire_msg) % WIRE_FRAME_SIZE == 0, "wire must be whole frames"

result = unpack_frames_tm(wire_msg)
assert "error" not in result, f"unexpected error: {result}"
assert result["tm_n_frames"] >= 1
assert result["tm_n_crc_errors"] == 0
assert result["complete"] is True
assert abs(result["message"]["lat"] - -43.7) < 1e-6
assert abs(result["message"]["lon"] - 130.2) < 1e-6

# 15. Frame CRC error -> fountain recovers (with sufficient redundancy)
wire_loss = bytearray(pack_message_tm(msg, scid=42, vcid=0, extra_fountain=50))
n_wire_frames = len(wire_loss) // WIRE_FRAME_SIZE
# Corrupt the last frame's data field -> CRC fail -> frame dropped
last_frame_start = (n_wire_frames - 1) * WIRE_FRAME_SIZE
wire_loss[last_frame_start + 4 + FRAME_HEADER_SIZE + 5] ^= 0xFF
result_loss = unpack_frames_tm(bytes(wire_loss))
assert result_loss["tm_n_crc_errors"] >= 1
assert result_loss["complete"] is True, (
    "fountain should recover from 1 dropped TM frame (extra_fountain=50)"
)

# 16. Existing API still works
plain = pack_message(msg, extra_fountain=3)
plain_result = unpack_stream(plain)
assert plain_result["complete"], "existing pack_message must still work"

print("All Phase 5 checks passed.")
