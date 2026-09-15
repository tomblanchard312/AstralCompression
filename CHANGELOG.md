# Changelog

## 2.0.0 — renamed to GistLink

The project was ASTRAL, and its compression engine was named after a fictional
television character. Neither name said what the software does. It is now
**GistLink**: gist-first, loss-tolerant messaging for one-way links.

### Breaking
- **Distribution renamed**: `pip install gistlink`, not `astral-compression`.
- **Package renamed**: `import gistlink`, not `astral`.
- **CLI renamed**: `gistlink`, not `astral`.
- **Engine module**: `gistlink.compress`, was `astral.mckay_astral_integration`.
- **API**: `pack_compressed_message` / `unpack_compressed_stream`, were
  `pack_mckay_message` / `unpack_mckay_stream`. CLI subcommands `pack-file` /
  `unpack-file`, were `pack-mckay` / `unpack-mckay`.
- **Result key**: `result["compression"]`, was `result["mckay"]`.
- **Environment variable**: `GISTLINK_DICT`, was `ASTRAL_DICT`.
- **Container magic**: `GL`, was `MK`. This is the one wire-format change; the
  atom format, fountain code, CCSDS layers and every other byte are untouched.
  Nothing was published under the old magic, so no deployed receiver breaks.
- `spacepacket.unwrap()` returns `gistlink_stream`, was `astral_stream`.
- The Rust crate is `gistlink_native`, was `astral_compress`.

No back-compatibility shims are provided: nothing had been published under the
old names, so shims would be dead weight from the first release.

### Changed
- The README leads with what the software does rather than a backronym, and
  the fictional framing is gone.

## 1.1.0

### Added
- **Mission dictionaries** (`gistlink.dictionary`): trained zstd dictionaries
  for short messages, which is where a general compressor has least context
  and where GistLink is meant to operate. Measured on 100 short messages
  compressed individually: 7,049 bytes with plain zstd -19, 4,578 with the
  built-in text transform, 3,650 with a trained dictionary. `train-dict` on
  the CLI, `--dict` on `pack-file` and `unpack-file`, and `dictionary=` /
  `dictionaries=` on the Python API.
- A compact three-byte container (container format 4) for the dictionary
  transform. A zstd frame already records its decompressed size and the
  dictionary it needs, so the ten-byte v3 header was pure duplication, and at
  23% of a typical 33-byte compressed message it was not affordable.
- `MissingDictionaryError` and a `missing_dictionary` key on decode results.
  A receiver lacking a dictionary reports its id, which is actionable, rather
  than failing as a generic decode error.

- `GISTLINK_DICT` environment variable: name a dictionary once and every pack
  and unpack uses it, instead of threading `--dict` through every call.

### Fixed (native extension)
- **The Rust text compressor shared the abbreviation bug** and would corrupt
  marker-bearing text if called directly. It now refuses such input, so the
  function is safe standalone and not only behind the Python guard.
- **Fallback warnings no longer cry wolf.** "Rust text compression failed"
  fired whenever the extension was handed input it legitimately cannot
  represent (non-UTF-8 text, a deliberately malformed stream), which reads as
  a broken accelerator. Those inputs are now filtered before the call, so a
  warning means something genuinely unexpected, and it names the error.
- CI verifies the extension **round-trips** rather than merely imports. A
  silently broken accelerator passes every other test in the suite, because
  the Python fallback covers for it.

### Fixed (release process)
- **The release workflow never built the package it releases.** It ran maturin
  in `gistlink_native/` and published only that, so a release would have
  shipped the Rust accelerator without the library, the CLI, or anything
  importable as `gistlink`. It now builds the wheel and sdist, checks them
  with twine, installs the wheel into a clean environment and exercises both
  the API and the console script before publishing, and refuses to publish a
  set that does not contain the package itself. Raised in review on PR #6.

### Changed
- New `dict` extra: `pip install gistlink[dict]`.
- **A dictionary can no longer make a payload larger.** Both the dictionary
  and the built-in transform are produced and the smaller is sent. This
  matters because a dictionary only helps on traffic resembling its training
  set: measured, a mission-vocabulary dictionary made JSON status messages 8%
  bigger and log lines 2% bigger. It is also why no built-in dictionary is
  shipped, which had been the plan until it was measured.

### Fixed
- `compress(data, "TEXT")` raised `UnicodeDecodeError` on bytes that are not
  valid UTF-8, turning a caller's wrong type hint into a lost message. The
  abbreviation step is skipped and the data is entropy-coded instead.

## 1.0.0

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the release summary, compatibility
table and scope limits.

### Fixed (correctness, breaking on the wire)
- **The CCSDS Reed-Solomon code used the wrong generator polynomial.** It built
  roots alpha^112..alpha^143 (primitive element alpha), but CCSDS uses
  alpha^(11*(112+i)) (primitive element alpha^11, `PRIM=11` in libfec). The old
  codewords were a valid RS code that no CCSDS ground station would decode. The
  generator is now verified against an independent construction with the
  `galois` library, and the parity bytes are frozen as test vectors.
- **Berlekamp dual-basis symbols are supported and are now the default**, as
  CCSDS specifies. `basis="conventional"` selects libfec's `encode_rs_8`
  representation. Mismatched bases are the classic CCSDS RS interop failure.

### Fixed (security)
- **Command authentication failed open.** `unpack_stream` never verified an
  HMAC at all; an unsigned command decoded identically to a signed one apart
  from a flag; decoding without a key omitted the flag entirely, so
  `.get("auth_ok", True)` passed; and replay was unrestricted. Now: decoding
  verifies by default and raises `CommandAuthError` otherwise, every result
  carries `authenticated`, `unpack_stream` takes a key and reports
  `command_authenticated`, and `ReplayGuard` rejects stale counters without
  advancing its window on a failed MAC. `CommandSequencer` is the sender-side
  counterpart.

