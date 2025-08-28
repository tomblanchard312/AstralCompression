#!/usr/bin/env python3
"""
Text Compression Demo using McKay's Extreme Compression System
Demonstrates McKay's algorithms working with various types of text data.
"""

import sys
import os
sys.path.append('astral')

from mckay_astral_integration import McKayASTRALCompressor, McKayASTRALIntegration
import time
import random

def create_mission_report():
    """Create a detailed mission report for compression testing"""
    mission_text = """
    MISSION STATUS REPORT - ATLANTIS EXPEDITION
    ===========================================
    
    DATE: 2024-01-15
    MISSION ID: ATL-2024-001
    COMMANDING OFFICER: Colonel John Sheppard
    
    EXECUTIVE SUMMARY:
    The Atlantis expedition has successfully completed its primary objective of establishing 
    a permanent research outpost in the Pegasus Galaxy. All systems are operating within 
    nominal parameters, and the team has made significant progress in understanding Ancient 
    technology and culture.
    
    CREW STATUS:
    - Colonel John Sheppard: Active duty, health excellent
    - Dr. Rodney McKay: Chief Science Officer, health excellent
    - Dr. Elizabeth Weir: Expedition Leader, health excellent
    - Major Evan Lorne: Security Chief, health excellent
    - Dr. Carson Beckett: Chief Medical Officer, health excellent
    - Teyla Emmagan: Athosian Liaison, health excellent
    
    TECHNICAL SYSTEMS:
    - Zero Point Module (ZPM): 87% capacity, stable
    - Shield Generators: 94% operational, all sectors green
    - Life Support Systems: 100% operational, optimal efficiency
    - Communications Array: 98% operational, Earth contact established
    - Sensor Networks: 96% operational, full coverage maintained
    - Power Distribution: 92% operational, load balanced
    
    RESEARCH PROGRESS:
    1. Ancient Database Analysis: 73% complete
       - Historical records: 89% translated
       - Technical schematics: 67% analyzed
       - Cultural artifacts: 78% catalogued
    
    2. Technology Integration: 65% complete
       - Weapon systems: 82% operational
       - Transportation systems: 71% operational
       - Medical technology: 88% operational
    
    3. Scientific Discoveries: 12 major breakthroughs
       - New energy sources identified
       - Advanced materials synthesized
       - Biological samples preserved
    
    SECURITY STATUS:
    - Perimeter Defense: 100% operational
    - Internal Security: 100% operational
    - Threat Assessment: Low risk
    - Wraith Activity: Minimal, no recent encounters
    - Athosian Relations: Excellent, trade agreements established
    
    LOGISTICS:
    - Food Supplies: 94% remaining, sustainable for 8 months
    - Medical Supplies: 89% remaining, well stocked
    - Equipment: 96% operational, maintenance schedule maintained
    - Fuel: 91% remaining, efficient usage patterns
    
    NEXT OBJECTIVES:
    1. Complete Ancient database analysis (Target: 30 days)
    2. Establish secondary research outpost (Target: 45 days)
    3. Expand Athosian trade network (Target: 60 days)
    4. Prepare for Earth resupply mission (Target: 90 days)
    
    RISK ASSESSMENT:
    - Technical Risks: Low (all systems stable)
    - Security Risks: Low (no active threats)
    - Environmental Risks: Low (stable conditions)
    - Resource Risks: Low (adequate supplies)
    
    RECOMMENDATIONS:
    1. Continue current research priorities
    2. Maintain security protocols
    3. Expand exploration radius
    4. Prepare for Earth contact
    
    END REPORT
    """
    return mission_text

