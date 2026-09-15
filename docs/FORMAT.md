# ASTRAL Wire Format Specification

Version 1.0, atom format 2, McKay format 3.

This document defines the bytes ASTRAL puts on the wire, completely enough to
write an independent implementation. Where a value is fixed, it is stated
here; where the reference implementation is the authority, that is said
explicitly.

Frozen test vectors for every layer below live in
[`tests/test_vectors.py`](../tests/test_vectors.py). An implementation that
reproduces those bytes is compatible.

All multi-byte integers are **little-endian** unless stated otherwise. CCSDS
layers use big-endian, as those standards require, and say so.

---

## 1. Layering

```
  optional   CCSDS TM Transfer Frame   (section 6)
  optional   CCSDS Reed-Solomon codeblock (section 7)
  optional   CCSDS Space Packet        (section 5)
  required   ASTRAL atom stream        (section 2)
               HEADER_GIST atoms       (section 3)
               FOUNTAIN_PACKET atoms   (section 4)
               MCKAY_GIST atoms        (section 8)
               DICT_UPDATE atoms       (section 9)
  payload    grammar / text / command / McKay stream
```

Each outer layer is independent: an atom stream is valid on its own.

---

## 2. Atom

Every atom is exactly **32 bytes**.

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | Sync word, `0xA5 0xE6` |
| 2 | 1 | Atom format version. `1` = original, `2` = header carries the integrity CRC |
| 3 | 2 | `atom_index`, uint16 LE |
| 5 | 2 | `total_atoms`, uint16 LE |
| 7 | 2 | `message_id`, uint16 LE |
| 9 | 1 | `atom_type` (see below) |
| 10 | 21 | Payload, zero-padded |
| 31 | 1 | CRC-8 over bytes 0..30 |

`atom_type`:

| Value | Name | Section |
|---|---|---|
| 0 | `HEADER_GIST` | 3 |
| 1 | `FOUNTAIN_PACKET` | 4 |
| 2 | `DICT_UPDATE` | 9 |
| 3 | `MCKAY_GIST` | 8 |

**CRC-8**: polynomial `0x1D`, initial value `0xFF`, final XOR `0xFF`, MSB
first (CRC-8/J1850). `crc8(b"123456789") == 0x4B`.

**Receiving**: scan the byte stream for the sync word rather than assuming
alignment. Accept an atom only when both the sync word and the CRC-8 check
out, then resume scanning at the end of that atom. A stream that starts
mid-atom or contains gaps must still decode.

`message_id` is a random 16-bit value identifying one message. A receiver
groups atoms by it, taking the id of the first valid atom.

---

## 3. HEADER_GIST payload

21 bytes. Replicated: a sender emits several identical copies so the gist
survives loss. A receiver that sees several copies takes the value the
**majority** agree on.

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | `K`, number of source blocks, uint16 LE |
| 2 | 1 | `symbol_size`, always 16 |
| 3 | 4 | `fountain_seed`, uint32 LE |
| 7 | 3 | `payload_len`, uint24 LE |
| 10 | 1 | `gist_bits`, always 33 |
| 11 | 5 | Packed gist bits (section 3.1) |
| 16 | 4 | Integrity CRC-32, uint32 LE (section 3.2) |

### 3.1 Gist bits

33 bits packed from offset 11, **least significant bit first**: each field is
written into the accumulator at the current bit position and whole bytes are
emitted low byte first. Writing a 3-bit field of value 1 produces `0x01`, not
`0x20`. Fields appear in this order:

| Bits | Field |
|---|---|
| 3 | Message type |
| 7 | Object id |
| 10 | Coarse latitude, `round((lat + 90) / 180 * 1023)` |
| 10 | Coarse longitude, `round((lon + 180) / 360 * 1023)` |
| 3 | Coarse confidence, `round(conf * 7)` |

