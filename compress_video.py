#!/usr/bin/env python3
"""
Video Compression using McKay's Extreme Compression System
Compresses the specified MP4 video file using McKay's algorithms.
"""

import sys
import os

sys.path.append("astral")

from mckay_astral_integration import McKayASTRALCompressor
import time
import struct


def compress_video_file(video_path: str):
    """Compress a video file using McKay's extreme compression"""
    print(f"🚀 McKay's Extreme Video Compression")
    print("=" * 60)

    # Check if file exists
    if not os.path.exists(video_path):
        print(f"❌ Error: Video file not found: {video_path}")
        return False

    # Get file info
    file_size = os.path.getsize(video_path)
    print(f"📹 Video File: {os.path.basename(video_path)}")
    print(f"📊 Original Size: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")

    # Read video file
    print(f"\n📖 Reading video file...")
    try:
        with open(video_path, "rb") as f:
            video_data = f.read()
        print(f"✅ Successfully read {len(video_data):,} bytes")
    except Exception as e:
        print(f"❌ Error reading video file: {e}")
        return False

    # Create McKay compressor
    print(f"\n🔧 Initializing McKay's extreme compression...")
    compressor = McKayASTRALCompressor(compression_mode="extreme")

    # Compress video
    print(f"🎬 Compressing video with McKay's algorithms...")
    start_time = time.time()

    try:
        compressed_data = compressor.compress_with_mckay(video_data, "VIDEO")
        compression_time = time.time() - start_time

        # Get compression stats
        stats = compressor.get_compression_stats()

        print(f"\n🎉 Compression Complete!")
        print(f"⏱️  Compression Time: {compression_time:.2f} seconds")
        print(
            f"📊 Compressed Size: {len(compressed_data):,} bytes ({len(compressed_data) / (1024*1024):.2f} MB)"
        )
        print(f"🚀 Compression Ratio: {stats['compression_ratio']:.2f}x")
        print(f"💾 Space Saved: {stats['space_saved_percent']:.1f}%")
        print(f"⭐ McKay Rating: {stats['mckay_rating']}")

        # Calculate bandwidth savings
        bandwidth_saved = (1 - 1 / stats["compression_ratio"]) * 100
        print(f"🌐 Transmission Time Reduction: {bandwidth_saved:.1f}%")

        # Save compressed file
        compressed_path = video_path.replace(".mp4", "_mckay_compressed.mckay")
        print(f"\n💾 Saving compressed file to: {compressed_path}")

        try:
            with open(compressed_path, "wb") as f:
                f.write(compressed_data)
            print(f"✅ Compressed file saved successfully!")

            # Show file size comparison
            compressed_size = os.path.getsize(compressed_path)
            print(f"\n📊 File Size Comparison:")
            print(f"  Original: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")
            print(
                f"  Compressed: {compressed_size:,} bytes ({compressed_size / (1024*1024):.2f} MB)"
            )
            print(
                f"  Savings: {file_size - compressed_size:,} bytes ({(file_size - compressed_size) / (1024*1024):.2f} MB)"
            )

            return True

        except Exception as e:
            print(f"❌ Error saving compressed file: {e}")
            return False

    except Exception as e:
        print(f"❌ Error during compression: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_decompression(compressed_path: str):
    """Test decompression of the compressed video"""
    print(f"\n🧪 Testing Decompression...")

    try:
        # Read compressed file
        with open(compressed_path, "rb") as f:
            compressed_data = f.read()

        # Create compressor
        compressor = McKayASTRALCompressor()

        # Decompress
        print(f"🔧 Decompressing video...")
        start_time = time.time()
        decompressed_data = compressor.decompress_with_mckay(compressed_data)
        decompression_time = time.time() - start_time

        print(f"✅ Decompression successful!")
        print(f"⏱️  Decompression Time: {decompression_time:.2f} seconds")
        print(f"📊 Decompressed Size: {len(decompressed_data):,} bytes")

        # Verify integrity
        original_path = compressed_path.replace("_mckay_compressed.mckay", ".mp4")
        if os.path.exists(original_path):
            with open(original_path, "rb") as f:
                original_data = f.read()

            if len(decompressed_data) == len(original_data):
                print(f"✅ Data integrity verified: Sizes match!")
            else:
                print(
                    f"⚠️  Size mismatch: Original {len(original_data):,} vs Decompressed {len(decompressed_data):,}"
                )

        return True

    except Exception as e:
        print(f"❌ Decompression error: {e}")
        return False


def main():
    """Main function"""
    video_path = r"C:\Users\wtblanch\Videos\20230409121922.mp4"

    print("🎬 McKay Video Compression Tool")
    print("=" * 60)
    print(f"Target Video: {video_path}")
    print("=" * 60)

    # Compress video
    if compress_video_file(video_path):
        # Test decompression
        compressed_path = video_path.replace(".mp4", "_mckay_compressed.mckay")
        if os.path.exists(compressed_path):
            test_decompression(compressed_path)

        print(f"\n🎉 Video compression completed successfully!")
        print(f"📁 Compressed file: {compressed_path}")
        print(f"🚀 Ready for deep space transmission with McKay's extreme compression!")
    else:
        print(f"\n❌ Video compression failed!")


if __name__ == "__main__":
    main()