### Added
- `PersistentReplayGuard`: replay protection that survives a receiver
  restart. The in-memory `ReplayGuard` reset to accepting everything when the
  process bounced, which left the replay window open in exactly the situation
  it was meant to close. The counter is written atomically and fsynced before
  the command is accepted, so a crash can lose a command but never execute one
  twice; an unreadable state file is an error rather than a silent reset; and
  the CLI gained `--replay-state` and `--link-id`.
- `docs/FORMAT.md`: the wire format specification, complete enough for an
  independent implementation.
- `tests/test_vectors.py`: frozen wire-format vectors, with provenance noted
  per vector; the Reed-Solomon ones come from an independent encoder.
- `tests/test_ccsds.py`: CCSDS conformance tests, consolidating the checks
  that used to live in the PHASE3/4/5 development scripts.
- `gistlink` console script, packaging metadata, classifiers and project URLs.

### Changed
- Documentation reorganised: `docs/` holds the specification, integration
  guide and quick reference; `benchmarks/` holds the benchmark scripts.
- Removed development artefacts superseded by the test suite:
  `PHASE3/4/5_VERIFICATION.py`, `verify_fixes.py`, `verify_core_fixes.py`,
  `FIXES_VERIFICATION.py`, and the `README_COMPRESS_ASTRAL.md` stub.
- Version 1.0.0.

## 2026-09-14 (second pass)

### Fixed (data integrity)
- **The integrity checksum covers the header, not just the payload.** The
  header decides how the payload is read (the gist type selects the decoder),
  so a corrupt header atom that passed its own CRC-8 could turn an intact TEXT
  payload into a fabricated STATUS report, complete with invented lat/lon, and
  still report `integrity_ok: True`. The CRC-32 now spans the header (with its
  own checksum slot zeroed) and the payload together. Raised by Codex on PR #5.
- **Replicated headers are chosen by majority vote.** The decoder used
  whichever copy arrived first, so a single damaged copy decided how the
  message was read. One corrupt copy is now outvoted and the message decodes
  normally.
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
- **compressed streams over 64 KiB no longer decode to corrupt output.** The
  original-length field was 16 bits, so TELEMETRY and BINARY payloads of 65535
  bytes or more came back truncated with no error (160,000 bytes in, 65,532
  out). The field is now 32 bits (format v3) and every reconstruction is
  length-checked. v2 streams still decode; v2 streams whose length was
  truncated when written now raise instead of returning junk.
- **The Rust fast path is no longer selected against a stub.** The un-built
  `gistlink_native/` source directory imports as an empty namespace package,
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
  parsers that no packet ever starts. Opaque GistLink streams now use VCA
  framing (sync flag 1); the new `MODE_PACKET` carries real Space Packets with
  a correct First Header Pointer and idle-packet fill.
- **`gistlink.cli wrap-sp` wraps its input.** It was discarding the file it read
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
- `codec.pack_compressed_message` / `unpack_compressed_stream`: compression
  carried over the fountain/atom layer with a replicated `COMPRESSED_GIST` atom.
  This is the GistLink integration the documentation described; no code
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
- CLI: `pack-file`, `unpack-file`, `frame-tm --mode`, `simulate --seed`.
- `tests/test_regressions.py`, 69 tests pinning every defect above.

### Changed
- `compression_benchmark.py` rewritten: it previously printed literal format
  specifiers (`print(".3f")`) instead of numbers, so its published figures were
  not reproducible. It now verifies every reconstruction before reporting a
  ratio. The same defect in the two Rust benchmarks is repaired.
- `gistlink/usage.py` rewritten against the real API; it called
  `get_compression_stats()` and `get_integration_stats()`, which do not exist,
  and crashed on the first example.
- README rewritten so every performance and compliance claim matches measured
  behaviour in this repository.
- `stats()` reports the whole stream size, not just the payload.
- Verification and benchmark scripts force UTF-8 output, fixing
  `UnicodeEncodeError` crashes on a default Windows console.
- flake8 configuration consolidated into `setup.cfg`; the exclusions that hid
  `compress.py` and the examples from linting are gone, and CI
  lints tests and scripts too.
- CI asserts the Rust extension is actually exercised and runs the standards
  verification scripts.
- `act.exe`, `act.zip` and `act-tool/` (about 44 MB) untracked and ignored.
  They remain in git history; removing them needs a history rewrite.

## 2026-03-24

### Fixed
- **Critical Bug Fixes for Deep-Space Reliability:**
  - Extended container header to include entropy coder information for cross-compatibility between Rust and Python paths
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
  - compress_comprehensive_test.py
  - test_fountain_status.py
  - test_compress_fountain_corrected.py
  - test_compress_fountain_integration.py
  - test_compress_fountain_multitype.py
  - test_compression_gist_first.py
  - test_voice_optimization.py

### Changed
- CLI cleanup in gistlink/cli.py:
  - Removed no-op options that were parsed but not used:
    - pack-text: --refine
    - pack-text-with-dict: --refine
    - pack-cmd: --refine
    - pack-cmd-batch: --refine, --key-id, --counter, --contact
- Removed dead contact parsing path in cmd_pack_cmd_batch.
- Removed redundant local crc import in gistlink/container.py parse_atoms.
- Updated README to remove references to deleted standalone scripts and point users to maintained gistlink.cli workflows.
- Updated .flake8 excludes to reflect removed files.
