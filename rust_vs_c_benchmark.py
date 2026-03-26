#!/usr/bin/env python3
"""
Benchmark comparison between Rust and C implementations of ASTRAL compression algorithms.
"""

import time
import numpy as np
import struct
import ctypes
import os

# Import Rust extension
try:
    import astral_compress as ac

    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    ac = None

# Load C library
try:
    # Compile C code if needed
    if not os.path.exists("c_benchmark.so"):
        os.system("gcc -shared -fPIC -O3 c_benchmark.c -lzstd -o c_benchmark.so")

    c_lib = ctypes.CDLL("./c_benchmark.so")
    C_AVAILABLE = True

    # Define function signatures
    c_lib.compress_telemetry_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ]
    c_lib.compress_telemetry_c.restype = ctypes.c_size_t

    c_lib.decompress_telemetry_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ]
    c_lib.decompress_telemetry_c.restype = ctypes.c_size_t

    c_lib.compress_binary_float_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ]
    c_lib.compress_binary_float_c.restype = ctypes.c_size_t

    c_lib.decompress_binary_float_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ]
    c_lib.decompress_binary_float_c.restype = ctypes.c_size_t

    c_lib.compress_text_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ]
    c_lib.compress_text_c.restype = ctypes.c_size_t

    c_lib.decompress_text_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
    ]
    c_lib.decompress_text_c.restype = ctypes.c_size_t

except (ImportError, OSError):
    C_AVAILABLE = False
    c_lib = None


def generate_telemetry_data(n_samples: int, n_channels: int, seed: int = 42) -> bytes:
    """Generate test telemetry data."""
    np.random.seed(seed)
    data = np.random.randn(n_samples * n_channels).astype(np.float32)
    # Add some structure to make it more compressible
    for ch in range(n_channels):
        # Add sinusoidal component
        t = np.linspace(0, 4 * np.pi, n_samples)
        data[ch::n_channels] += 2.0 * np.sin(t) + 0.5 * np.cos(2 * t)
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
        text += " ".join(np.random.choice(words, size=10)) + ". "
    return text.encode("utf-8")[:size]


def benchmark_telemetry(n_samples: int, n_channels: int, iterations: int = 10) -> dict:
    """Benchmark telemetry compression."""
    data = generate_telemetry_data(n_samples, n_channels)
    results = {}

    print(f"Benchmarking telemetry: {n_samples} samples, {n_channels} channels")

    # Rust implementation
    if RUST_AVAILABLE:
        rust_times = []
        for _ in range(iterations):
            start = time.time()
            compressed = ac.compress_telemetry(data, n_channels)
            ac.decompress_telemetry(compressed, len(data), n_channels)
            rust_times.append(time.time() - start)

        rust_avg = np.mean(rust_times)
        compression_ratio = len(data) / len(compressed)
        results["rust"] = {
            "time": rust_avg,
            "compression_ratio": compression_ratio,
            "throughput": len(data) / rust_avg / 1024 / 1024,  # MB/s
        }
        print(".3f")

    # C implementation
    if C_AVAILABLE:
        c_times = []
        for _ in range(iterations):
            # Convert data to C types
            data_array = (ctypes.c_uint8 * len(data))(*data)
            output_ptr = ctypes.POINTER(ctypes.c_uint8)()

            start = time.time()
            compressed_size = c_lib.compress_telemetry_c(
                data_array, len(data), n_channels, ctypes.byref(output_ptr)
            )
            if compressed_size > 0:
                compressed_data = bytes(
                    ctypes.cast(
                        output_ptr, ctypes.POINTER(ctypes.c_uint8 * compressed_size)
                    ).contents
                )

                # Decompress
                comp_array = (ctypes.c_uint8 * compressed_size)(*compressed_data)
                decomp_output_ptr = ctypes.POINTER(ctypes.c_uint8)()
                c_lib.decompress_telemetry_c(
                    comp_array,
                    compressed_size,
                    len(data),
                    n_channels,
                    ctypes.byref(decomp_output_ptr),
                )

                # Free memory
                # Note: In real C code you'd free these, but for simplicity we'll skip

            c_times.append(time.time() - start)

        c_avg = np.mean(c_times)
        compression_ratio = len(data) / compressed_size if compressed_size > 0 else 0
        results["c"] = {
            "time": c_avg,
            "compression_ratio": compression_ratio,
            "throughput": len(data) / c_avg / 1024 / 1024,  # MB/s
        }
        print(".3f")

    return results


