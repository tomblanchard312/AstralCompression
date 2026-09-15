# GistLink 2.0.0

Formerly ASTRAL. The project and its compression engine are renamed to say what
they do; the CHANGELOG lists every moved name. The only wire-format change is
the two magic bytes of the compressed container, and nothing had been published
under the old ones.

This is the first release with a specified wire format, frozen test vectors, a
security review of the command path, and mission dictionaries.

## What this release is for

GistLink is a payload format and reference implementation for compressing and
delivering messages over lossy links: domain-aware compression, a replicated
metadata gist that survives heavy loss, and a fountain-coded body that
reassembles from whatever arrives. It can be carried inside CCSDS Space
Packets, TM Transfer Frames and Reed-Solomon codeblocks.

**Supported use:** ground-segment tooling, laboratory and testbed work,
research, and payload software on a Linux-class board.

**Not supported:** flight software on a primary mission. This is pure Python
with no software assurance regime behind it, and no flight heritage. See
"Scope and limits" below before designing it into anything that flies.

## Mission dictionaries

**Mission dictionaries.** A trained zstd dictionary beats every built-in
transform on short messages, which is the traffic this format exists for:
3,650 bytes against 4,578 for 100 short mission reports, 20% better. Train
with `gistlink train-dict`, pass `--dict` to pack and unpack. The dictionary is
shared configuration; a receiver without it reports the id it needs rather
than failing generically. Needs `gistlink[dict]`.

Set `GISTLINK_DICT` once and both ends use it without further flags.

Train on your own traffic: a dictionary only helps on data resembling its
training set, and a mismatched one is a regression. That is measured, not
theoretical, and it is why no built-in dictionary ships. Applying one is still
safe, because both encodings are produced and the smaller is sent.

This is the honest recommendation from benchmarking against the alternatives:
the built-in text transform is not competitive with a dictionary-trained
general codec, and the project is better for saying so than for defending its
own transform.

## Highlights

**A specified format.** [docs/FORMAT.md](docs/FORMAT.md) defines every byte,
completely enough to write an independent implementation, with
[`tests/test_vectors.py`](tests/test_vectors.py) as the conformance suite. An
implementation that reproduces those vectors is compatible.

**Correct CCSDS Reed-Solomon.** Earlier versions used the wrong generator
polynomial: primitive element alpha rather than alpha^11. It was a valid RS
code that no CCSDS ground station would decode. The parameters now match
libfec (`FCR=112, PRIM=11`, field polynomial `0x187`), verified against an
independent construction, and Berlekamp's dual basis is supported and used by
default as the standard specifies.

**Commanding fails closed.** Decoding a command verifies it by default and
refuses to return its contents otherwise. `PersistentReplayGuard` rejects
replayed or reordered counters and keeps its high-water mark on disk, written
durably before the command is accepted, so replay protection survives a
receiver restart. `unpack_stream` accepts a key and reports
`command_authenticated`; without one, commands are tagged
`authenticated: False` and the CLI warns on stderr.

**End-to-end integrity.** A CRC-32 covering the header and payload together
catches the corrupt atom that slips past its own CRC-8, and replicated headers
are chosen by majority vote so a single damaged copy is outvoted rather than
deciding how the message is read.

**Usable speed in pure Python.** A 200 KB message packs in about 0.45 s,
down from 3.9 s, with no change to the wire format.

## Compatibility

| Format | Version | Reads older? |
|---|---|---|
| Atom | 2 | Yes, format 1 decodes as unverified |
| compressed stream | 3 | Yes, format 2 decodes; a truncated v2 length is reported, not guessed |
| Text payload | 2 | Yes, format 1 decodes (it was case- and whitespace-lossy) |
| Command | 1 | Unchanged on the wire; the receiving API is stricter |

**Breaking changes from 0.6.x:**

* TM frames changed on the wire. The randomizer sequence was wrong, it did not
  cover the whole frame, and every frame declared "no packet starts here". A
  receiver running 0.6.x will not decode 1.0.0 frames, which is the point.
* Reed-Solomon codewords changed, for the generator reason above.
* `decode_cmd` and `decode_cmd_batch` raise `CommandAuthError` instead of
  returning an unverified command. Pass `require_auth=False` to inspect one.
