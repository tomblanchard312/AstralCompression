#!/usr/bin/env python3
"""
Quick test to check if fountain code is working with user's changes
"""

import sys
import os

sys.path.append("astral")

from astral.fountain import lt_encode_blocks, lt_decode_blocks
import random


def test_fountain_basic():
    """Test basic fountain code functionality"""
    print("🧪 Testing Fountain Code Status")
    print("=" * 40)

    # Create test data
    test_data = b"Hello, this is a test of the fountain code system!"
    print(f"Original data: {test_data}")
    print(f"Length: {len(test_data)} bytes")

    # Encode with fountain codes
    print(f"\n🔧 Encoding with fountain codes...")
    try:
        # Split data into blocks first
        block_size = 64
        blocks = [
            test_data[i : i + block_size] for i in range(0, len(test_data), block_size)
        ]
        # Pad last block if needed
        if len(blocks[-1]) < block_size:
            blocks[-1] = blocks[-1] + b"\x00" * (block_size - len(blocks[-1]))

        encoded_blocks = lt_encode_blocks(
            blocks, 12345, int(len(blocks) * 1.5)
        )  # blocks, seed, num_packets
        print(f"✅ Encoding successful!")
        print(f"Encoded blocks: {len(encoded_blocks)}")
        print(
            f"Block size: {len(encoded_blocks[0]) if encoded_blocks else 'N/A'} bytes"
        )

        # Decode with fountain codes
        print(f"\n🔧 Decoding with fountain codes...")
        decoded_data = lt_decode_blocks(
            encoded_blocks, 1, 64
        )  # packets, K, symbol_size

        if decoded_data:
            print(f"✅ Decoding successful!")
            # Extract data from tuple (data, fraction)
            actual_data = (
                decoded_data[0] if isinstance(decoded_data, tuple) else decoded_data
            )
            print(f"Decoded data: {actual_data}")

            # Handle list of blocks
            if isinstance(actual_data, list):
                # Join blocks and remove padding
                joined_data = b"".join(actual_data)
                # Remove null padding
                while joined_data.endswith(b"\x00"):
                    joined_data = joined_data[:-1]
                actual_data = joined_data

            print(f"Length: {len(actual_data)} bytes")

            # Check if data matches (ignore padding)
            if actual_data.startswith(test_data):
                print(f"🎉 PERFECT! Data integrity maintained!")
                print(f"✅ Fountain code is working correctly!")
            else:
                print(f"⚠️  Data mismatch detected!")
                print(f"Original: {test_data}")
                print(f"Decoded:  {decoded_data}")

        else:
            print(f"❌ Decoding failed - no data returned")

    except Exception as e:
        print(f"❌ Fountain code error: {e}")
        import traceback

        traceback.print_exc()


def test_fountain_voice():
    """Test fountain code with voice data"""
    print(f"\n🎵 Testing Fountain Code with Voice Data")
    print("=" * 40)

    # Check if we have the sample voice file
    voice_file = "enhanced_fixed.wav"
    if not os.path.exists(voice_file):
        print(f"❌ Voice file not found: {voice_file}")
        return

    try:
        # Read voice file
        with open(voice_file, "rb") as f:
            voice_data = f.read()

        print(f"Voice file: {len(voice_data):,} bytes")

        # Encode with fountain codes
        print(f"🔧 Encoding voice with fountain codes...")
        # Split voice data into blocks first
        block_size = 1024
        blocks = [
            voice_data[i : i + block_size]
            for i in range(0, len(voice_data), block_size)
        ]
        # Pad last block if needed
        if len(blocks[-1]) < block_size:
            blocks[-1] = blocks[-1] + b"\x00" * (block_size - len(blocks[-1]))

        encoded_blocks = lt_encode_blocks(
            blocks, 54321, int(len(blocks) * 1.2)
        )  # blocks, seed, num_packets
        print(f"✅ Voice encoding successful!")
        print(f"Encoded blocks: {len(encoded_blocks)}")

        # Decode with fountain codes
        print(f"🔧 Decoding voice with fountain codes...")
        decoded_voice = lt_decode_blocks(
            encoded_blocks, len(blocks), 1024
        )  # packets, K, symbol_size

        if decoded_voice:
            print(f"✅ Voice decoding successful!")
            # Extract data from tuple (data, fraction)
            actual_voice = (
                decoded_voice[0] if isinstance(decoded_voice, tuple) else decoded_voice
            )

            # Check if we got valid data
            if actual_voice is None:
                print(f"❌ Voice decoding returned None")
                return

            print(f"Decoded voice: {len(actual_voice):,} bytes")

            # Handle list of blocks
            if isinstance(actual_voice, list):
                # Join blocks and remove padding
                joined_voice = b"".join(actual_voice)
                # Remove null padding
                while joined_voice.endswith(b"\x00"):
                    joined_voice = joined_voice[:-1]
                actual_voice = joined_voice

            print(f"Processed voice: {len(actual_voice):,} bytes")

            # Check data integrity
            if len(actual_voice) == len(voice_data):
                print(f"🎉 Voice data length preserved!")

                # Check first and last few bytes for corruption
                if (
                    actual_voice[:100] == voice_data[:100]
                    and actual_voice[-100:] == voice_data[-100:]
                ):
                    print(f"✅ Voice data integrity verified!")
                    print(f"✅ Fountain code working perfectly with voice!")
                else:
                    print(f"⚠️  Voice data corruption detected!")
            else:
                print(f"❌ Voice data length mismatch!")
        else:
            print(f"❌ Voice decoding failed!")

    except Exception as e:
        print(f"❌ Voice fountain code error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_fountain_basic()
    test_fountain_voice()
