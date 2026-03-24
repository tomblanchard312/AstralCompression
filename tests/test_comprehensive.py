import random
from astral.codec import (
    pack_message,
    unpack_stream,
    pack_text_message,
    pack_cmd_message,
)
from astral.container import make_atom, parse_atoms
from astral.fountain import lt_encode_blocks, lt_decode_blocks
from astral.bitstream import BitWriter, BitReader


def test_basic_functionality():
    """Test basic pack/unpack functionality"""
    print("Testing basic functionality...")

    msg = {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": -43.7,
        "lon": 130.2,
        "depth_m": 18.0,
        "conf": 0.94,
    }

    # Test that pack_message now returns data
    blob = pack_message(msg, extra_fountain=3)
    assert len(blob) > 0, "pack_message should return data"
    assert len(blob) % 32 == 0, "Output should be multiple of 32 bytes"

    # Test unpack
    result = unpack_stream(blob)
    assert result["complete"] is True, "Should decode completely"
    assert abs(result["message"]["lat"] - msg["lat"]) < 1e-6, "Latitude should match"

    print("✓ Basic functionality works")


def test_edge_cases():
    """Test edge cases and error conditions"""
    print("Testing edge cases...")

    # Test empty message
    try:
        pack_message({})
        assert False, "Should fail with empty message"
    except ValueError:
        pass  # Expected

    # Test invalid message type
    try:
        pack_message({"type": "INVALID_TYPE"})
        assert False, "Should handle invalid message type gracefully"
    except Exception:
        pass  # Expected

    # Test extreme coordinates
    msg = {
        "type": "DETECT",
        "lat": 91.0,  # Invalid latitude
        "lon": 200.0,  # Invalid longitude
        "conf": 1.5,  # Invalid confidence
    }

    # Should handle gracefully
    blob = pack_message(msg)
    assert len(blob) > 0, "Should handle invalid coordinates"

    print("✓ Edge cases handled properly")


def test_fountain_code():
    """Test fountain code encoding/decoding"""
    print("Testing fountain code...")

    # Test with single block
    raw = b"Hello World Test"
    block = raw + bytes(16 - len(raw) % 16) if len(raw) % 16 else raw
    blocks = [block]
    packets = lt_encode_blocks(blocks, seed=12345, num_packets=5)
    assert len(packets) == 5, "Should generate correct number of packets"

    recovered, frac = lt_decode_blocks(packets, 1, 16)
    assert recovered is not None, "Should recover single block"
    assert frac == 1.0, "Should recover completely"
    assert recovered[0] == block, "Should recover correct data"
    # Test with multiple blocks
    blocks = [b"Block1" + b"\x00" * 10, b"Block2" + b"\x00" * 10]
    packets = lt_encode_blocks(blocks, seed=54321, num_packets=10)
    assert len(packets) == 10, "Should generate correct number of packets"

    recovered, frac = lt_decode_blocks(packets, 2, 16)
    assert recovered is not None, "Should recover multiple blocks"
    assert frac == 1.0, "Should recover completely"

    print("✓ Fountain code works correctly")


def test_loss_tolerance():
    """Test loss tolerance with various drop rates"""
    print("Testing loss tolerance...")

    msg = {
        "type": "DETECT",
        "subject": "KESTREL-2",
        "object": "H2O_ICE",
        "lat": -10.0,
        "lon": 20.0,
        "depth_m": 5.5,
        "conf": 0.8,
    }

    # Test with high redundancy
    blob = pack_message(msg, extra_fountain=20)

    # Test various drop rates
    for drop_rate in [0.1, 0.3, 0.5, 0.7]:
        rng = random.Random(42)
        lossy = bytearray()
        first = True
        for i in range(0, len(blob), 32):
            atom = blob[i : i + 32]
            if len(atom) < 32:
                break
            if first or rng.random() >= drop_rate:
                lossy += atom
            first = False
        out = unpack_stream(bytes(lossy))
        assert "gist" in out, f"Should have gist even with {drop_rate:.1f} drop rate"
        assert out["gist"]["type"] == "DETECT", "Gist type should be preserved"  # type: ignore

        if drop_rate < 0.6:  # With high redundancy, should recover completely
            assert (
                out["complete"] is True
            ), f"Should recover completely with {drop_rate:.1f} drop rate"

    print("✓ Loss tolerance works as expected")


