# ASTRAL — Atomic Semantic Tiles with Robust Asynchronous Linking

Extreme compression and loss-tolerant message delivery for deep-space RF links.

ASTRAL combines three things that are usually kept apart:

- **Domain-aware compression** (the McKay codec): quantised, delta-coded
  telemetry, byte-reordered float arrays, abbreviation-coded mission text.
- **Gist-first framing**: a replicated metadata atom, so a receiver learns
  what was sent even when it recovers none of the body.
- **Fountain-coded payloads** (LT codes) in fixed 32-byte atoms with CRC-8, so
  the message reassembles from whatever subset of atoms arrives.

The core is pure Python 3.9+ with no required dependencies. Reed-Solomon,
Codec2 voice and the Rust fast path are optional extras.

**GitHub**: [github.com/tomblanchard312/astralcompression](https://github.com/tomblanchard312/astralcompression)
**License**: MIT with an attribution requirement. See [LICENSE](LICENSE).

> **Inspired by**: [Atlantis Data Burst](https://www.gateworld.net/wiki/Atlantis_data_burst).
> The name is a nod to the fiction; everything below is measured.

## Documentation

| Document | What it covers |
|---|---|
| [docs/FORMAT.md](docs/FORMAT.md) | The wire format specification: every byte, enough to reimplement |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | McKay + ASTRAL guide, measured ratios, redundancy sizing |
| [docs/QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md) | Command and API cheat sheet |
| [CHANGELOG.md](CHANGELOG.md) | What changed and why |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | This release: scope, compatibility, known limits |

## Install

```bash
pip install astral-compression            # core, no dependencies
pip install astral-compression[rs]        # + Reed-Solomon (reedsolo)
pip install astral-compression[dict]      # + mission dictionaries (zstandard)
pip install astral-compression[voice]     # + Codec2 voice (pycodec2, numpy)
pip install astral-compression[fast]      # + Rust extension and zstd
pip install astral-compression[all]       # everything
```

## Quick start

```bash
# Pack a JSON message into atomized binary, lose 40% of it, decode anyway
python -m astral.cli pack examples/detect.json out.bin
python -m astral.cli simulate out.bin lossy.bin --drop 0.4 --seed 1
python -m astral.cli unpack lossy.bin
```

```bash
# Compress a file with McKay and send it as gist-first atoms
python -m astral.cli pack-mckay report.txt out.bin --type TEXT
python -m astral.cli unpack-mckay out.bin recovered.txt
```

```python
from astral import pack_mckay_message, unpack_mckay_stream

stream = pack_mckay_message(open("telemetry.bin", "rb").read(), "TELEMETRY")
result = unpack_mckay_stream(stream)
result["mckay"]   # metadata gist: type, sizes, ratio (survives body loss)
result["data"]    # recovered bytes, once enough atoms arrive
```

## Measured performance

Every number below is produced by a script in this repository on the datasets
those scripts generate. Reproduce with `python benchmarks/mckay_vs_standard.py` and
`python -m astral.mckay_usage_example`. Expect variation with your data.

### Mission dictionaries: the biggest win on short messages

A general compressor has nothing to work with inside a 90-byte status report;
the patterns that make mission traffic compressible live *between* messages.
Train a dictionary on past traffic and hand it to both ends:

```bash
astral train-dict "samples/*.txt" -o mission.dict
astral pack-mckay report.txt out.bin --type TEXT --dict mission.dict
astral unpack-mckay out.bin recovered.txt --dict mission.dict
```

100 short mission messages, compressed individually:

| Method | Total |
|---|---|
| zstd -19, no dictionary | 7,049 B |
| built-in text transform | 4,578 B |
| **zstd -19 + trained dictionary** | **3,650 B** |

That is 20% better than the built-in transform, and it is the honest
recommendation for message traffic. The dictionary is mission configuration:
ship the file to both ends, version it, and keep it. A receiver without it
reports the dictionary id it needs rather than failing generically.

Requires `pip install astral-compression[dict]`.

### Compression, McKay vs general-purpose codecs

| Dataset | McKay | zstd -9 | LZMA -9 | Notes |
|---|---|---|---|---|
| Telemetry, 160 KB float32, 4 channels | **3.60x** | 1.12x | 1.43x | McKay is lossy here (Q12) |
| Binary float32, 100 KB, random | **1.17x** | 1.08x | 1.08x | random data barely compresses |
| Mission text, 200 KB | **11.66x** | 7.98x | 9.95x | exact |

McKay's telemetry advantage comes from quantisation, so it is not a
like-for-like comparison with the exact codecs: Q12 quantisation introduces a
relative error of about 1.2e-4 of the signal range. Use `BINARY` rather than
`TELEMETRY` when you need bit-exact floats.

### End-to-end, source file to wire

Includes the header, the gist and all fountain redundancy:

| Payload | Source | On the wire | Ratio |
|---|---|---|---|
| Mission text | 9,600 B | 736 B | 13.0x |
| Telemetry (1 channel, float32) | 32,000 B | 8,448 B | 3.8x |

Small messages *expand*, and there is a floor: the minimum message is around
19 atoms (608 bytes) once header replication, the metadata gist and the
minimum fountain redundancy are counted, however small the payload.
`examples/detect.json` is 146 bytes of JSON and ships as 480 bytes in 15
atoms. That floor is the price of surviving loss, not waste, but it means
compression barely matters below a kilobyte: at those sizes tune
`min_redundancy` and `header_redundancy` instead.

The wire cost is predictable, so you can work out in advance whether ASTRAL
pays for a given payload:

```
wire bytes  ~=  compressed bytes  x  32/21 (atom framing)
                                  x  (1 + redundancy)   (fountain, default 1.0)
                                  +  32 x header copies
```

That is roughly **3x the compressed size** at the default settings, so McKay
has to compress better than about 3x before the transmission is smaller than
the source. Repetitive mission text and telemetry clear that easily; already
compressed or random data does not.

Redundancy is a dial. For a 12 KB text payload, recovery rate over 100 trials:

| `redundancy` | Wire | 10% loss | 20% loss | 30% loss |
|---|---|---|---|---|
| 1.0 (default) | 25.9 KB | 100% | 100% | 100% |
| 0.5 | 19.4 KB | 100% | 100% | 90% |
| 0.3 | 16.8 KB | 100% | 92% | 0% |
| 0.15 | 14.9 KB | 95% | 0% | 0% |

Pick it for the loss you expect, with margin; the cliff is sharp because a
fountain code either collects enough independent packets or it does not.

### Loss tolerance

A 9.6 KB text message with `extra_fountain=20`, 300 trials per cell, showing
the share of trials that recover the full payload, and the share that recover
only the gist:

| Atom loss | Default header redundancy (43 atoms) | Sized with `header_redundancy_for(0.8)` (77 atoms) |
|---|---|---|
| 20% | 100% full | 100% full |
| 40% | 95% full | 100% full |
| 60% | 75% full, 2% gist | 98% full, 2% gist |
| 80% | 23% full, 12% gist | 56% full, 43% gist |

The gist and the fountain parameters live in the header atoms, so if every
copy is lost there is nothing to decode. Replication is therefore the floor on
survivability, and it is tunable:

```python
from astral import header_redundancy_for, pack_mckay_message

# Keep the gist alive with 99% confidence on a link that drops 80% of atoms.
stream = pack_mckay_message(
    data, "TEXT", header_redundancy=header_redundancy_for(0.8)  # -> 21 copies
)
```

The default scales with message size (at least 4 copies, at least 10% of the
fountain atom count), which is sized for ordinary links, not for 80% loss.

### Throughput (pure Python, no Rust extension)

| Operation | Rate |
|---|---|
| `pack_mckay_message`, 200 KB binary | ~0.45 s |
| `unpack_mckay_stream`, 200 KB | ~0.57 s |
| TM framing / deframing | ~13 MB/s |
| Fountain encode, K=2000, 4000 packets | 27 ms |
| Fountain decode, K=2000 | 40 ms |

The fountain layer XORs symbols as big integers, indexes equations by unknown
rather than rescanning, and draws packet indices from a sparse shuffle; the
CRCs are table-driven. Together those took a 200 KB message from 3.9 s to
0.45 s without changing a byte of the wire format.

### Fountain overhead

Packets needed to recover K source blocks, measured over 20 seeds per K:

| K | Packets needed | Overhead |
|---|---|---|
| 10 | 13.3 | 1.33x |
| 50 | 52.5 | 1.05x |
| 200 | 202.6 | 1.01x |

The decoder peels degree-1 equations first and then runs Gaussian elimination
over GF(2) on the residual system, so it does not stall when the residual
graph has no degree-1 node.

## File format

An atom is 32 bytes:

| Bytes | Field |
|---|---|
| 0-1 | sync `0xA5 0xE6` |
| 2 | atom format version (2: header carries the integrity CRC-32) |
| 3-4 | atom_index (uint16 LE) |
| 5-6 | total_atoms (uint16 LE) |
| 7-8 | message_id (uint16 LE) |
| 9 | atom_type: `0=HEADER_GIST`, `1=FOUNTAIN_PACKET`, `2=DICT_UPDATE`, `3=MCKAY_GIST` |
| 10-30 | payload (21 bytes) |
| 31 | CRC-8/J1850 over bytes 0-30 |

- **HEADER_GIST** carries source block count K, symbol size (16), payload
  length, fountain seed, the packed gist bits, and a CRC-32 over the header
  and payload together. It is replicated; see `header_redundancy_for`.
- **MCKAY_GIST** carries the McKay version, transform, data type, original and
  compressed sizes, channel count and entropy coder. Also replicated.
- **FOUNTAIN_PACKET** carries a packet seed, degree and a 16-byte XOR block.
- The receiver scans for the sync word, so a stream that starts mid-atom or
  contains byte-level gaps still decodes.

A single message is limited by the 16-bit atom counters to roughly 512 KB of
payload; `codec.max_payload_bytes()` returns the exact figure and oversized
input raises rather than wrapping.

### End-to-end integrity

Each atom carries a CRC-8, which rejects 255 of every 256 corrupt atoms. The
one that slips through is XORed into the reconstruction and silently changes
what the receiver ends up with, so the header carries a CRC-32 covering **the
header and the payload together**, checked before any decode is reported:

```python
result = unpack_stream(stream)
result["integrity_ok"]   # True | False | None
```

| Value | Meaning |
|---|---|
| `True` | header and payload both verified against the checksum |
| `False` | they reassembled but do not match: at least one atom was corrupt |
| `None` | **not verified**: recovery was incomplete, or the stream predates atom format 2 |

`None` is not a format-version indicator. A v2 stream that recovers only some
of its fountain blocks also reports `None`, because there is nothing complete
to check yet.

The checksum covers the header because the header decides how the payload is
read: the gist type selects the decoder, and K and the seed drive reassembly.
Covering the payload alone left a gap where a corrupt header atom that passed
its own CRC-8 could turn an intact TEXT payload into a fabricated STATUS
report while the payload checksum still matched.

Header atoms are replicated, so the decoder takes the copy the majority agree
on. One corrupt copy is outvoted and the message decodes normally; only if
every copy is damaged the same way does the checksum fail.

A failed check is reported as `complete: False` with an `error`, never as a
decode. The gist still comes back, so an operator learns what was sent and
that it arrived damaged. Measured on 600-byte payloads where one corrupt atom
reaches the solution: the check catches it, and of those cases the unchecked
path would have returned wrong bytes as a clean decode about two thirds of
the time (the rest raised inside the decompressor).

Note that CRC-32 is an error-detecting code, not an authenticator. It catches
noise, not tampering. Commands are authenticated separately.

### Commanding

Commands are the one place where a mistake is dangerous, so the API fails
closed. Decoding a command verifies it by default, and refuses to hand back
its contents otherwise:

```python
from astral import unpack_stream
from astral.commands import CommandSequencer, PersistentReplayGuard

# Sender: a counter that always increases.
seq = CommandSequencer()
stream = pack_cmd_message(
    {"name": "BURN", "thruster_id": 1, "duration_ms": 12500},
    key=KEY, counter=seq.next(),
)

# Receiver: one guard per uplink key, on disk so it survives a restart.
guard = PersistentReplayGuard("/var/lib/astral/uplink.json", "sat-1")
result = unpack_stream(stream, key=KEY, replay_guard=guard)
result["command_authenticated"]   # True, False, or None if not a command
```

Use `PersistentReplayGuard` for anything commanding real hardware. The
in-memory `ReplayGuard` protects only a single run: a receiver that restarts
begins again at -1 and will accept a command it has already executed. The
persistent guard writes the counter durably **before** accepting the command,
so a crash can lose a command but never execute one twice, and it refuses to
run on a state file it cannot parse rather than silently reopening the window.

From the command line:

```bash
astral unpack cmd.bin --key $KEY --replay-state /var/lib/astral/uplink.json --link-id sat-1
```

A command that fails its HMAC, arrives without one, or repeats a counter the
guard has already accepted is reported as an error with `message: None`. It is
never returned as if it had been verified.

Without a key, commands still decode for inspection but are tagged
`authenticated: False`, and the CLI prints a warning to stderr. **Never act on
such a command.** `decode_cmd` raises rather than returning an unverified
command unless you pass `require_auth=False`.

Authentication is HMAC-SHA256 over a 4-byte counter plus the command body. A
replayed BURN is still a perfectly valid BURN, so freshness matters as much as
authenticity: keep the `ReplayGuard` for the life of the key.

## Space communications standards

ASTRAL is not a replacement for CCSDS. It is a payload format that can be
carried inside CCSDS framing, and the framing implemented here is conformant
where it claims to be.

**CCSDS 133.0-B-2 Space Packet Protocol** (`astral/spacepacket.py`)
Six-byte primary header, 14-bit per-APID sequence counters, idle packets.
APIDs: DETECT 0x010, STATUS 0x011, TEXT 0x012, VOICE 0x013, CMD 0x100,
CMD_BATCH 0x101. Covered by `tests/test_ccsds.py`.

**CCSDS 132.0-B-3 TM Transfer Frames + 131.0-B-5 randomizer** (`astral/tmframe.py`)
1115-byte frames, ASM 0x1ACFFC1D, CRC-16-CCITT FECF, SCID/VCID, master and
virtual channel counters. The pseudo-randomizer generates the published CCSDS
sequence (`FF 48 0E C0 9A 0D 70 BC`) and is applied to the entire transfer
frame, header and FECF included, exactly as the standard specifies. Two data
field modes:

- `MODE_VCA` (default): the data field is an opaque VCA_SDU (a raw ASTRAL atom
  stream), sync flag 1.
- `MODE_PACKET`: the data field carries CCSDS Space Packets, sync flag 0,
  segment length ID `11`, and a real First Header Pointer, so a standard
  ground-station packet extractor can reassemble the stream. Short frames are
  filled with idle packets.

Covered by `tests/test_ccsds.py`.

**CCSDS Reed-Solomon** (`astral/rs_fec.py`, needs the `rs` extra)
Two distinct codes, not interchangeable:

- `encode_codeblock` / `decode_codeblock`: RS(255,223) and RS(255,239) over
  GF(2^8) with the CCSDS generator and symbol interleaving. At interleave 5 a
  1115-byte TM frame becomes a 1275-byte codeblock and an 80-byte burst error
  is corrected exactly.

  The parameters are the ones that matter for interop: field polynomial
  `0x187`, first consecutive root 112, and **primitive element alpha^11**, so
  the generator roots are alpha^(11*(112+i)). These match Phil Karn's libfec
  (`FCR=112, PRIM=11`), which is what gr-satellites and most ground stations
  use. The generator polynomial was verified against an independent
  construction using the `galois` library, and the parity bytes are frozen as
  test vectors.

  Symbols are carried in the **dual basis** by default, as CCSDS specifies;
  pass `basis="conventional"` for libfec's `encode_rs_8` representation.
  Mismatched bases are the classic CCSDS RS interop failure: the maths is
  identical, the bytes on the wire are not. Both endpoints must agree.
- `encode_stream` / `decode_stream`: RS(48,32) and RS(64,32) applied per atom,
  so a damaged atom is repaired rather than dropped by its CRC. Useful, but
  not a CCSDS codeblock.

Covered by `tests/test_ccsds.py`, with parity vectors in `tests/test_vectors.py`.

### What this does and does not buy you

A ground station that speaks CCSDS will synchronise, derandomise, check the
FECF and route by APID without custom code. It will **not** understand the
ASTRAL atoms inside: the gist, fountain decoding and McKay decompression need
this library (or a reimplementation of it) at the receiving end. Treat CCSDS
support as transport compatibility, not as end-to-end interoperability.

## Data types

### TEXT

```bash
python -m astral.cli pack-text "Hello from the far side." out_text.bin
python -m astral.cli unpack out_text.bin
```

Text roundtrips exactly, including capitalisation and whitespace.

### VOICE (WAV to bitstream)

```bash
python -m astral.cli pack-voice input.wav out_voice.bin
python -m astral.cli unpack-voice out_voice.bin recovered.wav
```

Codec2 re-encoding requires the `voice` extra; without it voice falls back to
LZMA passthrough.

### CMD (optional HMAC authentication)

```bash
python -m astral.cli pack-cmd '{"name":"POINT","az":-12.3456,"el":30.0}' out_cmd.bin \
  --key 00112233445566778899aabbccddeeff
python -m astral.cli unpack out_cmd.bin
```

### Mission lexicon updates (DICT_UPDATE)

```bash
python -m astral.cli pack-text-with-dict "kepler,thruster,firing,anomaly" \
  "Kepler reports thruster anomaly." out_text_dict.bin
```

### Batched, time-tagged commands (CMD_BATCH)

```bash
python -m astral.cli pack-cmd-batch '{"policy":{"rollback_on_fail":true},"items":[{"tai_offset_s":5,"cmd":{"name":"SET_MODE","mode":"SCIENCE"}}]}' out_batch.bin \
  --key 00112233445566778899aabbccddeeff
```

## McKay compression format (v3)

10-byte header: magic `MK`, version, transform id, original length (uint32 LE),
channel count, entropy coder. Transforms: passthrough, text (abbreviation
coding), telemetry (Q12 quantisation plus per-channel delta coding), Codec2
voice, binary float (byte reordering). Entropy coders: LZMA, zlib, zstd, none.

Version 2 streams still decode. Note that v2 stored the original length in 16
bits, so v2 streams of 64 KiB or more were written with an unrecoverable
length; those are now reported as an error instead of being returned
truncated.

## Rust fast path

`astral_compress/` holds an optional PyO3 extension implementing the telemetry,
binary-float and text transforms with zstd. Build it with
`maturin build --release` inside that directory and install the wheel, or use
the `fast` extra. Without it everything still works in pure Python.

Speedups depend on your machine and data; run `python benchmarks/rust_vs_python_benchmark.py`
to measure yours rather than relying on a number in a README. The benchmark
reports per-dataset timings, ratios and an average speedup.

## Testing

```bash
pip install pytest reedsolo numpy
python -m pytest tests            # 285 passed, 19 skipped without the Rust extension
python -m flake8 astral/ tests/ *.py --config=setup.cfg
```

The skipped tests are the Rust extension suite; they run in CI, where the
wheel is built, and CI additionally asserts that the fast path is actually
selected rather than silently falling back.

Wire-format stability is pinned by `tests/test_vectors.py`, whose Reed-Solomon
vectors were produced by an independent implementation. CCSDS conformance is
covered by `tests/test_ccsds.py`.

## Limitations

- The grammar covers DETECT and STATUS. Extend `astral/grammar.py` for more.
- Symbol size is fixed at 16 bytes; soliton parameters are simple defaults.
- Telemetry compression is lossy (Q12). Use `BINARY` for bit-exact floats.
- One message is capped near 512 KB of payload by the 16-bit atom counters.
- The gist survives only as long as one header atom does. Size
  `header_redundancy` for your link.
- CCSDS framing gives transport compatibility, not end-to-end decoding, at a
  third-party ground station.

## Repository and license

**GitHub**: [github.com/tomblanchard312/astralcompression](https://github.com/tomblanchard312/astralcompression)

MIT License with an attribution requirement: any use, distribution or
derivative work must include a clear and prominent attribution to the original
creator, visible to end users. See [LICENSE](LICENSE) for the full text.
