#!/usr/bin/env python3
"""Verification script for all bug fixes."""

import sys

sys.path.insert(0, ".")

try:
    # Test 1: grammar roundtrip
    print("Test 1: Grammar roundtrip with lat/lon fix...")
    from astral.grammar import encode_payload, decode_payload

    msg = {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": -43.7,
        "lon": 130.2,
        "depth_m": 18.0,
        "conf": 0.94,
    }
    dec = decode_payload(encode_payload(msg))
    lat_error = abs(dec["lat"] - -43.7)  # type: ignore
    assert lat_error < 1e-6, f"lat mismatch: {dec['lat']}"
    lon_error = abs(dec["lon"] - 130.2)  # type: ignore
    assert lon_error < 1e-6, f"lon mismatch: {dec['lon']}"
    print("✓ Grammar roundtrip passed")

    # Test 2: fountain K=1 roundtrip
    print("\nTest 2: Fountain K=1 roundtrip with padding fix...")
    from astral.fountain import lt_encode_blocks, lt_decode_blocks

    blocks = [b"Hello World!!!!"]  # 15 bytes, will be padded to 16
    packets = lt_encode_blocks(blocks, seed=12345, num_packets=5)
    recovered, frac = lt_decode_blocks(packets, 1, 16)
    assert (
        recovered is not None and frac == 1.0
    ), f"K=1 fountain failed: recovered={recovered}, frac={frac}"
    print("✓ Fountain K=1 roundtrip passed")

    # Test 3: fountain all-zero block
    print("\nTest 3: Fountain all-zero block handling...")
    blocks = [bytes(16)]
    packets = lt_encode_blocks(blocks, seed=99, num_packets=5)
    recovered, frac = lt_decode_blocks(packets, 1, 16)
    assert (
        recovered is not None and frac == 1.0
    ), f"all-zero block failed: recovered={recovered}, frac={frac}"
    print("✓ Fountain all-zero block passed")

    # Test 4: text message roundtrip
    print("\nTest 4: Text message roundtrip with import fix...")
    from astral.codec import pack_text_message, unpack_stream

    text = "Hello from ASTRAL: nominal link, standing by."
    result = unpack_stream(pack_text_message(text, extra_fountain=5))
    msg_text = result["message"]["text"]  # type: ignore
    assert result["complete"] and msg_text == text, (
        f"text roundtrip failed: {result}"
    )
    print("✓ Text message roundtrip passed")

    # Test 5: full pack/unpack roundtrip
    print("\nTest 5: Full pack/unpack roundtrip with command fix...")
    from astral.codec import pack_message

    result = unpack_stream(pack_message(msg, extra_fountain=3))
    assert result["complete"], "pack/unpack not complete"
    msg_lat = result["message"]["lat"]  # type: ignore
    assert abs(msg_lat - -43.7) < 1e-6, f"lat roundtrip failed: {msg_lat}"
    print("✓ Full pack/unpack roundtrip passed")

    print("\n" + "=" * 50)
    print("✓✓✓ All checks passed! ✓✓✓")
    print("=" * 50)

except Exception:
    print("\n✗ Test failed with error:")
    import traceback

    traceback.print_exc()
    sys.exit(1)