Message types: 1 `DETECT`, 2 `STATUS`, 3 `TEXT`, 4 `VOICE`, 5 `CMD`,
6 `CMD_BATCH`, 7 `MCKAY`. Object ids: 1 `H2O_ICE`, 2 `CH4_ICE`, 3 `BASALT`,
4 `UNKNOWN`.

### 3.2 Integrity CRC-32

CRC-32 (the standard zlib polynomial) over **the header followed by the
payload**:

```
normalised_header = header[0:16] + b"\x00\x00\x00\x00" + header[20:21]
crc = crc32(payload, crc32(normalised_header))
```

The header's own CRC slot is zeroed for the computation. The header is
included because it decides how the payload is interpreted: covering the
payload alone leaves a corrupt gist type able to select the wrong decoder
while the checksum still matches.

A receiver must verify this before reporting a successful decode. On mismatch,
report failure and keep the gist; do not return the payload.

Atom format 1 has no CRC here; a receiver treats such a stream as unverified.

---

## 4. FOUNTAIN_PACKET payload

21 bytes.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | `packet_seed`, uint32 LE |
| 4 | 1 | `degree` (informational; the decoder recomputes it) |
| 5 | 16 | XOR of the selected source blocks |

### 4.1 Source blocks

The payload is split into `K` blocks of 16 bytes, the last zero-padded. An
empty payload yields one zero block.

### 4.2 PRNG

xorshift32 (Marsaglia), shift triple 13, 17, 5:

```
x ^= (x << 13) & 0xFFFFFFFF
x ^= x >> 17
x ^= (x << 5)  & 0xFFFFFFFF
```

Seed 0 is replaced by 1. Test vector: from seed 1 the first five outputs are
270369, 67634689, 2647435461, 307599695, 2398689233.

`random()` returns `next_u32() / 2**32`. `getrandbits(k)` returns
`next_u32() >> (32 - k)`.

### 4.3 Degree distribution

Robust soliton with `c = 0.1`, `delta = 0.05`:

```
ideal[1] = 1/K;  ideal[d] = 1 / (d * (d - 1))       for d >= 2
R = c * ln(K / delta) * sqrt(K)
pivot = max(1, floor(K / max(R, 1)))
tau[d] = R / (d * K)                 for d < pivot
tau[pivot] = R * ln(R / delta) / K
tau[d] = 0                           for d > pivot
dist[d] = (ideal[d] + tau[d]) / sum(ideal + tau)
```

A degree is drawn by taking `r = random()` and returning the smallest `d >= 1`
whose cumulative probability reaches `r`, clamped to `1..K`.

### 4.4 Packet construction

With the message RNG seeded from `fountain_seed`, for each packet in turn:

1. `packet_seed = rng.getrandbits(32)`; store it in the atom.
2. Seed a second RNG with `packet_seed`.
3. Draw `degree` from the distribution.
4. Draw `degree` distinct block indices (section 4.5).
5. XOR those blocks together to form the 16-byte payload.

`K == 1` is special-cased: every packet is `(fountain_seed, 1, block0)`.

### 4.5 Index selection

Partial Fisher-Yates over `range(K)`, using the packet RNG:

```
for i in 0 .. degree-1:
    j = i + (rng.next_u32() % (K - i))
    swap pool[i], pool[j]
    emit pool[i]
```

An implementation may keep the pool sparse (only touched positions) as long as
the emitted sequence is identical. Test vector: seed 12345, K=100, degree 5
gives `[30, 85, 40, 9, 87]`.

### 4.6 Decoding

Reconstruct each packet's index set from its `packet_seed` using the same
procedure, then solve the system over GF(2). Peeling degree-1 equations alone
is not sufficient: it stalls when no degree-1 equation remains even though the
packets are independent. Follow it with Gaussian elimination.

The payload is the concatenation of blocks `0..K-1`, truncated to
`payload_len`.

---

## 5. CCSDS Space Packet (optional)

CCSDS 133.0-B-2. Six-byte primary header, big-endian, followed by the atom
stream as the user data field.

