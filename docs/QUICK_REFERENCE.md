# ASTRAL Quick Reference

## CLI

| Command | Purpose |
|---|---|
| `pack IN.json OUT.bin` | pack a grammar message into atoms |
| `unpack IN.bin` | best-effort decode to JSON |
| `simulate IN.bin OUT.bin --drop 0.4 --seed 1` | drop atoms at random |
| `pack-mckay IN OUT.bin --type TEXT` | compress with McKay, send as atoms |
| `unpack-mckay IN.bin [OUT]` | decode a McKay atom stream |
| `pack-text "..." OUT.bin` | TEXT message |
| `pack-text-with-dict "a,b" "..." OUT.bin` | TEXT plus lexicon update |
| `pack-voice IN.wav OUT.bin` / `unpack-voice IN.bin OUT.wav` | VOICE |
| `pack-cmd '{...}' OUT.bin --key HEX` | authenticated command |
| `pack-cmd-batch '{...}' OUT.bin --key HEX` | time-tagged command batch |
| `wrap-sp IN.bin OUT.bin --msg-type DETECT` / `unwrap-sp` | CCSDS Space Packet |
| `encode-rs IN.bin OUT.bin --e 16` / `decode-rs` | per-atom Reed-Solomon |
| `frame-tm IN.bin OUT.bin --scid 42 [--mode VCA\|PACKET]` / `deframe-tm` | CCSDS TM frames |

Useful flags on `pack-mckay`: `--redundancy R` (proportional fountain
overhead, default 1.0), `--min-redundancy N` (floor), `--extra N` (extra
atoms), `--header-redundancy N` and `--survive-loss R` (size gist survival for
a loss rate).

## Python API

```python
from astral import (
    pack_message, unpack_stream,
    pack_mckay_message, unpack_mckay_stream,
    header_redundancy_for,
    pack_message_sp, unpack_stream_sp,     # CCSDS Space Packet
    pack_message_rs, unpack_stream_rs,     # per-atom Reed-Solomon
    pack_message_tm, unpack_frames_tm,     # CCSDS TM frames
)
```

Compression on its own:

```python
from astral import mckay_astral_integration as mckay
mckay.compress(data, "TELEMETRY", channels=4)
mckay.decompress(stream)
mckay.stats(stream)
```

CCSDS Reed-Solomon codeblocks (needs the `rs` extra):

```python
from astral import rs_fec
block = rs_fec.encode_codeblock(frame_1115_bytes)      # RS(255,223) I=5 -> 1275 B
data, corrected, ok = rs_fec.decode_codeblock(block)
```

## Decode result keys

| Key | Meaning |
|---|---|
| `gist` | coarse type/object/location/confidence, from the header atom |
| `mckay` | McKay metadata gist (McKay streams only) |
| `complete` | payload fully recovered |
| `recovered_fraction` | share of source blocks solved |
| `message` | decoded message dict, or `None` |
| `data` | decompressed bytes (McKay streams only) |
| `integrity_ok` | `True` verified, `False` corrupt, `None` not verified (incomplete recovery, or a pre-v2 stream) |

## Data types

| Type | Transform | Exact | Notes |
|---|---|---|---|
| TEXT | abbreviation coding | yes | case and whitespace preserved |
| TELEMETRY | Q12 + delta | **no** | ~1.2e-4 relative error |
| BINARY | float byte reordering | yes | use this for exact floats |
| VOICE | Codec2 | no | needs the `voice` extra |

## Redundancy cheat sheet

| Atom loss | `header_redundancy_for(loss)` |
|---|---|
| 20% | 3 |
| 40% | 6 |
| 60% | 10 |
| 80% | 21 |

## Wire cost

```
wire  ~=  compressed x 32/21 (framing) x (1 + redundancy) + 32 x header copies
```

About 3x the compressed size at defaults, so compression must beat ~3x for the
transmission to be smaller than the source.

| `redundancy` | 12 KB text payload | survives |
|---|---|---|
| 1.0 (default) | 25.9 KB | 30% loss |
| 0.5 | 19.4 KB | 20% loss |
| 0.3 | 16.8 KB | 10% loss |

## Limits

- Payload per message: about 512 KB (`codec.max_payload_bytes()`).
- Atom: 32 bytes, 21 of them payload; symbol size 16 bytes.
- McKay original length: 4 GB (uint32).
- Small messages expand; fountain redundancy pays off from a few KB up.

## Checks

```bash
python -m pytest tests
python -m flake8 astral/ tests/ *.py --config=setup.cfg
python benchmarks/mckay_vs_standard.py
python -m astral.mckay_usage_example
```