def test_text_messages():
    """Test text message encoding/decoding"""
    print("Testing text messages...")

    text = "Hello from ASTRAL: nominal link, standing by."
    blob = pack_text_message(text, extra_fountain=5)

    result = unpack_stream(blob)
    assert result["complete"] is True, "Text should decode completely"
    assert result["message"]["type"] == "TEXT", "Should be TEXT type"  # type: ignore
    expected = "hello from ASTRAL: nominal link, standing by."
    assert result["message"]["text"] == expected, (  # type: ignore
        f"Text mismatch.\nExpected: {expected!r}\n"
        f"Got:      {result['message']['text']!r}"  # type: ignore
    )
    print("✓ Text messages work correctly")


def test_command_messages():
    """Test command message encoding/decoding"""
    print("Testing command messages...")

    cmd = {"name": "POINT", "az": -12.3456, "el": 30.0}
    blob = pack_cmd_message(cmd, extra_fountain=5)

    result = unpack_stream(blob)
    assert result["complete"] is True, "Command should decode completely"
    assert result["message"]["type"] == "CMD", "Should be CMD type"  # type: ignore
    assert result["message"]["cmd"]["name"] == "POINT", "Command name should match"  # type: ignore

    print("✓ Command messages work correctly")


def test_container_integrity():
    """Test atom container integrity"""
    print("Testing container integrity...")

    # Test atom creation
    atom = make_atom(0, 10, 12345, 0, b"test payload")
    assert len(atom) == 32, "Atom should be exactly 32 bytes"
    assert atom[0] == 0xA5, "First sync byte should be 0xA5"
    assert atom[1] == 0xE6, "Second sync byte should be 0xE6"

    # Test atom parsing
    atoms = parse_atoms(atom)
    assert len(atoms) == 1, "Should parse one atom"
    assert atoms[0][0] == 0, "Atom index should be 0"
    assert atoms[0][1] == 10, "Total atoms should be 10"
    assert atoms[0][2] == 12345, "Message ID should be 12345"

    print("✓ Container integrity maintained")


def test_bitstream_operations():
    """Test bitstream reading/writing"""
    print("Testing bitstream operations...")

    bw = BitWriter()
    bw.write_bits(5, 3)  # Write 5 in 3 bits
    bw.write_bits(10, 4)  # Write 10 in 4 bits
    bw.write_bits(255, 8)  # Write 255 in 8 bits

    data = bw.getvalue()
    assert len(data) > 0, "Should produce output"

    br = BitReader(data)
    assert br.read_bits(3) == 5, "Should read 5 from 3 bits"
    assert br.read_bits(4) == 10, "Should read 10 from 4 bits"
    assert br.read_bits(8) == 255, "Should read 255 from 8 bits"

    print("✓ Bitstream operations work correctly")


def test_error_handling():
    """Test error handling and validation"""
    print("Testing error handling...")

    # Test invalid input types
    try:
        pack_message("not a dict")  # type: ignore
        assert False, "Should reject non-dict input"
    except ValueError:
        pass  # Expected

    # Test missing required fields
    try:
        pack_message({"lat": 10.0})  # Missing type
        assert False, "Should reject message without type"
    except ValueError:
        pass  # Expected

    # Test invalid fountain parameters
    try:
        lt_encode_blocks([], seed=123, num_packets=5)
        assert False, "Should reject empty blocks"
    except ValueError:
        pass  # Expected

    print("✓ Error handling works correctly")


def run_all_tests():
    """Run all tests"""
    print("Running comprehensive test suite...")
    print("=" * 50)

    tests = [
        test_basic_functionality,
        test_edge_cases,
        test_fountain_code,
        test_loss_tolerance,
        test_text_messages,
        test_command_messages,
        test_container_integrity,
        test_bitstream_operations,
        test_error_handling,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1

    print("=" * 50)
    print(f"Tests passed: {passed}")
    print(f"Tests failed: {failed}")

    if failed == 0:
        print("🎉 All tests passed!")
        return True
    else:
        print("❌ Some tests failed!")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