| Bits | Field | Value |
|---|---|---|
| 0-2 | Packet version | `000` |
| 3 | Packet type | 0 TM, 1 TC |
| 4 | Secondary header flag | 0 |
| 5-15 | APID | see below |
| 16-17 | Sequence flags | `11` (standalone) |
| 18-31 | Sequence count | per-APID, mod 16384 |
| 32-47 | Data length | octets minus one |

APIDs: `DETECT` 0x010, `STATUS` 0x011, `TEXT` 0x012, `VOICE` 0x013 (all TM);
`CMD` 0x100, `CMD_BATCH` 0x101 (TC). Idle packets use APID 0x7FF.

---

## 6. CCSDS TM Transfer Frame (optional)

CCSDS 132.0-B-3 framing with the CCSDS 131.0-B randomizer. Fixed 1115-byte
frames, 1119 bytes on the wire including the ASM.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | ASM `0x1ACFFC1D`, never randomised |
| 4 | 2 | Version (`00`), SCID (10 bits), VCID (3), OCF flag (0) |
| 6 | 1 | Master channel frame count, mod 256 |
| 7 | 1 | Virtual channel frame count, mod 256 |
| 8 | 2 | Transfer frame data field status |
| 10 | 1107 | Data field, padded with `0xE0` or idle packets |
| 1117 | 2 | FECF: CRC-16-CCITT-FALSE over offsets 4..1116 |

**Data field status**, two modes:

* **VCA** (opaque SDU, the default for a raw atom stream): sync flag = 1, all
  other fields zero. Value `0x4000`.
* **PACKET** (the data field holds Space Packets): sync flag = 0, segment
  length id = `11`, and a real first header pointer: the offset of the first
  packet that starts in this frame, `0x7FF` when none does, `0x7FE` for an
  idle frame. Short frames are filled with an idle packet.

**CRC-16-CCITT-FALSE**: polynomial `0x1021`, initial value `0xFFFF`, no final
XOR. `crc16(b"123456789") == 0x29B1`.

**Randomizer**: CCSDS 131.0-B, `h(x) = x^8 + x^7 + x^5 + x^3 + 1`, all-ones
initial state, equivalently `a(n) = a(n-1) ^ a(n-3) ^ a(n-5) ^ a(n-8)`. The
sequence begins `FF 48 0E C0 9A 0D 70 BC` and has a period of 255 bytes. It is
applied to the **entire transfer frame**, header and FECF included, and never
to the ASM.

**Receiving**: search for the ASM rather than assuming frame alignment.

---

## 7. CCSDS Reed-Solomon (optional)

RS(255,223) and RS(255,239) over GF(2^8).

| Parameter | Value |
|---|---|
| Field polynomial | `0x187` (x^8 + x^7 + x^2 + x + 1) |
| First consecutive root (FCR) | 112 |
| Primitive element exponent (PRIM) | 11 |
| Generator polynomial | product of (x - alpha^(11 * (112 + i))), i = 0..nroots-1 |
| Interleave depth | 5 by default; 1115 data bytes give a 1275-byte codeblock |

The primitive element exponent of 11 is what makes this the CCSDS code rather
than merely an RS code over the same field. Using alpha^1 produces a valid but
incompatible code that no CCSDS ground station will decode.

**Symbol basis.** CCSDS specifies Berlekamp's **dual basis** for symbols on the
wire; the arithmetic is done in the conventional basis. Conversion is a linear
map over GF(2)^8 built from the seed
`8D EF EC 86 FA 99 AF 7B`:

```
taltab[i] = XOR of tal[7-k] for each bit k set in i      # conventional -> dual
tal1tab   = inverse permutation of taltab                # dual -> conventional
```

To encode in the dual basis: map the data through `tal1tab`, encode
conventionally, map the parity back through `taltab`. The data bytes are
unchanged on the wire. This matches libfec's `encode_rs_ccsds`; the
conventional variant matches `encode_rs_8`. **Both endpoints must agree**,
and mismatched bases are the classic CCSDS RS interop failure.

