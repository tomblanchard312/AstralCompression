from astral.rs_fec import (
    encode_stream,
    decode_stream,
    codeword_size,
    ATOM_SIZE,
    CODEWORD_SIZE,
    PARITY_E16,
    PARITY_E8,
    _RS_E16,
    _RS_E8,
)
from astral.codec import (
    pack_message,
    unpack_stream,
    pack_message_rs,
    unpack_stream_rs,
)

# 1. Constants
assert ATOM_SIZE == 32
assert PARITY_E16 == 32
assert PARITY_E8 == 16
assert CODEWORD_SIZE[16] == 64
assert CODEWORD_SIZE[8] == 48
assert codeword_size(16) == 64
assert codeword_size(8) == 48

# 2. RS codec uses exact CCSDS parameters

ref_enc = _RS_E16.encode(bytes(32))
assert len(ref_enc) == 64, f"expected 64 bytes, got {len(ref_enc)}"
ref_enc_e8 = _RS_E8.encode(bytes(32))
assert len(ref_enc_e8) == 48

# 3. encode_stream output size
_ = bytes(range(256)) * (32 // 32)
test_atoms = bytes(i % 256 for i in range(32 * 8))
enc16 = encode_stream(test_atoms, e=16)
assert len(enc16) == 8 * 64, f"expected {8 * 64}, got {len(enc16)}"
enc8 = encode_stream(test_atoms, e=8)
assert len(enc8) == 8 * 48, f"expected {8 * 48}, got {len(enc8)}"

# 4. Clean roundtrip E=16
rec16, n_corr16, n_drop16 = decode_stream(enc16, e=16)
assert rec16 == test_atoms, "E=16 clean decode: data mismatch"
assert n_corr16 == 0, "E=16 clean decode: unexpected corrections"
assert n_drop16 == 0, "E=16 clean decode: unexpected drops"

# 5. Clean roundtrip E=8
rec8, n_corr8, n_drop8 = decode_stream(enc8, e=8)
assert rec8 == test_atoms, "E=8 clean decode: data mismatch"
assert n_drop8 == 0

# 6. Correct up to 16 byte errors per codeword (E=16)
corrupted16 = bytearray(enc16)
for j in range(16):
    corrupted16[j * 4 % 64] ^= (0xAB + j) & 0xFF
rec_err16, n_corr_err, n_drop_err = decode_stream(bytes(corrupted16), e=16)
assert rec_err16 == test_atoms, "E=16 error correction failed"
assert n_drop_err == 0, "E=16: unexpectedly dropped a codeword"
assert n_corr_err > 0, "E=16: no corrections reported"

# 7. Correct up to 8 byte errors per codeword (E=8)
corrupted8 = bytearray(enc8)
for j in range(8):
    corrupted8[j * 6 % 48] ^= (0xCD + j) & 0xFF
rec_err8, n_corr_err8, n_drop_err8 = decode_stream(bytes(corrupted8), e=8)
assert rec_err8 == test_atoms, "E=8 error correction failed"
assert n_drop_err8 == 0

# 8. Uncorrectable codeword -> atom dropped (17 errors, E=16)
corrupted_drop = bytearray(enc16)
for j in range(17):
    corrupted_drop[j] ^= 0xFF
rec_drop, _, n_drop = decode_stream(bytes(corrupted_drop), e=16)
assert n_drop == 1, "expected exactly 1 uncorrectable atom"
assert len(rec_drop) == 7 * ATOM_SIZE, "dropped atom must be absent (not zeroed)"

# 9. Empty stream
assert encode_stream(b"", e=16) == b""
assert encode_stream(b"", e=8) == b""
r, c, d = decode_stream(b"", e=16)
assert r == b"" and c == 0 and d == 0

# 10. ValueError on bad inputs
try:
    encode_stream(bytes(33), e=16)
    assert False, "must raise ValueError"
except ValueError:
    pass

try:
    encode_stream(bytes(32), e=4)
    assert False, "must raise ValueError"
except ValueError:
    pass

try:
    decode_stream(bytes(64), e=4)
    assert False, "must raise ValueError"
except ValueError:
    pass

# 11. pack_message_rs / unpack_stream_rs end-to-end
msg = {
    "type": "DETECT",
    "subject": "KESTREL-2",
    "object": "H2O_ICE",
    "lat": -43.7,
    "lon": 130.2,
    "depth_m": 18.0,
    "conf": 0.94,
}
rs_stream = pack_message_rs(msg, extra_fountain=5, e=16)
assert len(rs_stream) % 64 == 0, "RS stream length not multiple of codeword size"

result = unpack_stream_rs(rs_stream, e=16)
assert "error" not in result, f"unexpected error: {result}"
assert result["rs_e"] == 16
assert result["rs_corrected_symbols"] == 0
assert result["rs_uncorrectable_atoms"] == 0
assert result["complete"] is True
assert abs(result["message"]["lat"] - -43.7) < 1e-6, "lat mismatch"
assert abs(result["message"]["lon"] - 130.2) < 1e-6, "lon mismatch"

# 12. pack_message_rs with errors injected
corrupted_rs = bytearray(rs_stream)
for cw_idx in [2, 4, 6]:
    base = cw_idx * 64
    for j in range(8):
        corrupted_rs[base + j] ^= (0xBE + j) & 0xFF
result_err = unpack_stream_rs(bytes(corrupted_rs), e=16)
assert "error" not in result_err
assert result_err["complete"] is True
assert result_err["rs_corrected_symbols"] > 0
assert result_err["rs_uncorrectable_atoms"] == 0
assert abs(result_err["message"]["lat"] - -43.7) < 1e-6

# 13. unpack_stream_rs error path
bad = unpack_stream_rs(b"not rs data", e=16)
assert isinstance(bad, dict)

# 14. Fountain code recovers from RS-dropped atoms
msg2 = {
    "type": "DETECT",
    "subject": "KESTREL-1",
    "object": "BASALT",
    "lat": 10.0,
    "lon": 20.0,
    "depth_m": 0.0,
    "conf": 0.8,
}
rs_stream2 = pack_message_rs(msg2, extra_fountain=15, e=16)
corrupted2 = bytearray(rs_stream2)
for cw_idx in [3, 7]:
    base = cw_idx * 64
    for j in range(17):
        corrupted2[base + j] ^= 0xFF
result2 = unpack_stream_rs(bytes(corrupted2), e=16)
assert result2["rs_uncorrectable_atoms"] == 2
assert (
    result2["complete"] is True
), f"fountain should recover from 2 dropped atoms, got: {result2}"

# 15. E=8 end-to-end
rs_e8_stream = pack_message_rs(msg, extra_fountain=5, e=8)
assert len(rs_e8_stream) % 48 == 0
result_e8 = unpack_stream_rs(rs_e8_stream, e=8)
assert result_e8["rs_e"] == 8
assert result_e8["complete"] is True

# 16. Existing API unchanged
plain = pack_message(msg, extra_fountain=3)
plain_result = unpack_stream(plain)
assert plain_result["complete"], "existing pack_message must still work"

print("All Phase 4 checks passed.")
