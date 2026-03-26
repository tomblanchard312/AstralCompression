#!/usr/bin/env python3
"""
Compare McKay compression vs standard compression (zstd/LZMA)
"""

import time
import numpy as np
import struct
import lzma
import zstandard as zstd

# Import Rust extension
try:
    import astral_compress as ac

    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    ac = None


def generate_telemetry_data(n_samples: int, n_channels: int, seed: int = 42) -> bytes:
    """Generate test telemetry data with structure."""
    np.random.seed(seed)
    data = np.random.randn(n_samples * n_channels).astype(np.float32)

    # Add structured components
    for ch in range(n_channels):
        t = np.linspace(0, 4 * np.pi, n_samples)
        data[ch::n_channels] += 2.0 * np.sin(t) + 0.5 * np.cos(2 * t)
        data[ch::n_channels] += 0.1 * np.random.randn(n_samples)

    return b"".join(struct.pack(">f", x) for x in data)


def generate_binary_float_data(size: int, seed: int = 42) -> bytes:
    """Generate test binary float data."""
    np.random.seed(seed)
    data = np.random.randn(size // 4).astype(np.float32)
    return b"".join(struct.pack(">f", x) for x in data)


def generate_text_data(size: int) -> bytes:
    """Generate test text data."""
    words = [
        "satellite",
        "telemetry",
        "nominal",
        "battery",
        "temperature",
        "attitude",
        "command",
        "systems",
        "payload",
        "critical",
        "warning",
        "anomaly",
        "downlink",
        "uplink",
        "interface",
    ]
    text = ""
    while len(text.encode("utf-8")) < size:
        text += " ".join(np.random.choice(words, size=np.random.randint(5, 15))) + ". "
    return text.encode("utf-8")[:size]


def benchmark_compression(data: bytes, data_type: str, iterations: int = 3) -> dict:
    """Benchmark McKay vs standard compression."""
    results = {}

    print(f"Benchmarking {data_type}: {len(data)} bytes")

    # McKay compression (Rust)
    if RUST_AVAILABLE:
        mckay_times = []
        mckay_ratios = []

        for _ in range(iterations):
            start = time.time()

            if data_type == "telemetry":
                compressed = ac.compress_telemetry(data, 4)  # 4 channels
            elif data_type == "binary_float":
                compressed = ac.compress_binary_float(data)
            else:  # text
                compressed = ac.compress_text(data)

            elapsed = time.time() - start
            mckay_times.append(elapsed)
            mckay_ratios.append(len(data) / len(compressed))

        results["mckay"] = {
            "time": np.mean(mckay_times),
            "ratio": np.mean(mckay_ratios),
            "throughput": len(data) / np.mean(mckay_times) / 1024 / 1024,  # MB/s
        }
        print(".3f")

    # Standard zstd compression
    zstd_times = []
    zstd_ratios = []

    compressor = zstd.ZstdCompressor(level=9)
    for _ in range(iterations):
        start = time.time()
        compressed = compressor.compress(data)
        elapsed = time.time() - start
        zstd_times.append(elapsed)
        zstd_ratios.append(len(data) / len(compressed))

    results["zstd"] = {
        "time": np.mean(zstd_times),
        "ratio": np.mean(zstd_ratios),
        "throughput": len(data) / np.mean(zstd_times) / 1024 / 1024,  # MB/s
    }
    print(".3f")

    # Standard LZMA compression
    lzma_times = []
    lzma_ratios = []

    for _ in range(iterations):
        start = time.time()
        compressed = lzma.compress(data, preset=9)
        elapsed = time.time() - start
        lzma_times.append(elapsed)
        lzma_ratios.append(len(data) / len(compressed))

    results["lzma"] = {
        "time": np.mean(lzma_times),
        "ratio": np.mean(lzma_ratios),
        "throughput": len(data) / np.mean(lzma_times) / 1024 / 1024,  # MB/s
    }
    print(".3f")

    return results


def main():
    print("McKay vs Standard Compression Comparison")
    print("=" * 50)

    if not RUST_AVAILABLE:
        print("ERROR: Rust extension not available!")
        return

    # Test datasets
    datasets = [
        ("telemetry", generate_telemetry_data(10000, 4)),  # 160KB
        ("binary_float", generate_binary_float_data(100000)),  # 400KB
        ("text", generate_text_data(200000)),  # ~200KB
    ]

    all_results = {}

    for data_type, data in datasets:
        print(f"\n--- {data_type.upper()} ---")
        results = benchmark_compression(data, data_type)
        all_results[data_type] = results

    # Summary
    print("\n" + "=" * 50)
    print("COMPRESSION RATIO COMPARISON")
    print("=" * 50)

    for data_type, results in all_results.items():
        print(f"\n{data_type.upper()}:")
        if "mckay" in results:
            mckay_ratio = results["mckay"]["ratio"]
            zstd_ratio = results["zstd"]["ratio"]
            lzma_ratio = results["lzma"]["ratio"]

            print(".2f")
            print(".2f")
            print(".2f")
            print(".1f")
            print(".1f")

            if mckay_ratio > zstd_ratio and mckay_ratio > lzma_ratio:
                print("  ✅ McKay WINS - Better compression!")
            elif mckay_ratio > zstd_ratio:
                print("  ⚡ McKay beats Zstd, LZMA still better")
            else:
                print("  ❌ Standard compression better")

    print("\n" + "=" * 50)
    print("PERFORMANCE COMPARISON")
    print("=" * 50)

    for data_type, results in all_results.items():
        print(f"\n{data_type.upper()}:")
        if "mckay" in results:
            print(".1f")
            print(".1f")
            print(".1f")


if __name__ == "__main__":
    main()