Interleaving: codeword `j` takes every `interleave`-th byte starting at
offset `j`, and the codewords are re-interleaved on output, so a burst error
is spread across codewords. At interleave 5, RS(255,223) corrects any burst up
to 80 bytes.

ASTRAL also defines per-atom codes RS(48,32) and RS(64,32) over the same
field and generator, for repairing a damaged atom instead of discarding it.
These are **not** CCSDS codeblocks.

---

## 8. MCKAY_GIST payload

21 bytes, replicated like the header, present only in McKay messages.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | McKay format version |
| 1 | 1 | Transform id |
| 2 | 1 | Data type id |
| 3 | 4 | Original size, uint32 LE |
| 7 | 4 | Compressed size, uint32 LE |
| 11 | 1 | Channel count |
| 12 | 1 | Entropy coder id |
| 13 | 8 | Reserved, zero |

Data type ids: 0 `AUTO`, 1 `TEXT`, 2 `TELEMETRY`, 3 `VOICE`, 4 `BINARY`,
5 `IMAGE`.

### 8.1 McKay stream (format 3)

The message payload is a McKay stream: a 10-byte header followed by the
transform payload.

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | Magic `MK` |
| 2 | 1 | Version, 3 |
| 3 | 1 | Transform id |
| 4 | 4 | Original length, uint32 LE |
| 8 | 1 | Channel count |
| 9 | 1 | Entropy coder |

Transform ids: 0 passthrough, 1 text, 2 telemetry, 3 Codec2 voice, 4 binary
float, 5 zstd with a mission dictionary. Entropy coders: 0 LZMA, 1 zlib,
2 zstd, 0xFF none.

### 8.2 Compact container (format 4)

Transform 5 uses a three-byte container instead, because its payload already
describes itself:

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | Magic `MK` |
| 2 | 1 | `(4 << 4) | transform`, so `0x45` for the dictionary transform |
| 3 | .. | A bare zstd frame |

The zstd frame records both its decompressed size and the id of the
dictionary it needs, so a length field and an entropy field would be
duplication. This matters: the ten-byte v3 header was 23% of a typical
33-byte compressed status message.

A reader distinguishes the two containers by the high nibble of byte 2. For
v1 to v3 that byte is the version (1, 2 or 3); for the compact container it is
4. Since no v3 transform id reaches 0x40, the encodings cannot be confused.

### 8.3 Mission dictionaries

A dictionary is a zstd dictionary trained on representative traffic, shared
out of band and identified by the id zstd stamps into every frame. A receiver
that lacks it must report the id rather than a generic decode failure: the
operator needs to know which artifact to fetch. It cannot be derived from the
payload, so treat it as mission configuration and version it.

Format 2 is identical except the original length is 2 bytes, capping it at
65535; readers should accept it, and must reject a version 2 stream whose
length field reads 65535 as unrecoverable rather than returning truncated data.

**Telemetry transform** (lossy): per channel, store big-endian float32
minimum and span, quantise to 12 bits as
`round((v - min) / span * 4095)`, store the first sample as uint16 BE and the
remaining samples as int16 BE deltas clamped to ±2047. Layout is all channel
metadata, then all first samples, then all deltas.

**Binary float transform**: reorder an array of float32 so that all byte 0s
are contiguous, then all byte 1s, and so on, before entropy coding.

**Text transform**: replace mission vocabulary words with a marker
`0x1E` followed by two lowercase hex digits (the word index) and one digit for
case (0 lower, 1 title, 2 upper), then entropy code.

---

## 9. DICT_UPDATE payload

21 bytes per atom:

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | Flags; bit 0 set means another DICT_UPDATE atom follows |
| 1 | 20 | UTF-8 word bytes, words separated by `0x00` |

A receiver concatenates the word bytes from successive atoms until it sees a
flags byte with bit 0 clear, strips trailing `0x00` padding, and splits on
`0x00`.

This is informational: it announces mission vocabulary to an operator and does
not change how the text payload decodes.

---

## 10. Text payload (format 2)

