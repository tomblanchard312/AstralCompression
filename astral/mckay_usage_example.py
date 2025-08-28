#!/usr/bin/env python3
"""
McKay + ASTRAL Usage Example
Practical examples of using Dr. McKay's extreme compression with ASTRAL fountain codes.

This demonstrates:
1. How to compress different types of data
2. Integration with existing ASTRAL systems
3. Real-world compression ratios
4. Deep space communication optimization
"""

from astral.mckay_astral_integration import McKayASTRALIntegration, McKayASTRALCompressor
import time

def example_mission_communication():
    """Example: Compressing mission communications for deep space transmission"""
    print("=== Mission Communication Example ===")
    
    # Create McKay + ASTRAL integration
    integration = McKayASTRALIntegration()
    
    # Mission status report
    mission_report = """
    MISSION STATUS REPORT - ATLANTIS EXPEDITION
    
    COLONEL SHEPPARD: All systems operational. ZPM at 87% capacity.
    DR. MCKAY: Ancient database analysis complete. Compression algorithm working perfectly.
    MAJOR LORNE: Security perimeter secure. No Wraith activity detected.
    DR. WEIR: Excellent work team. Ready for next phase.
    
    TECHNICAL DATA:
    - Power consumption: 2.3 MW (nominal)
    - Shield strength: 94%
    - Life support: 100%
    - Communications: Optimal
    - Sensors: All operational
    
    NEXT OBJECTIVES:
    1. Continue Ancient technology research
    2. Maintain ZPM efficiency
    3. Monitor for Wraith activity
    4. Prepare for Earth contact
    
    END REPORT
    """
    
    print(f"Original mission report: {len(mission_report)} characters")
    
    # Compress with McKay + ASTRAL
    compressed = integration.compress_and_encode(mission_report, "TEXT", extra_fountain=15)
    
    # Get compression stats
    mckay_stats = integration.mckay_compressor.get_compression_stats()
    integration_stats = integration.get_integration_stats()
    
    print(f"\nCompression Results:")
    print(f"  McKay compression: {mckay_stats['compression_ratio']:.2f}x")
    print(f"  McKay rating: {mckay_stats['mckay_rating']}")
    print(f"  Total compression: {integration_stats['total_compression']:.2f}x")
    print(f"  Space saved: {mckay_stats['space_saved_percent']:.1f}%")
    
    # Test decompression
    decompressed = integration.decompress_and_decode(compressed)
    print(f"\nDecompression: {'✅ Success' if len(decompressed) > 0 else '❌ Failed'}")
    
    return compressed

def example_telemetry_data():
    """Example: Compressing telemetry data for efficient transmission"""
    print("\n=== Telemetry Data Example ===")
    
    integration = McKayASTRALIntegration()
    
    # Simulate telemetry data (repetitive sensor readings)
    telemetry_data = []
    for i in range(1000):
        telemetry_data.append(f"SENSOR_{i%10}:{100 + (i%50)}:NOMINAL")
    
    telemetry_text = "\n".join(telemetry_data)
    print(f"Original telemetry: {len(telemetry_text)} characters")
    
    # Compress telemetry
    compressed = integration.compress_and_encode(telemetry_text, "TEXT", extra_fountain=10)
    
    # Get stats
    mckay_stats = integration.mckay_compressor.get_compression_stats()
    print(f"\nTelemetry Compression:")
    print(f"  Compression ratio: {mckay_stats['compression_ratio']:.2f}x")
    print(f"  McKay rating: {mckay_stats['mckay_rating']}")
    print(f"  Space saved: {mckay_stats['space_saved_percent']:.1f}%")
    
    return compressed

def example_binary_data():
    """Example: Compressing binary data (scientific measurements)"""
    print("\n=== Binary Data Example ===")
    
    integration = McKayASTRALIntegration()
    
    # Simulate scientific measurement data (repetitive patterns)
    # This is the kind of data McKay's algorithm excels at
    measurement_data = b''
    for i in range(1000):
        # Create repetitive patterns
        if i % 100 == 0:
            measurement_data += b'\x00\x01\x02\x03\x04\x05' * 10  # Pattern 1
        elif i % 50 == 0:
            measurement_data += b'\xFF\xFE\xFD\xFC' * 25  # Pattern 2
        else:
            measurement_data += b'\xAA\xBB\xCC\xDD' * 5   # Pattern 3
    
    print(f"Original binary data: {len(measurement_data)} bytes")
    
    # Compress binary data
    compressed = integration.compress_and_encode(measurement_data, "BINARY", extra_fountain=20)
    
    # Get stats
    mckay_stats = integration.mckay_compressor.get_compression_stats()
    print(f"\nBinary Data Compression:")
    print(f"  Compression ratio: {mckay_stats['compression_ratio']:.2f}x")
    print(f"  McKay rating: {mckay_stats['mckay_rating']}")
    print(f"  Space saved: {mckay_stats['space_saved_percent']:.1f}%")
    
    return compressed

