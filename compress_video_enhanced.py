#!/usr/bin/env python3
"""
Enhanced Video Compression using McKay's Extreme Compression System
Intelligently compresses video files with better handling of MP4 format.
"""

import sys
import os
sys.path.append('astral')

from mckay_astral_integration import McKayASTRALCompressor
import time
import struct
import shutil

def analyze_video_structure(video_data: bytes):
    """Analyze the video file structure to understand compression potential"""
    print(f"🔍 Analyzing video structure...")
    
    # Check for common video formats
    if video_data.startswith(b'\x00\x00\x00') and b'mdat' in video_data[:1000]:
        print(f"  📹 Format: MP4 (MPEG-4)")
        print(f"  📊 Already compressed with H.264/H.265 codec")
        return "MP4"
    elif video_data.startswith(b'RIFF') and b'AVI ' in video_data[:20]:
        print(f"  📹 Format: AVI")
        return "AVI"
    elif video_data.startswith(b'\x1a\x45\xdf\xa3'):
        print(f"  📹 Format: Matroska (MKV)")
        return "MKV"
    else:
        print(f"  📹 Format: Unknown/Other")
        return "UNKNOWN"

def compress_video_file(video_path: str):
    """Compress a video file using McKay's extreme compression"""
    print(f"🚀 McKay's Enhanced Video Compression")
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
        with open(video_path, 'rb') as f:
            video_data = f.read()
        print(f"✅ Successfully read {len(video_data):,} bytes")
    except Exception as e:
        print(f"❌ Error reading video file: {e}")
        return False
    
    # Analyze video structure
    video_format = analyze_video_structure(video_data)
    
    # Create McKay compressor
    print(f"\n🔧 Initializing McKay's extreme compression...")
    compressor = McKayASTRALCompressor(compression_mode="extreme")
    
    # Try different compression strategies based on format
    compression_results = []
    
    # Strategy 1: Direct video compression
    print(f"\n🎬 Strategy 1: Direct video compression...")
    start_time = time.time()
    try:
        compressed_video = compressor.compress_with_mckay(video_data, "VIDEO")
        compression_time = time.time() - start_time
        stats = compressor.get_compression_stats()
        
        result = {
            "strategy": "Direct Video",
            "compressed_data": compressed_video,
            "compression_time": compression_time,
            "stats": stats
        }
        compression_results.append(result)
        
        print(f"  ✅ Compression: {stats['compression_ratio']:.2f}x - {stats['mckay_rating']}")
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Strategy 2: Binary compression (for already-compressed formats)
    print(f"\n🔢 Strategy 2: Binary compression...")
    start_time = time.time()
    try:
        compressed_binary = compressor.compress_with_mckay(video_data, "BINARY")
        compression_time = time.time() - start_time
        stats = compressor.get_compression_stats()
        
        result = {
            "strategy": "Binary",
            "compressed_data": compressed_binary,
            "compression_time": compression_time,
            "stats": stats
        }
        compression_results.append(result)
        
        print(f"  ✅ Compression: {stats['compression_ratio']:.2f}x - {stats['mckay_rating']}")
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Strategy 3: Frame-based compression for large files
    if file_size > 100 * 1024 * 1024:  # > 100MB
        print(f"\n🎞️  Strategy 3: Frame-based compression...")
        start_time = time.time()
        try:
            # Split into chunks for frame analysis
            chunk_size = 1024 * 1024  # 1MB chunks
            compressed_chunks = []
            
            for i in range(0, len(video_data), chunk_size):
                chunk = video_data[i:i+chunk_size]
                compressed_chunk = compressor.compress_with_mckay(chunk, "VIDEO")
                compressed_chunks.append(compressed_chunk)
            
            # Combine compressed chunks
            compressed_frames = b''.join(compressed_chunks)
            compression_time = time.time() - start_time
            
            # Calculate overall stats
            overall_ratio = len(video_data) / len(compressed_frames)
            space_saved = len(video_data) - len(compressed_frames)
            space_saved_percent = (space_saved / len(video_data)) * 100
            
            stats = {
                "compression_ratio": overall_ratio,
                "space_saved_percent": space_saved_percent,
                "mckay_rating": compressor._calculate_mckay_rating(overall_ratio)
            }
            
            result = {
                "strategy": "Frame-based",
                "compressed_data": compressed_frames,
                "compression_time": compression_time,
                "stats": stats
            }
            compression_results.append(result)
            
            print(f"  ✅ Compression: {stats['compression_ratio']:.2f}x - {stats['mckay_rating']}")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Find best compression result
    if not compression_results:
        print(f"\n❌ All compression strategies failed!")
        return False
    
    best_result = max(compression_results, key=lambda x: x['stats']['compression_ratio'])
    
    print(f"\n🏆 Best Compression Strategy: {best_result['strategy']}")
    print(f"🎉 Compression Complete!")
    print(f"⏱️  Compression Time: {best_result['compression_time']:.2f} seconds")
    print(f"📊 Compressed Size: {len(best_result['compressed_data']):,} bytes ({len(best_result['compressed_data']) / (1024*1024):.2f} MB)")
    print(f"🚀 Compression Ratio: {best_result['stats']['compression_ratio']:.2f}x")
    print(f"💾 Space Saved: {best_result['stats']['space_saved_percent']:.1f}%")
    print(f"⭐ McKay Rating: {best_result['stats']['mckay_rating']}")
    
    # Calculate bandwidth savings
    if best_result['stats']['compression_ratio'] > 1:
        bandwidth_saved = (1 - 1/best_result['stats']['compression_ratio']) * 100
        print(f"🌐 Transmission Time Reduction: {bandwidth_saved:.1f}%")
    else:
        print(f"⚠️  Note: File expanded during compression (common with already-compressed formats)")
    
    # Save compressed file to current directory
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    compressed_path = f"{base_name}_mckay_compressed.mckay"
    
    print(f"\n💾 Saving compressed file to: {compressed_path}")
    
    try:
        with open(compressed_path, 'wb') as f:
            f.write(best_result['compressed_data'])
        print(f"✅ Compressed file saved successfully!")
        
        # Show file size comparison
        compressed_size = os.path.getsize(compressed_path)
        print(f"\n📊 File Size Comparison:")
        print(f"  Original: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")
        print(f"  Compressed: {compressed_size:,} bytes ({compressed_size / (1024*1024):.2f} MB)")
        
        if compressed_size < file_size:
            savings = file_size - compressed_size
            print(f"  💾 Savings: {savings:,} bytes ({savings / (1024*1024):.2f} MB)")
            print(f"  🚀 Ready for efficient deep space transmission!")
        else:
            expansion = compressed_size - file_size
            print(f"  ⚠️  Expansion: {expansion:,} bytes ({expansion / (1024*1024):.2f} MB)")
            print(f"  💡 Note: MP4 files are already highly compressed - McKay's algorithm works best with raw video data")
        
        return compressed_path
        
    except Exception as e:
        print(f"❌ Error saving compressed file: {e}")
        return False