* `container.parse_atoms` returns `Atom` named tuples. Indexes 0-4 are
  unchanged; five-way tuple unpacking needs a sixth name.
* `stats()` reports the whole stream size, not just the payload.

## Install

```bash
pip install gistlink            # core, no dependencies
pip install gistlink[rs]        # + Reed-Solomon
pip install gistlink[dict]      # + mission dictionaries
pip install gistlink[voice]     # + Codec2 voice
pip install gistlink[fast]      # + Rust extension and zstd
pip install gistlink[all]
```

Python 3.9 through 3.13, tested on the 3.9-3.12 matrix in CI.

## Verification

* 354 tests pass, 23 skip without optional extras; the suite also passes with
  every extra absent.
* 20 of those are interop tests in which third-party CCSDS libraries, not this
  one, parse the frames.
* Lint clean across the package, tests and benchmarks.
* The decoder was fuzzed with 4,000 hostile inputs (random bytes, bit-flipped
  streams, shuffled fragments): no uncaught exceptions, nothing slower than a
  second.
* The wheel installs into a clean virtual environment and the `gistlink`
  console script runs.

## Scope and limits

Read these before relying on it.

1. **Interop is verified in software, not over the air.** Two unrelated CCSDS
   libraries parse what GistLink transmits, in
   [`tests/test_interop.py`](tests/test_interop.py): `spacepackets` reads the
   TM Transfer Frames and checks the Frame Error Control Field with its own
   CRC-16, and `ccsdspy` reads the Space Packet stream a second time and
   reports no structural faults. The derandomiser in that module is written
   from the polynomial in the standard, not copied from this library, and it
   reproduces the published Table 9-1 vector. What has not happened is a run
   against a real receiver on a real link: no COSMOS, OpenMCT or
   gr-satellites deployment, and no RF. Those would test the radio path and
   the operator tooling, not the byte format, which is what the loopback
   settles. The Reed-Solomon codeblock is the one layer with no third-party
   reader available; it stays verified against the libfec parameters and an
   independent `galois` construction.
2. **CCSDS framing gives transport compatibility, not end-to-end decoding.** A
   ground station will synchronise, derandomise, check the FECF and route by
   APID without custom code. It will not understand the GistLink atoms inside;
   the gist, fountain decoding and decompression need this library at
   the receiving end.
3. **Telemetry compression is lossy** by design (Q12 quantisation, about
   1.2e-4 relative error). Use `BINARY` when you need bit-exact floats.
4. **One message is capped near 512 KB** by the 16-bit atom counters. There is
   no streaming API for larger payloads yet. There is also a floor: the
   smallest message costs about 19 atoms (608 bytes) once header replication,
   the gist and the minimum fountain redundancy are counted. Below a kilobyte
   the framing dominates and compression barely matters; tune
   `min_redundancy` and `header_redundancy` instead.
5. **The gist survives only as long as one header atom does.** Size
   `header_redundancy` for the loss your link actually sees;
   `header_redundancy_for(0.8)` returns 21 copies for 99% survival at 80% loss.
6. **CRC-32 and CRC-8 detect noise, not tampering.** HMAC on commands is the
   only authentication in the format, and there is no confidentiality at all.
   The HMAC construction has not been reviewed by a third party.
7. **Replay state is a single-writer file.** `PersistentReplayGuard` assumes
   one process per link; two guarding the same link with the same file will
   race. Treat the state file as security state: deleting it disables replay
   protection.
8. **Voice needs `pycodec2`**, and those paths are not exercised in CI.
9. **Publishing has never been exercised end to end.** The workflow now
   builds and verifies the package before publishing, but no release has been
   cut, so the first `pip install gistlink` from PyPI will be the first real
   test of it.
10. **The Rust extension is not published as a wheel.** It is built and
    round-trip verified in CI, so the fast path works where it is built. Build it with
   `maturin build --release` in `gistlink_native/`. The pure Python path is
   the supported one.

## Next

* A run against a live receiver, to cover the radio path and operator tooling
  that a software loopback cannot.
* Publish Rust wheels for the common platforms.
* A streaming API for payloads above the single-message ceiling.