Byte 0 is the version (2), byte 1 is flags (0). Then a sequence of records:

| Tag | Meaning | Follows |
|---|---|---|
| 0 | Dictionary word, lower case | LEB128 index |
| 1 | Literal | LEB128 byte length, then UTF-8 |
| 2 | Dictionary word, Title case | LEB128 index |
| 3 | Dictionary word, UPPER case | LEB128 index |
| 4 | Explicit separator | LEB128 byte length, then UTF-8 |

Dictionary indices are 1-based into the base lexicon (see
`astral/textpack.py`, which is the authority for its contents and order).

Separators are implicit where they follow the common shape: a single space
before a token starting with an alphanumeric, nothing before punctuation, and
nothing before the first token. Any separator differing from that is written
as an explicit record placed immediately before the token it precedes; a
trailing separator appears at the end with no following token. This makes the
encoding exact for any input string while keeping ordinary prose compact.

Format 1 had no case tags and no separator records, so it was lossy; readers
should accept it for old data.

---

## 11. Command payload

Byte 0 is a version/flags byte (`0x01`), then a LEB128 command id, then the
command's arguments.

| Id | Name | Arguments |
|---|---|---|
| 1 | `SET_MODE` | 1 byte mode: 0 SAFE, 1 NORMAL, 2 SCIENCE |
| 2 | `POINT` | azimuth and elevation, each `round(deg * 10000)` as signed 24-bit LE |
| 3 | `BURN` | LEB128 thruster id, LEB128 duration in ms |
| 4 | `SCHED_WAKE` | LEB128 TAI offset in seconds |
| 5 | `REBOOT` | none |
| 6 | `UPLOAD_CHUNK` | LEB128 sequence, LEB128 length, bytes |
| 7 | `APPLY_UPDATE` | none |

### 11.1 Authentication

When a key is used, a 36-byte trailer is appended: a 4-byte big-endian counter
followed by HMAC-SHA256 over `counter || body`, where `body` is everything
before the trailer.

A receiver **must**:

* reject a command whose MAC is absent or does not verify;
* reject a command whose counter is not strictly greater than the last
  accepted counter for that key, and not advance its window on rejection;
* keep that high-water mark across restarts, and record it durably before
  acting on the command. A receiver that forgets it on restart offers no
  replay protection at all;
* never present an unverified command as if it were verified.

The counter is 32 bits and must not wrap; rekey instead. CRC-32 and CRC-8
elsewhere in this format detect noise, not tampering; this HMAC is the only
authentication in ASTRAL.

### 11.2 Command batch

Byte 0 version (1), byte 1 flags (bit 0 rollback on fail, bit 1 halt on
error), LEB128 item count, then per item: LEB128 TAI offset, LEB128 body
length, command body. The optional trailer of section 11.1 covers the whole
batch, so verifying a batch verifies every command in it; the individual
commands are not separately signed.

---

## 12. Grammar payload

For `DETECT` and `STATUS`: LEB128 type id, LEB128 subject id, LEB128 object
id, then bit-packed least significant bit first (section 3.1): latitude as
28-bit two's complement
`round(deg * 1e6)`, longitude as 29-bit two's complement, depth as 11 bits of
`round(m * 10)`, confidence as 8 bits of `round(conf * 255)`.

Subject ids: 1 `KESTREL-1`, 2 `KESTREL-2`, 3 `ORION-A`.

---

## 13. Limits

| Limit | Value |
|---|---|
| Atoms per message | 65535 (16-bit counters) |
| Payload per message | about 512 KB; see `codec.max_payload_bytes()` |
| McKay original length | 4 GB (uint32) |
| Command counter | 2^32, must not wrap |
| Space Packet user data | 65536 bytes |

---

## 14. Conformance

An implementation is compatible if it reproduces every vector in
`tests/test_vectors.py`. At minimum check: the xorshift32 sequence, the index
selection vector, the CRC-8 and CRC-16 check values, the randomizer sequence,
the RS parity for both bases, and the atom layout.
