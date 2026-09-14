# Changelog

## 2026-09-14 (second pass)

### Fixed (data integrity)
- **A corrupt atom could be reported as a clean decode.** CRC-8 rejects 255 of
  every 256 corrupt atoms; the one that slips through is XORed into the
  reconstruction, and nothing checked the result. Atom format 2 puts a CRC-32
  of the assembled payload in the header atom and the decoder verifies it
  before reporting success. Measured on 600-byte payloads where the corruption
  reaches the fountain solution, the unchecked path returned wrong bytes as a
  clean decode in 48 of 71 cases and raised in the other 23. Results now carry
  `integrity_ok`, and a failure is reported as `complete: False` with an
  error, with the gist still available.
- `tests/test_basic.py::test_lossy` used an unseeded RNG and asserted the gist
  always survives 40% loss, which fails roughly once in 40 runs by chance. It
  is now seeded and sizes header replication for the loss it simulates.

### Changed (performance, no wire-format change)
- The fountain code XORs symbols as big integers instead of byte at a time,
  indexes equations by the unknowns they contain instead of rescanning every
  equation for every solved block, and draws packet indices from a sparse
  Fisher-Yates instead of materialising `list(range(K))` per packet.
- CRC-8 and CRC-16 are table-driven; the TM randomizer XORs a whole frame in
  one operation.
- Net effect on a 200 KB message: 3.9 s to 0.45 s to pack. Fountain decode at
  K=2000 went from 509 ms to 40 ms; TM framing from 8.8 to 13.7 MB/s.
- The sampler and both CRCs are covered by tests asserting bit-identical
  output against the original reference implementations, since the Rust and C
  ports mirror them and old streams must still decode.

### Changed (API)
- `container.parse_atoms` returns `Atom` named tuples carrying the atom format
  version. Indexes 0-4 are unchanged, so positional access still works, but
  five-way unpacking now needs a sixth name.

## 2026-09-14

### Fixed (data integrity)
- **McKay streams over 64 KiB no longer decode to corrupt output.** The
  original-length field was 16 bits, so TELEMETRY and BINARY payloads of 65535
  bytes or more came back truncated with no error (160,000 bytes in, 65,532
  out). The field is now 32 bits (format v3) and every reconstruction is
  length-checked. v2 streams still decode; v2 streams whose length was
  truncated when written now raise instead of returning junk.
- **The Rust fast path is no longer selected against a stub.** The un-built
  `astral_compress/` source directory imports as an empty namespace package,
  so `_RUST_AVAILABLE` was always True and every compression call took an
  exception-driven fallback. Availability now requires a real entry point.
- **The CCSDS pseudo-randomizer generates the correct sequence.** It produced
  `FF 1A AF 66 ...`; the published CCSDS 131.0-B-5 sequence is
  `FF 48 0E C0 9A 0D 70 BC`. Frames randomised by the old code could not be
  derandomised by any conforming ground station.
- **Randomisation now covers the whole transfer frame**, header and FECF
  included, as CCSDS 131.0-B-5 requires. Only the ASM is left in the clear.
- **TM frames declare their data field honestly.** Every frame previously
  claimed segment length ID `11` with First Header Pointer 0x7FF, telling
  parsers that no packet ever starts. Opaque ASTRAL streams now use VCA
  framing (sync flag 1); the new `MODE_PACKET` carries real Space Packets with
  a correct First Header Pointer and idle-packet fill.
- **`astral.cli wrap-sp` wraps its input.** It was discarding the file it read
  and packing a fresh, near-empty message instead.
- **Text payloads roundtrip exactly.** Capitalisation and whitespace were
  being discarded ("Hello world" decoded as "hello world"). Payload format v2
  adds case tags and explicit gap records; v1 payloads still decode.

### Fixed (robustness)
- The fountain decoder runs Gaussian elimination over GF(2) after peeling.
  Recovery overhead at K=200 fell from 1.42x to 1.01x, and decodes that
  previously stalled with ample packets now succeed.
- Atom parsing scans for the sync word instead of assuming 32-byte alignment,
  so a stream that starts mid-atom or has byte-level gaps still decodes.
- TM deframing likewise searches for the ASM rather than stepping blindly.
- Payload size limits are checked against the real 16-bit atom counter budget
  instead of a 24-bit field that could not be reached.
