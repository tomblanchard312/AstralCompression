# McKay + ASTRAL Integration Guide

How domain-aware compression and gist-first atomized transmission fit
together, and how to drive them.

## The two layers

**McKay** (`astral/mckay_astral_integration.py`) is a compressor. It picks a
transform from the data type, applies it, then entropy-codes the result:

| Data type | Transform | Exact? |
|---|---|---|
| `TEXT` | mission-vocabulary abbreviation coding | yes |
| `TELEMETRY` | Q12 quantisation + per-channel delta coding | **no**, lossy |
| `BINARY` | float32 byte reordering (exponents grouped) | yes |
| `VOICE` | Codec2 re-encoding (needs the `voice` extra) | no |
| `IMAGE` / other | passthrough | yes |

**ASTRAL** (`astral/codec.py`) is a transmission format: 32-byte atoms, CRC-8
per atom, a replicated gist, and an LT fountain code over the body.

`pack_mckay_message` runs both: compress, describe, atomize, fountain-code.

## Usage

```python
from astral import pack_mckay_message, unpack_mckay_stream

data = open("telemetry.bin", "rb").read()

stream = pack_mckay_message(
    data,
    "TELEMETRY",
    channels=4,          # 0 auto-detects
    extra_fountain=20,   # additional redundancy atoms
)

result = unpack_mckay_stream(stream)
result["mckay"]     # metadata gist, present whenever one gist atom survived
result["data"]      # recovered bytes, present only on full recovery
result["complete"]  # bool
result["recovered_fraction"]
```

From the command line:

```bash
python -m astral.cli pack-mckay telemetry.bin out.bin --type TELEMETRY --channels 4
python -m astral.cli simulate out.bin lossy.bin --drop 0.5 --seed 1
python -m astral.cli unpack-mckay lossy.bin recovered.bin
```

## What the gist gives you

The `MCKAY_GIST` atom (atom type 3) is replicated alongside the header, so it
survives when no fountain packet does:

```python
{'mckay_version': 3, 'transform_id': 1, 'data_type': 'TEXT',
 'original_size': 9600, 'compressed_size': 68, 'channels': 0,
 'entropy_coder': 1, 'ratio': 141.176}
```

That is enough to know what was sent, how large it was, and whether it is
worth requesting a retransmission, without recovering a single payload byte.

## Sizing redundancy for your link

The gist lives only in the header atoms. If every copy is lost, the receiver
gets nothing, so replication is the floor on survivability:

```python
from astral import header_redundancy_for, pack_mckay_message

stream = pack_mckay_message(
    data, "TEXT",
    header_redundancy=header_redundancy_for(0.8),  # 21 copies -> 99% survival
)
```

| Target atom loss | Copies needed for 99% gist survival |
|---|---|
| 20% | 3 |
| 40% | 6 |
| 60% | 10 |
| 80% | 21 |

The default is `max(4, 10% of the fountain atom count)`, which suits ordinary
links and is not enough for 80% loss.

Measured recovery for a 9.6 KB text message with `extra_fountain=20`, 300
trials per cell:

| Atom loss | Default (43 atoms) | `header_redundancy_for(0.8)` (77 atoms) |
|---|---|---|
| 20% | 100% full | 100% full |
| 40% | 95% full | 100% full |
| 60% | 75% full, 2% gist | 98% full, 2% gist |
| 80% | 23% full, 12% gist | 56% full, 43% gist |

## Measured compression

From `python mckay_vs_standard.py` on this repository's generated datasets:

| Dataset | McKay | zstd -9 | LZMA -9 |
|---|---|---|---|
| Telemetry, 160 KB, 4 channels | 3.60x (lossy) | 1.12x | 1.43x |
| Binary float32, 100 KB, random | 1.17x | 1.08x | 1.08x |
| Mission text, 200 KB | 11.66x | 7.98x | 9.95x |

Telemetry's advantage comes from discarding precision: Q12 quantisation gives
a relative error around 1.2e-4 of the signal range. If you need bit-exact
floats, use `BINARY`.

End to end, source file to wire, redundancy included:

| Payload | Source | Wire | Ratio |
|---|---|---|---|
| Mission text | 9,600 B | 736 B | 13.0x |
| Telemetry, 1 channel | 32,000 B | 8,448 B | 3.8x |

Messages below roughly a kilobyte expand rather than shrink; fountain
redundancy is a fixed cost that only pays off with size.

## Compression without transmission

```python
from astral import mckay_astral_integration as mckay

compressed = mckay.compress(data, "TELEMETRY", channels=4)
restored = mckay.decompress(compressed)
mckay.stats(compressed)
# {'transform': 'telemetry', 'version': 3, 'original_size': 32000,
#  'compressed_size': 1768, 'payload_size': 1758, 'ratio': 18.1, ...}
```

## Stream format (v3)

10-byte header, then the transform payload:

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | magic `MK` |
| 2 | 1 | version (3) |
| 3 | 1 | transform id |
| 4 | 4 | original length, uint32 LE |
| 8 | 1 | channel count |
| 9 | 1 | entropy coder (0 LZMA, 1 zlib, 2 zstd, 0xFF none) |

Version 2 used a 16-bit length field, which truncated anything from 64 KiB
upward. Those streams are still read, but a v2 stream whose length was
truncated when written raises rather than returning a short result.

## Optional dependencies

| Feature | Extra | Fallback without it |
|---|---|---|
| Rust fast path, zstd | `fast` | pure Python, LZMA/zlib |
| Codec2 voice | `voice` | LZMA passthrough |
| Reed-Solomon | `rs` | `ImportError` with an install hint |

The Rust path is selected only when the compiled extension really exposes its
entry points, so an un-built source tree cannot masquerade as the fast path.

## Related documents

- [README.md](README.md): project overview, formats, CCSDS support
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md): command and API cheat sheet
- `python -m astral.mckay_usage_example`: runnable demonstrations
