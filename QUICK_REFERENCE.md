# McKay + ASTRAL Quick Reference

## System Status: **100% OPERATIONAL**

- **GIST-First Architecture**: Essential metadata survives packet loss
- **Atomized Packets**: 32-byte atoms with CRC-8 integrity
- **McKay Compression**: 2x to 180x compression ratios
- **Fountain Codes**: Reliable transmission with packet loss tolerance
- **All Data Types**: TEXT, BINARY, IMAGE, VOICE, VIDEO

## Repository

**GitHub**: [github.com/tomblanchard312/astralcompression](https://github.com/tomblanchard312/astralcompression)

**License**: MIT License with Attribution Requirement - See [LICENSE](LICENSE) for details.

## Quick Start

### Basic Compression
```python
from astral.mckay_astral_integration import McKayASTRALCompressor

mckay = McKayASTRALCompressor(compression_mode="extreme")
compressed = mckay.compress_with_mckay(data, "TEXT")
decompressed = mckay.decompress_with_mckay(compressed)
```

### GIST-First Transmission
```python
# Pack with atomized packets
atomized = mckay.pack_mckay_message_atomized(
    data=your_data,
    data_type="TEXT",
    extra_fountain=5
)

# Unpack from atomized stream
recovered, data_type, metadata = mckay.unpack_mckay_message_atomized(atomized)
```

## Data Type Support

| Type | Compression | Features | Status |
|------|-------------|----------|---------|
| **TEXT** | 3.125x | Semantic encoding, mission vocabulary | 100% |
| **BINARY** | Up to 180x | Pattern recognition, frequency analysis | 100% |
| **IMAGE** | 2.84x | Run-length encoding, delta compression | 100% |
| **VOICE** | 1.04x | Delta encoding, MCKAY_VOICE markers | 100% |
| **VIDEO** | Variable | Frame analysis, zlib integration | 100% |

## Maintained Command Set

```bash
# Show available CLI workflows
python -m astral.cli --help

# Minimal end-to-end atomized flow
python -m astral.cli pack examples/detect.json out.bin
python -m astral.cli simulate out.bin lossy.bin --drop 0.4
python -m astral.cli unpack lossy.bin

# Validate Space Packet wrapper integration
python PHASE3_SPACEPACKET_VERIFICATION.py

# Run maintained module tests
python -m pytest tests
```

## Key Methods

### Core Compression
- `compress_with_mckay(data, data_type)` → compressed bytes
- `decompress_with_mckay(compressed)` → original data

### GIST-First Transmission
- `pack_mckay_message_atomized(data, type, extra_fountain)` → atomized stream
- `unpack_mckay_message_atomized(stream)` → (data, type, metadata)

### Utilities
- `get_compression_stats()` → compression metrics
- `_detect_data_type(data)` → automatic type detection

## Deep Space Ready

- **Packet Loss Tolerance**: Up to 80% with gist survival
- **Progressive Recovery**: Fountain codes recover data progressively
- **Atomic Integrity**: CRC-8 checksums for each 32-byte atom
- **Essential Metadata**: Survives even under severe packet loss

## Documentation

- **Full Integration Guide**: [MCKAY_ASTRAL_INTEGRATION.md](MCKAY_ASTRAL_INTEGRATION.md)
- **Main README**: [README.md](README.md)
- **System Status**: 100% operational for all data types

---

## Repository & License

**GitHub**: [github.com/tomblanchard312/astralcompression](https://github.com/tomblanchard312/astralcompression)

**License**: MIT License with Attribution Requirement - See [LICENSE](LICENSE) for details.

*This project requires attribution to the original creator when used or distributed. Please see the LICENSE file for complete requirements.*

---

**Dr. Rodney McKay's Extreme Compression System: Ready for deployment to the Pegasus Galaxy!**