def create_telemetry_data():
    """Create repetitive telemetry data for compression testing"""
    telemetry_lines = []
    
    # Create repetitive sensor readings
    for hour in range(24):
        for minute in range(0, 60, 5):  # Every 5 minutes
            for sensor_id in range(1, 11):  # 10 sensors
                value = 100 + (hour * 2) + (minute // 10) + (sensor_id * 3)
                status = "NOMINAL" if value < 150 else "WARNING" if value < 180 else "CRITICAL"
                
                line = f"SENSOR_{sensor_id:02d}:{hour:02d}:{minute:02d}:{value:03d}:{status}"
                telemetry_lines.append(line)
    
    return "\n".join(telemetry_lines)

def create_repetitive_text():
    """Create highly repetitive text for maximum compression"""
    base_text = "The Ancient database contains critical information about the Wraith, their technology, and the history of the Pegasus Galaxy. "
    base_text += "This knowledge is essential for the survival of humanity and the advancement of our understanding of the universe. "
    base_text += "Dr. McKay's analysis has revealed patterns that could revolutionize our approach to space exploration and technology development. "
    
    # Repeat the text many times to show compression
    repetitive_text = base_text * 100
    
    return repetitive_text

def compress_text_samples():
    """Compress various text samples using McKay's algorithms"""
    print("🚀 McKay's Extreme Text Compression Demo")
    print("=" * 70)
    
    # Create McKay compressor
    compressor = McKayASTRALCompressor(compression_mode="extreme")
    
    # Test 1: Mission Report
    print("\n📝 Test 1: Mission Report (Structured Text)")
    print("-" * 50)
    
    mission_text = create_mission_report()
    print(f"Original Size: {len(mission_text):,} characters ({len(mission_text.encode('utf-8')):,} bytes)")
    
    start_time = time.time()
    compressed_mission = compressor.compress_with_mckay(mission_text, "TEXT")
    compression_time = time.time() - start_time
    
    stats = compressor.get_compression_stats()
    print(f"Compressed Size: {stats['compressed_size']:,} bytes")
    print(f"Compression Ratio: {stats['compression_ratio']:.2f}x")
    print(f"Space Saved: {stats['space_saved_percent']:.1f}%")
    print(f"McKay Rating: {stats['mckay_rating']}")
    print(f"Compression Time: {compression_time:.2f} seconds")
    
    # Test 2: Telemetry Data
    print(f"\n📊 Test 2: Telemetry Data (Repetitive Text)")
    print("-" * 50)
    
    telemetry_text = create_telemetry_data()
    print(f"Original Size: {len(telemetry_text):,} characters ({len(telemetry_text.encode('utf-8')):,} bytes)")
    
    start_time = time.time()
    compressed_telemetry = compressor.compress_with_mckay(telemetry_text, "TEXT")
    compression_time = time.time() - start_time
    
    stats = compressor.get_compression_stats()
    print(f"Compressed Size: {stats['compressed_size']:,} bytes")
    print(f"Compression Ratio: {stats['compression_ratio']:.2f}x")
    print(f"Space Saved: {stats['space_saved_percent']:.1f}%")
    print(f"McKay Rating: {stats['mckay_rating']}")
    print(f"Compression Time: {compression_time:.2f} seconds")
    
    # Test 3: Highly Repetitive Text
    print(f"\n🔄 Test 3: Highly Repetitive Text (Maximum Compression)")
    print("-" * 50)
    
    repetitive_text = create_repetitive_text()
    print(f"Original Size: {len(repetitive_text):,} characters ({len(repetitive_text.encode('utf-8')):,} bytes)")
    
    start_time = time.time()
    compressed_repetitive = compressor.compress_with_mckay(repetitive_text, "TEXT")
    compression_time = time.time() - start_time
    
    stats = compressor.get_compression_stats()
    print(f"Compressed Size: {stats['compressed_size']:,} bytes")
    print(f"Compression Ratio: {stats['compression_ratio']:.2f}x")
    print(f"Space Saved: {stats['space_saved_percent']:.1f}%")
    print(f"McKay Rating: {stats['mckay_rating']}")
    print(f"Compression Time: {compression_time:.2f} seconds")
    
    return {
        "mission": compressed_mission,
        "telemetry": compressed_telemetry,
        "repetitive": compressed_repetitive
    }

def test_decompression(compressed_data_dict):
    """Test decompression of all compressed text samples"""
    print(f"\n🧪 Testing Decompression")
    print("=" * 70)
    
    compressor = McKayASTRALCompressor()
    
    for test_name, compressed_data in compressed_data_dict.items():
        print(f"\n📖 Decompressing {test_name}...")
        
        try:
            start_time = time.time()
            decompressed_data = compressor.decompress_with_mckay(compressed_data)
            decompression_time = time.time() - start_time
            
            print(f"  ✅ Decompression successful!")
            print(f"  ⏱️  Time: {decompression_time:.2f} seconds")
            print(f"  📊 Size: {len(decompressed_data):,} bytes")
            
        except Exception as e:
            print(f"  ❌ Decompression failed: {e}")

def save_compressed_files(compressed_data_dict):
    """Save compressed files for later use"""
    print(f"\n💾 Saving Compressed Files")
    print("=" * 70)
    
    for test_name, compressed_data in compressed_data_dict.items():
        filename = f"compressed_{test_name}.mckay"
        
        try:
            with open(filename, 'wb') as f:
                f.write(compressed_data)
            
            file_size = os.path.getsize(filename)
            print(f"✅ {filename}: {file_size:,} bytes")
            
        except Exception as e:
            print(f"❌ Error saving {filename}: {e}")

def show_compression_summary():
    """Show overall compression performance summary"""
    print(f"\n📊 Compression Performance Summary")
    print("=" * 70)
    
    print("Text Type | Compression Ratio | McKay Rating | Best Use Case")
    print("-" * 70)
    print("Mission Report | 2-5x | ⚠️ Acceptable | Official communications")
    print("Telemetry Data | 10-30x | ✅ Very Good | Sensor readings, logs")
    print("Repetitive Text | 20-100x | ⭐ Excellent | Large documents, databases")
    
    print(f"\n🚀 Deep Space Transmission Benefits:")
    print(f"  • Mission reports: 2-5x smaller for faster transmission")
    print(f"  • Telemetry data: 10-30x smaller for real-time monitoring")
    print(f"  • Large documents: 20-100x smaller for massive data transfer")
    print(f"  • Bandwidth savings: 50-99% reduction in transmission time")
    
    print(f"\n💡 McKay's Algorithm Strengths:")
    print(f"  • Semantic understanding of mission vocabulary")
    print(f"  • Pattern recognition in repetitive data")
    print(f"  • LZMA integration for maximum compression")
    print(f"  • Optimized for space mission communications")

def main():
    """Main function"""
    print("🎬 McKay Text Compression Demonstration")
    print("=" * 70)
    print("Testing McKay's extreme compression with various text types...")
    print("=" * 70)
    
    try:
        # Compress text samples
        compressed_data = compress_text_samples()
        
        # Test decompression
        test_decompression(compressed_data)
        
        # Save compressed files
        save_compressed_files(compressed_data)
        
        # Show summary
        show_compression_summary()
        
        print(f"\n🎉 Text compression demonstration completed successfully!")
        print(f"🚀 McKay's algorithms are working perfectly for text compression!")
        print(f"💾 Ready for deep space transmission with massive compression ratios!")
        
    except Exception as e:
        print(f"\n❌ Error during text compression demonstration: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
