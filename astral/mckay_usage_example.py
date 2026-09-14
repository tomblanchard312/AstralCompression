#!/usr/bin/env python3
"""
McKay + ASTRAL usage examples.

Runnable demonstrations of the full path: domain-aware compression, a
replicated metadata gist, and a fountain-coded body that survives packet loss.

    python -m astral.mckay_usage_example
"""

import math
import random
import struct
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from astral import mckay_astral_integration as mckay
from astral.codec import (
    header_redundancy_for,
    pack_mckay_message,
    unpack_mckay_stream,
    unpack_stream,
)
from astral.container import HEADER_GIST, MCKAY_GIST


def _report(label: str, source: bytes, stream: bytes) -> None:
    atoms = len(stream) // 32
    print(f"  {label}")
    print(f"    source      : {len(source):,} bytes")
    print(f"    on the wire : {len(stream):,} bytes in {atoms} atoms")
    print(f"    wire ratio  : {len(source) / len(stream):.2f}x (redundancy included)")


def example_mission_report() -> bytes:
    """Text: mission prose compressed and atomized."""
    print("=== Mission report (TEXT) ===")
    report = (
        "MISSION STATUS REPORT\n"
        "Battery temperature nominal. Attitude nominal. Downlink established.\n"
        "Payload telemetry nominal. No anomaly detected. Subsystem interface ok.\n"
    ) * 40
    source = report.encode("utf-8")

    stream = pack_mckay_message(source, "TEXT", extra_fountain=15)
    _report("McKay + fountain", source, stream)

    result = unpack_mckay_stream(stream)
    print(f"    gist        : {result['mckay']}")
    print(f"    recovered   : {'exact' if result['data'] == source else 'MISMATCH'}")
    return stream


def example_telemetry() -> bytes:
    """Telemetry: a float32 matrix, quantised and delta-coded."""
    print("\n=== Spacecraft telemetry (TELEMETRY) ===")
    channels = 4
    samples = 2000
    values = []
    for t in range(samples):
        for ch in range(channels):
            values.append(math.sin(t / 40.0 + ch) * 10.0 + ch)
    source = struct.pack(f">{len(values)}f", *values)

    stream = pack_mckay_message(source, "TELEMETRY", channels=channels)
    _report("McKay + fountain", source, stream)

    result = unpack_mckay_stream(stream)
    restored = struct.unpack(f">{len(values)}f", result["data"])
    max_err = max(abs(a - b) for a, b in zip(values, restored))
    print(f"    quantiser   : lossy by design, max error {max_err:.2e}")
    return stream


def example_binary() -> bytes:
    """Binary float data: byte reordering before entropy coding."""
    print("\n=== Scientific binary data (BINARY) ===")
    source = struct.pack(f">{5000}f", *[i * 0.25 for i in range(5000)])
    stream = pack_mckay_message(source, "BINARY")
    _report("McKay + fountain", source, stream)
    result = unpack_mckay_stream(stream)
    print(f"    recovered   : {'exact' if result['data'] == source else 'MISMATCH'}")
    return stream


def example_gist_under_loss() -> None:
    """The point of gist-first: something useful survives heavy loss."""
    print("\n=== Gist-first behaviour under packet loss ===")
    source = ("Telemetry nominal. Battery temperature nominal. " * 200).encode()

    # Size the header replication for the loss rate the link actually sees.
    loss = 0.8
    stream = pack_mckay_message(
        source,
        "TEXT",
        extra_fountain=20,
        header_redundancy=header_redundancy_for(loss),
    )
    atoms = [stream[i : i + 32] for i in range(0, len(stream), 32)]
    print(f"  message: {len(atoms)} atoms, header replication sized for {loss:.0%} loss")

    trials = 200
    full = gist_only = nothing = 0
    for seed in range(trials):
        rng = random.Random(seed)
        kept = b"".join(a for a in atoms if rng.random() >= loss)
        result = unpack_stream(kept)
        if result.get("data") == source:
            full += 1
        elif result.get("mckay"):
            gist_only += 1
        else:
            nothing += 1

    print(f"  at {loss:.0%} loss over {trials} trials:")
    print(f"    full payload recovered : {full / trials:.0%}")
    print(f"    gist only              : {gist_only / trials:.0%}")
    print(f"    nothing                : {nothing / trials:.0%}")

    # Show what the gist alone tells you when no fountain packet arrives.
    gist_atoms = b"".join(
        a for a in atoms if a[9] in (HEADER_GIST, MCKAY_GIST)
    )
    result = unpack_stream(gist_atoms)
    print(f"  gist without any fountain packet: {result['mckay']}")


def example_compression_only() -> None:
    """Compression on its own, without the transmission layer."""
    print("\n=== Compressor used directly ===")
    source = ("satellite telemetry nominal battery temperature " * 100).encode()
    compressed = mckay.compress(source, "TEXT")
    print(f"  stats: {mckay.stats(compressed)}")
    assert mckay.decompress(compressed) == source


def main() -> int:
    print("McKay + ASTRAL deep space compression")
    print("=" * 60)
    try:
        example_mission_report()
        example_telemetry()
        example_binary()
        example_gist_under_loss()
        example_compression_only()
    except Exception as exc:  # pragma: no cover - demonstration script
        print(f"Error during demonstration: {exc}")
        return 1
    print("\nAll examples completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
