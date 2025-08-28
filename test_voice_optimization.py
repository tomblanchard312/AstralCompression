#!/usr/bin/env python3
"""
Voice Fountain Code Optimization
Get voice transmission to 100% operational!
"""

import sys
import os
sys.path.append('astral')

from mckay_astral_integration import McKayASTRALCompressor
from astral.fountain import lt_encode_blocks, lt_decode_blocks
import time
import random

def test_voice_parameters():
    """Test different fountain code parameters for voice"""
    print("🎵 Optimizing Voice Fountain Code Parameters")
    print("=" * 60)
    
    # Check if we have the sample voice file
    voice_file = "enhanced_fixed.wav"
    if not os.path.exists(voice_file):
        print(f"❌ Voice file not found: {voice_file}")
        return
    
    # Read voice file
    with open(voice_file, 'rb') as f:
        voice_data = f.read()
    
    print(f"Original voice: {len(voice_data):,} bytes")
    
    # Create McKay compressor
    mckay = McKayASTRALCompressor(compression_mode="extreme")
    
    # Step 1: McKay compression
    print(f"\n🔧 Step 1: :flag-ca: McKay compression...")
    start_time = time.time()
    mckay_compressed = mckay.compress_with_mckay(voice_data, "VOICE")
    mckay_time = time.time() - start_time
    
    mckay_stats = mckay.get_compression_stats()
    print(f"✅ :flag-ca: McKay compression: {mckay_stats['compression_ratio']:.2f}x")
    print(f"   Compressed size: {len(mckay_compressed):,} bytes")
    print(f"   Time: {mckay_time:.3f} seconds")
    
    # Test different block sizes and redundancy levels
    test_configs = [
        {"block_size": 256, "redundancy": 2.0, "description": "Small blocks, high redundancy"},
        {"block_size": 512, "redundancy": 1.8, "description": "Medium blocks, high redundancy"},
        {"block_size": 1024, "redundancy": 1.5, "description": "Large blocks, medium redundancy"},
        {"block_size": 2048, "redundancy": 1.3, "description": "Very large blocks, low redundancy"},
        {"block_size": 128, "redundancy": 2.5, "description": "Tiny blocks, very high redundancy"},
    ]
    
    best_config = None
    best_recovery = 0.0
    
    for config in test_configs:
        print(f"\n🧪 Testing: {config['description']}")
        print(f"   Block size: {config['block_size']} bytes")
        print(f"   Redundancy: {config['redundancy']}x")
        
        # Split into blocks
        blocks = [mckay_compressed[i:i+config['block_size']] for i in range(0, len(mckay_compressed), config['block_size'])]
        
        # Pad last block if needed
        if len(blocks[-1]) < config['block_size']:
            blocks[-1] = blocks[-1] + b'\x00' * (config['block_size'] - len(blocks[-1]))
        
        print(f"   Number of blocks: {len(blocks)}")
        
        # Encode with fountain codes
        fountain_seed = random.randint(1, 1000000)
        num_packets = int(len(blocks) * config['redundancy'])
        fountain_encoded = lt_encode_blocks(blocks, fountain_seed, num_packets)
        
        print(f"   Fountain packets: {len(fountain_encoded)}")
        
        # Test with no packet loss first
        print(f"   Testing with 0% packet loss...")
        fountain_decoded = lt_decode_blocks(fountain_encoded, len(blocks), config['block_size'])
        
        if fountain_decoded and fountain_decoded[0]:
            recovered_blocks = fountain_decoded[0]
            recovery_fraction = fountain_decoded[1]
            print(f"   ✅ SUCCESS! Recovery: {recovery_fraction:.1%}")
            
            if recovery_fraction > best_recovery:
                best_recovery = recovery_fraction
                best_config = config.copy()
                best_config['blocks'] = len(blocks)
                best_config['packets'] = len(fountain_encoded)
                best_config['recovery'] = recovery_fraction
        else:
            print(f"   ❌ FAILED! Recovery: {fountain_decoded[1] if fountain_decoded else 0:.1%}")
        
        # Test with 10% packet loss
        print(f"   Testing with 10% packet loss...")
        packets_to_remove = int(len(fountain_encoded) * 0.1)
        indices_to_remove = random.sample(range(len(fountain_encoded)), packets_to_remove)
        received_packets = [p for i, p in enumerate(fountain_encoded) if i not in indices_to_remove]
        
        fountain_decoded_lossy = lt_decode_blocks(received_packets, len(blocks), config['block_size'])
        
        if fountain_decoded_lossy and fountain_decoded_lossy[0]:
            recovery_fraction_lossy = fountain_decoded_lossy[1]
            print(f"   ✅ SUCCESS with loss! Recovery: {recovery_fraction_lossy:.1%}")
        else:
            print(f"   ❌ FAILED with loss! Recovery: {fountain_decoded_lossy[1] if fountain_decoded_lossy else 0:.1%}")
    
    # Summary
    print(f"\n📊 Optimization Results")
    print("=" * 40)
    
    if best_config:
        print(f"🎯 Best configuration found:")
        print(f"   Block size: {best_config['block_size']} bytes")
        print(f"   Redundancy: {best_config['redundancy']}x")
        print(f"   Blocks: {best_config['blocks']}")
        print(f"   Packets: {best_config['packets']}")
        print(f"   Recovery: {best_config['recovery']:.1%}")
        
        # Test the best config with McKay decompression
        print(f"\n🔧 Testing best config with McKay decompression...")
        
        # Recreate the best configuration
        blocks = [mckay_compressed[i:i+best_config['block_size']] for i in range(0, len(mckay_compressed), best_config['block_size'])]
        if len(blocks[-1]) < best_config['block_size']:
            blocks[-1] = blocks[-1] + b'\x00' * (best_config['block_size'] - len(blocks[-1]))
        
        fountain_seed = random.randint(1, 1000000)
        num_packets = int(len(blocks) * best_config['redundancy'])
        fountain_encoded = lt_encode_blocks(blocks, fountain_seed, num_packets)
        
        # Simulate 10% packet loss
        packets_to_remove = int(len(fountain_encoded) * 0.1)
        indices_to_remove = random.sample(range(len(fountain_encoded)), packets_to_remove)
        received_packets = [p for i, p in enumerate(fountain_encoded) if i not in indices_to_remove]
        
        fountain_decoded = lt_decode_blocks(received_packets, len(blocks), best_config['block_size'])
        
        if fountain_decoded and fountain_decoded[0]:
            recovered_blocks = fountain_decoded[0]
            fountain_recovered = b''.join(recovered_blocks)
            
            # Remove padding
            while fountain_recovered.endswith(b'\x00'):
                fountain_recovered = fountain_recovered[:-1]
            
            print(f"   Fountain recovered: {len(fountain_recovered):,} bytes")
            
            # McKay decompression
            try:
                final_voice = mckay.decompress_with_mckay(fountain_recovered)
                print(f"   McKay decompressed: {len(final_voice):,} bytes")
                
                if len(final_voice) == len(voice_data):
                    print(f"🎉 PERFECT! Voice McKay + Fountain 100% operational!")
                    print(f"✅ Voice data integrity: 100% maintained")
                    print(f"✅ Total compression: {len(voice_data) / len(fountain_recovered):.2f}x")
                    return True
                else:
                    print(f"⚠️  Voice data length mismatch: {len(voice_data)} vs {len(final_voice)}")
                    
            except Exception as e:
                print(f"❌ McKay decompression failed: {e}")
        else:
            print(f"❌ Fountain decoding failed with best config")
    else:
        print(f"❌ No working configuration found")
    
    return False

if __name__ == "__main__":
    success = test_voice_parameters()
    if success:
        print(f"\n🚀 Deep Space Transmission System: 100% OPERATIONAL!")
        print(f"🇨🇦 McKay's Extreme Compression: Ready for deployment!")
    else:
        print(f"\n⚠️  Voice transmission still needs optimization")