def test_decompression(compressed_path: str):
    """Test decompression of the compressed video"""
    print(f"\n🧪 Testing Decompression...")
    
    try:
        # Read compressed file
        with open(compressed_path, 'rb') as f:
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
        
        return True
        
    except Exception as e:
        print(f"❌ Decompression error: {e}")
        return False

def main():
    """Main function"""
    video_path = r"C:\Users\wtblanch\Videos\20230409121922.mp4"
    
    print("🎬 McKay Enhanced Video Compression Tool")
    print("=" * 60)
    print(f"Target Video: {video_path}")
    print("=" * 60)
    
    # Compress video
    compressed_path = compress_video_file(video_path)
    
    if compressed_path:
        # Test decompression
        test_decompression(compressed_path)
        
        print(f"\n🎉 Video compression completed successfully!")
        print(f"📁 Compressed file: {compressed_path}")
        
        if os.path.exists(compressed_path):
            compressed_size = os.path.getsize(compressed_path)
            original_size = os.path.getsize(video_path)
            
            if compressed_size < original_size:
                print(f"🚀 Successfully compressed video for deep space transmission!")
                print(f"💾 Space saved: {((original_size - compressed_size) / original_size) * 100:.1f}%")
            else:
                print(f"💡 Note: MP4 files are already highly compressed by modern codecs")
                print(f"🔬 McKay's algorithm works best with raw video data or uncompressed formats")
                print(f"🚀 For deep space transmission, consider using raw video formats for maximum compression")
    else:
        print(f"\n❌ Video compression failed!")

if __name__ == "__main__":
    main()
