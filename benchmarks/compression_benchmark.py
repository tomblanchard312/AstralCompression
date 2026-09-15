#!/usr/bin/env python3
"""
Compare GistLink's domain-aware compression against zstd and LZMA.

Every measurement here is verified: each codec must reproduce its input (or,
for lossy telemetry quantisation, reproduce it within the quantiser's error
bound) before its ratio is reported. Run it to reproduce the numbers quoted in
the README:

    python benchmarks/compression_benchmark.py
    python benchmarks/compression_benchmark.py --iterations 5

The GistLink column uses the Rust extension when it is built and the pure Python
implementation otherwise; the report says which.
"""
import sys

import os

# Run from anywhere: these live in benchmarks/ but exercise the package at the
# repository root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows consoles default to a legacy code page; these scripts print check
# marks, so force UTF-8 rather than dying with UnicodeEncodeError mid-report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import lzma
import struct
import time
import zlib

import numpy as np

from gistlink import compress as engine

try:
    import zstandard as _zstandard
except ImportError:  # pragma: no cover - benchmark-only dependency
    _zstandard = None

MB = 1024 * 1024


def generate_telemetry_data(n_samples: int, n_channels: int, seed: int = 42) -> bytes:
    """Structured multi-channel telemetry: smooth signal plus noise."""
    rng = np.random.default_rng(seed)
    data = rng.standard_normal(n_samples * n_channels).astype(np.float32) * 0.05
    for ch in range(n_channels):
        t = np.linspace(0, 4 * np.pi, n_samples)
        data[ch::n_channels] += (2.0 * np.sin(t) + 0.5 * np.cos(2 * t)).astype(
            np.float32
        )
    return struct.pack(f">{data.size}f", *data.tolist())


def generate_binary_float_data(size: int, seed: int = 42) -> bytes:
    rng = np.random.default_rng(seed)
    data = rng.standard_normal(size // 4).astype(np.float32)
    return struct.pack(f">{data.size}f", *data.tolist())


def generate_text_data(size: int, seed: int = 42) -> bytes:
    rng = np.random.default_rng(seed)
    words = [
        "satellite", "telemetry", "nominal", "battery", "temperature",
        "attitude", "command", "systems", "payload", "critical",
        "warning", "anomaly", "downlink", "uplink", "interface",
    ]
    parts = []
    total = 0
    while total < size:
        sentence = " ".join(rng.choice(words, size=int(rng.integers(5, 15)))) + ". "
        parts.append(sentence)
        total += len(sentence)
    return "".join(parts).encode("utf-8")[:size]


def _time_it(fn, data: bytes, iterations: int):
    """Return (best_seconds, output) for the fastest of `iterations` runs."""
    best = float("inf")
    out = b""
    for _ in range(iterations):
        start = time.perf_counter()
        out = fn(data)
        best = min(best, time.perf_counter() - start)
    return best, out


def _max_float_error(a: bytes, b: bytes) -> float:
    n = min(len(a), len(b)) // 4
    fa = struct.unpack(f">{n}f", a[: n * 4])
    fb = struct.unpack(f">{n}f", b[: n * 4])
    span = max(fa) - min(fa) or 1.0
    return max(abs(x - y) for x, y in zip(fa, fb)) / span


def benchmark(data: bytes, data_type: str, channels: int, iterations: int) -> dict:
    """Measure every codec on one dataset, verifying each reconstruction."""
    results = {}
    compress_type = {"telemetry": "TELEMETRY", "binary_float": "BINARY", "text": "TEXT"}[
        data_type
    ]

    def run_engine(d):
        return engine.compress(d, compress_type, channels=channels)

    seconds, compressed = _time_it(run_engine, data, iterations)
    restored = engine.decompress(compressed)
    if compress_type == "TELEMETRY":
        # Q12 quantisation is lossy by design: check the error bound instead.
        err = _max_float_error(data, restored)
        exact = f"lossy, max err {err:.2e} of range"
        verified = len(restored) == len(data) and err < 1e-3
    else:
        exact = "exact" if restored == data else "MISMATCH"
        verified = restored == data
    results["gistlink"] = {
        "seconds": seconds,
        "size": len(compressed),
        "ratio": len(data) / len(compressed),
        "throughput": len(data) / seconds / MB,
        "fidelity": exact,
        "verified": verified,
    }

    codecs = [
        ("lzma", lambda d: lzma.compress(d, preset=9), lzma.decompress),
        ("zlib", lambda d: zlib.compress(d, 9), zlib.decompress),
    ]
    if _zstandard is not None:
        compressor = _zstandard.ZstdCompressor(level=9)
        decompressor = _zstandard.ZstdDecompressor()
        codecs.append(
            ("zstd", compressor.compress, decompressor.decompress)
        )

    for name, comp, decomp in codecs:
        seconds, compressed = _time_it(comp, data, iterations)
        results[name] = {
            "seconds": seconds,
            "size": len(compressed),
            "ratio": len(data) / len(compressed),
            "throughput": len(data) / seconds / MB,
            "fidelity": "exact" if decomp(compressed) == data else "MISMATCH",
            "verified": decomp(compressed) == data,
        }
    return results


def print_table(data_type: str, size: int, results: dict) -> None:
    print(f"\n--- {data_type.upper()} ({size:,} bytes) ---")
    print(
        f"{'codec':<10}{'size':>12}{'ratio':>9}{'MB/s':>9}  fidelity"
    )
    for name, r in sorted(results.items(), key=lambda kv: -kv[1]["ratio"]):
        print(
            f"{name:<10}{r['size']:>12,}{r['ratio']:>8.2f}x"
            f"{r['throughput']:>9.1f}  {r['fidelity']}"
        )

    compression_ratio = results["gistlink"]["ratio"]
    baselines = {k: v["ratio"] for k, v in results.items() if k != "gistlink"}
    best_name = max(baselines, key=baselines.get)
    best = baselines[best_name]
    delta = (compression_ratio / best - 1.0) * 100.0
    verdict = "better than" if delta >= 0 else "worse than"
    print(
        f"GistLink is {abs(delta):.1f}% {verdict} the best general-purpose codec "
        f"({best_name}, {best:.2f}x)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()

    print("GistLink vs standard compression")
    print("=" * 62)
    backend = "Rust extension" if engine._RUST_AVAILABLE else "pure Python"
    print(f"Compression backend: {backend}")
    if _zstandard is None:
        print("zstd baseline skipped (pip install zstandard)")

    datasets = [
        ("telemetry", generate_telemetry_data(10_000, 4), 4),
        ("binary_float", generate_binary_float_data(100_000), 0),
        ("text", generate_text_data(200_000), 0),
    ]

    failures = 0
    for data_type, data, channels in datasets:
        results = benchmark(data, data_type, channels, args.iterations)
        print_table(data_type, len(data), results)
        failures += sum(1 for r in results.values() if not r["verified"])

    print("\n" + "=" * 62)
    if failures:
        print(f"{failures} codec(s) failed verification")
        return 1
    print("All reconstructions verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