def benchmark_binary_float(data_size: int, iterations: int = 10) -> dict:
    """Benchmark binary float compression."""
    data = generate_binary_float_data(data_size)
    results = {}

    print(f"Benchmarking binary float: {data_size} bytes")

    # Rust implementation
    if RUST_AVAILABLE:
        rust_times = []
        for _ in range(iterations):
            start = time.time()
            compressed = ac.compress_binary_float(data)
            ac.decompress_binary_float(compressed, len(data))
            rust_times.append(time.time() - start)

        rust_avg = np.mean(rust_times)
        compression_ratio = len(data) / len(compressed)
        results["rust"] = {
            "time": rust_avg,
            "compression_ratio": compression_ratio,
            "throughput": len(data) / rust_avg / 1024 / 1024,  # MB/s
        }
        print(".3f")

    # C implementation
    if C_AVAILABLE:
        c_times = []
        for _ in range(iterations):
            data_array = (ctypes.c_uint8 * len(data))(*data)
            output_ptr = ctypes.POINTER(ctypes.c_uint8)()

            start = time.time()
            compressed_size = c_lib.compress_binary_float_c(
                data_array, len(data), ctypes.byref(output_ptr)
            )
            if compressed_size > 0:
                compressed_data = bytes(
                    ctypes.cast(
                        output_ptr, ctypes.POINTER(ctypes.c_uint8 * compressed_size)
                    ).contents
                )

                # Decompress
                comp_array = (ctypes.c_uint8 * compressed_size)(*compressed_data)
                decomp_output_ptr = ctypes.POINTER(ctypes.c_uint8)()
                c_lib.decompress_binary_float_c(
                    comp_array,
                    compressed_size,
                    len(data),
                    ctypes.byref(decomp_output_ptr),
                )

            c_times.append(time.time() - start)

        c_avg = np.mean(c_times)
        results["c"] = {
            "time": c_avg,
            "compression_ratio": len(data) / compressed_size if compressed_size > 0 else 0,
            "throughput": len(data) / c_avg / 1024 / 1024,  # MB/s
        }
        print(".3f")

    return results


def benchmark_text(data_size: int, iterations: int = 10) -> dict:
    """Benchmark text compression."""
    data = generate_text_data(data_size)
    results = {}

    print(f"Benchmarking text: {data_size} bytes")

    # Rust implementation
    if RUST_AVAILABLE:
        rust_times = []
        for _ in range(iterations):
            start = time.time()
            compressed = ac.compress_text(data)
            ac.decompress_text(compressed)
            rust_times.append(time.time() - start)

        rust_avg = np.mean(rust_times)
        results["rust"] = {
            "time": rust_avg,
            "compression_ratio": len(data) / len(compressed),
            "throughput": len(data) / rust_avg / 1024 / 1024,  # MB/s
        }
        print(".3f")

    # C implementation
    if C_AVAILABLE:
        c_times = []
        for _ in range(iterations):
            data_array = (ctypes.c_uint8 * len(data))(*data)
            output_ptr = ctypes.POINTER(ctypes.c_uint8)()

            start = time.time()
            compressed_size = c_lib.compress_text_c(
                data_array, len(data), ctypes.byref(output_ptr)
            )
            if compressed_size > 0:
                compressed_data = bytes(
                    ctypes.cast(
                        output_ptr, ctypes.POINTER(ctypes.c_uint8 * compressed_size)
                    ).contents
                )

                # Decompress
                comp_array = (ctypes.c_uint8 * compressed_size)(*compressed_data)
                decomp_output_ptr = ctypes.POINTER(ctypes.c_uint8)()
                c_lib.decompress_text_c(
                    comp_array, compressed_size, ctypes.byref(decomp_output_ptr)
                )

            c_times.append(time.time() - start)

        c_avg = np.mean(c_times)
        compression_ratio = len(data) / compressed_size if compressed_size > 0 else 0
        results["c"] = {
            "time": c_avg,
            "compression_ratio": compression_ratio,
            "throughput": len(data) / c_avg / 1024 / 1024,  # MB/s
        }
        print(".3f")

    return results


def main():
    print("ASTRAL Compression: Rust vs C Performance Comparison")
    print("=" * 60)

    if not RUST_AVAILABLE:
        print("WARNING: Rust extension not available")
    if not C_AVAILABLE:
        print("WARNING: C library not available")

    # Test configurations
    configs = [
        # Telemetry: (n_samples, n_channels)
        ("telemetry_small", {"n_samples": 1000, "n_channels": 4}),
        ("telemetry_medium", {"n_samples": 10000, "n_channels": 8}),
        ("telemetry_large", {"n_samples": 50000, "n_channels": 16}),
        # Binary float: data_size
        ("binary_float_small", {"data_size": 40000}),  # 10k floats
        ("binary_float_medium", {"data_size": 400000}),  # 100k floats
        ("binary_float_large", {"data_size": 2000000}),  # 500k floats
        # Text: data_size
        ("text_small", {"data_size": 10000}),
        ("text_medium", {"data_size": 100000}),
        ("text_large", {"data_size": 500000}),
    ]

    all_results = {}

    for config_name, params in configs:
        print(f"\n--- {config_name.upper()} ---")

        if "n_samples" in params:
            results = benchmark_telemetry(params["n_samples"], params["n_channels"])
        elif "data_size" in params:
            if "telemetry" in config_name:
                continue  # Skip, handled above
            elif "binary_float" in config_name:
                results = benchmark_binary_float(params["data_size"])
            else:  # text
                results = benchmark_text(params["data_size"])

        all_results[config_name] = results

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for config_name, results in all_results.items():
        print(f"\n{config_name.upper()}:")
        if "rust" in results and "c" in results:
            rust_time = results["rust"]["time"]
            c_time = results["c"]["time"]

            print(f"  Rust time: {rust_time:.3f}s")
            print(f"  C time: {c_time:.1f}s")
        elif "rust" in results:
            print("  Only Rust available")
        elif "c" in results:
            print("  Only C available")
        else:
            print("  No implementations available")


if __name__ == "__main__":
    main()