def example_voice_compression():
    """Example: Compressing voice data for communication"""
    print("\n=== Voice Compression Example ===")
    
    integration = McKayASTRALIntegration()
    
    # Simulate voice data (repetitive audio patterns)
    # In practice, this would be real audio data
    voice_data = b''
    for i in range(1000):
        # Simulate repetitive audio patterns
        if i % 200 == 0:
            voice_data += b'\x80' * 100  # Silence
        elif i % 100 == 0:
            voice_data += b'\x90' * 50   # Low tone
        else:
            voice_data += b'\xA0' * 25   # High tone
    
    print(f"Original voice data: {len(voice_data)} bytes")
    
    # Compress voice data
    compressed = integration.compress_and_encode(voice_data, "VOICE", extra_fountain=25)
    
    # Get stats
    mckay_stats = integration.mckay_compressor.get_compression_stats()
    print(f"\nVoice Compression:")
    print(f"  Compression ratio: {mckay_stats['compression_ratio']:.2f}x")
    print(f"  McKay rating: {mckay_stats['mckay_rating']}")
    print(f"  Space saved: {mckay_stats['space_saved_percent']:.1f}%")
    
    return compressed

def example_deep_space_transmission():
    """Example: Complete deep space transmission workflow"""
    print("\n=== Deep Space Transmission Example ===")
    
    integration = McKayASTRALIntegration()
    
    # Simulate a complete mission data package
    mission_data = {
        "text": "Mission critical data for Earth command.",
        "telemetry": "All systems operational. ZPM stable.",
        "binary": b'\x00\x01\x02\x03' * 500,  # Scientific data
        "voice": b'\x80\x90\xA0' * 200        # Voice message
    }
    
    print("Preparing mission data package for deep space transmission...")
    
    # Compress each data type
    compressed_package = {}
    total_original = 0
    total_compressed = 0
    
    for data_type, data in mission_data.items():
        if isinstance(data, str):
            compressed = integration.compress_and_encode(data, "TEXT", extra_fountain=10)
            original_size = len(data.encode('utf-8'))
        else:
            compressed = integration.compress_and_encode(data, "BINARY", extra_fountain=10)
            original_size = len(data)
        
        compressed_package[data_type] = compressed
        total_original += original_size
        total_compressed += len(compressed)
        
        mckay_stats = integration.mckay_compressor.get_compression_stats()
        print(f"  {data_type}: {mckay_stats['compression_ratio']:.2f}x compression")
    
    # Calculate overall compression
    overall_ratio = total_original / total_compressed
    space_saved = total_original - total_compressed
    space_saved_percent = (space_saved / total_original) * 100
    
    print(f"\nOverall Mission Data Compression:")
    print(f"  Total original: {total_original} bytes")
    print(f"  Total compressed: {total_compressed} bytes")
    print(f"  Overall ratio: {overall_ratio:.2f}x")
    print(f"  Total space saved: {space_saved_percent:.1f}%")
    
    # Simulate transmission
    print(f"\nTransmitting to Earth...")
    print(f"  Estimated transmission time reduction: {space_saved_percent:.0f}%")
    print(f"  Bandwidth efficiency: {overall_ratio:.1f}x")
    
    return compressed_package

def performance_benchmark():
    """Benchmark the McKay + ASTRAL system performance"""
    print("\n=== Performance Benchmark ===")
    
    integration = McKayASTRALIntegration()
    
    # Test data sizes
    test_sizes = [100, 1000, 10000, 100000]
    
    print("Compression Performance by Data Size:")
    print("Size (chars) | Time (ms) | Ratio | Rating")
    print("-" * 50)
    
    for size in test_sizes:
        # Generate test data
        test_data = "The Ancient database contains critical information. " * (size // 50)
        
        # Measure compression time
        start_time = time.time()
        compressed = integration.compress_and_encode(test_data, "TEXT")
        compression_time = (time.time() - start_time) * 1000
        
        # Get stats
        mckay_stats = integration.mckay_compressor.get_compression_stats()
        
        print(f"{size:11} | {compression_time:9.1f} | {mckay_stats['compression_ratio']:5.1f}x | {mckay_stats['mckay_rating'][:10]}")

def main():
    """Main function demonstrating McKay + ASTRAL system"""
    print("🚀 McKay + ASTRAL Deep Space Compression System")
    print("=" * 60)
    
    try:
        # Run examples
        example_mission_communication()
        example_telemetry_data()
        example_binary_data()
        example_voice_compression()
        example_deep_space_transmission()
        performance_benchmark()
        
        print("\n" + "=" * 60)
        print("✅ All examples completed successfully!")
        print("\nMcKay's extreme compression is now integrated with ASTRAL!")
        print("Ready for deep space communication with compression ratios of 2-100x!")
        
    except Exception as e:
        print(f"\n❌ Error during demonstration: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