- Ragged TELEMETRY input is rejected with an explanatory error rather than
  silently truncated.

### Added
- `codec.pack_mckay_message` / `unpack_mckay_stream`: McKay compression
  carried over the fountain/atom layer with a replicated `MCKAY_GIST` atom.
  This is the McKay + ASTRAL integration the documentation described; no code
  previously connected the two.
- `codec.header_redundancy_for(loss_rate)` and a `header_redundancy` argument
  on every pack function, so gist survival can be sized for the link.
- A `redundancy` argument (and `codec.fountain_atom_count`) giving proportional
  control of fountain overhead. The old formula pinned it at 100% with no way
  down, even though the improved decoder recovers large messages from about 5%.
  The default is unchanged.
- `rs_fec.encode_codeblock` / `decode_codeblock` / `encode_codeblocks`:
  genuine CCSDS RS(255,223) and RS(255,239) with symbol interleaving. The
  previous "RS(255,223)" claim referred to per-atom RS(48,32)/RS(64,32) codes,
  which remain available under their own names.
- `tmframe.frame_info`, `tmframe.split_space_packets`, `MODE_PACKET`.
- `spacepacket.SpacePacketSequenceCounter.set`.
- CLI: `pack-mckay`, `unpack-mckay`, `frame-tm --mode`, `simulate --seed`.
- `tests/test_regressions.py`, 69 tests pinning every defect above.

### Changed
- `mckay_vs_standard.py` rewritten: it previously printed literal format
  specifiers (`print(".3f")`) instead of numbers, so its published figures were
  not reproducible. It now verifies every reconstruction before reporting a
  ratio. The same defect in the two Rust benchmarks is repaired.
- `astral/mckay_usage_example.py` rewritten against the real API; it called
  `get_compression_stats()` and `get_integration_stats()`, which do not exist,
  and crashed on the first example.
- README rewritten so every performance and compliance claim matches measured
  behaviour in this repository.
- `stats()` reports the whole stream size, not just the payload.
- Verification and benchmark scripts force UTF-8 output, fixing
  `UnicodeEncodeError` crashes on a default Windows console.
- flake8 configuration consolidated into `setup.cfg`; the exclusions that hid
  `mckay_astral_integration.py` and the examples from linting are gone, and CI
  lints tests and scripts too.
- CI asserts the Rust extension is actually exercised and runs the standards
  verification scripts.
- `act.exe`, `act.zip` and `act-tool/` (about 44 MB) untracked and ignored.
  They remain in git history; removing them needs a history rewrite.

## 2026-03-24

### Fixed
- **Critical Bug Fixes for Deep-Space Reliability:**
  - Extended McKay header to include entropy coder information for cross-compatibility between Rust and Python paths
  - Improved exception handling with warning-based fallbacks instead of silent data corruption
  - Changed quantization from Q16 to Q12 to prevent overflow in telemetry data
  - Fixed text decompression routing to properly use entropy-aware decompression paths
  - Removed regex dependency from Rust code, replaced with manual string processing
  - Enabled Rust text compression with Python fallback for optimal performance
- Fixed unused variable warning in Rust extension (src/lib.rs)
- Updated pyproject.toml build configuration for proper package discovery

### Removed
- Archived legacy standalone scripts removed:
  - compress_text_demo.py
  - compress_video.py
  - compress_video_enhanced.py
  - mckay_comprehensive_test.py
  - test_fountain_status.py
  - test_mckay_fountain_corrected.py
  - test_mckay_fountain_integration.py
  - test_mckay_fountain_multitype.py
  - test_mckay_gist_first.py
  - test_voice_optimization.py

### Changed
- CLI cleanup in astral/cli.py:
  - Removed no-op options that were parsed but not used:
    - pack-text: --refine
    - pack-text-with-dict: --refine
    - pack-cmd: --refine
    - pack-cmd-batch: --refine, --key-id, --counter, --contact
- Removed dead contact parsing path in cmd_pack_cmd_batch.
- Removed redundant local crc import in astral/container.py parse_atoms.
- Updated README to remove references to deleted standalone scripts and point users to maintained astral.cli workflows.
- Updated .flake8 excludes to reflect removed files.
