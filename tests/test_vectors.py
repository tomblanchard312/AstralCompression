"""
Frozen wire-format test vectors.

These pin the bytes GistLink puts on the wire so that an independent
implementation (the Rust extension, a C port, a ground-station plugin) can be
checked against the same values, and so that an accidental change to a
format-defining routine fails here rather than in the field.

Provenance matters, and differs by vector:

* The Reed-Solomon parity, the CCSDS randomizer sequence and the CRC-16 check
  value are anchored **externally**, to the CCSDS standard and to values an
  independent implementation produces.
* The atom layout, text payload, fountain packets and integrity CRC are
  **frozen current behaviour**: they do not prove correctness on their own,
  they pin the wire format so a port can be checked against it and so an
  accidental change is caught.

The Reed-Solomon vectors were produced by an independent systematic encoder
built on the `galois` library from the CCSDS 131.0-B generator polynomial, not
by the code under test; both implementations agree byte for byte.

Regenerating (requires `pip install galois`):

    g(x) = product over i in 0..nroots-1 of (x - alpha^(11*(112+i)))
    parity = (message(x) * x^nroots) mod g(x)
    over GF(2^8) with field polynomial 0x187 and primitive element alpha = 2
"""

from __future__ import annotations

import pytest

from gistlink import codec, container
from gistlink.crc import crc16_ccitt, crc8_j1850
from gistlink.fountain import _Xorshift32, lt_encode_blocks
from gistlink.textpack import decode_text, encode_text
from gistlink.tmframe import apply_prng

try:
    from gistlink import rs_fec

    RS_AVAILABLE = True
except ImportError:  # optional extra
    rs_fec = None
    RS_AVAILABLE = False


# --------------------------------------------------------------------------
# Reed-Solomon, CCSDS 131.0-B
# --------------------------------------------------------------------------

RS_MESSAGE_223 = bytes((i * 7 + 3) % 256 for i in range(223))
RS_PARITY_223_CONVENTIONAL = bytes.fromhex(
    "3f56af8183b8ad235310d48f4ce7c60e458d1948b674923ab100c186f0bc1519"
)
RS_PARITY_223_DUAL = bytes.fromhex(
    "0c29d565dd7fb9654ba23207f8d9962e04699784e7e28d63c017bad54b582bc4"
)
RS_MESSAGE_239 = bytes((i * 11 + 5) % 256 for i in range(239))
RS_PARITY_239_CONVENTIONAL = bytes.fromhex("8b6ea2698b65d54964be2bfc22492915")


@pytest.mark.skipif(not RS_AVAILABLE, reason="requires the 'rs' extra (reedsolo)")
class TestReedSolomonVectors:
    def test_ccsds_parameters(self):
        """The parameters that make this the CCSDS code and not merely an RS code."""
        assert rs_fec.CCSDS_FIELD_POLY == 0x187
        assert rs_fec.CCSDS_FCR == 112
        assert rs_fec.CCSDS_PRIM_EXP == 11
        assert rs_fec.CCSDS_GENERATOR == 173  # alpha^11

    def test_rs255_223_conventional_parity(self):
        block = rs_fec.encode_codeblock(
            RS_MESSAGE_223, k=223, interleave=1, basis=rs_fec.BASIS_CONVENTIONAL
        )
        assert block[:223] == RS_MESSAGE_223
        assert block[223:] == RS_PARITY_223_CONVENTIONAL

    def test_rs255_223_dual_basis_parity(self):
        block = rs_fec.encode_codeblock(
            RS_MESSAGE_223, k=223, interleave=1, basis=rs_fec.BASIS_DUAL
        )
        # libfec's encode_rs_ccsds leaves the data bytes untouched on the wire.
        assert block[:223] == RS_MESSAGE_223
        assert block[223:] == RS_PARITY_223_DUAL

    def test_rs255_239_conventional_parity(self):
        block = rs_fec.encode_codeblock(
            RS_MESSAGE_239, k=239, interleave=1, basis=rs_fec.BASIS_CONVENTIONAL
        )
        assert block[239:] == RS_PARITY_239_CONVENTIONAL

    def test_dual_basis_transform_table(self):
        """Berlekamp transform seed from CCSDS 131.0-B / Karn's gen_ccsds_tal."""
        assert rs_fec._TAL == (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)
        assert rs_fec.to_dual_basis(bytes([0x00])) == bytes([0x00])
        # The map is a bijection on all 256 byte values.
        allbytes = bytes(range(256))
        assert rs_fec.from_dual_basis(rs_fec.to_dual_basis(allbytes)) == allbytes
        assert len(set(rs_fec.to_dual_basis(allbytes))) == 256

    def test_bases_are_not_interchangeable(self):
        """If these ever matched, the transform would be a no-op."""
        conv = rs_fec.encode_codeblock(
            RS_MESSAGE_223, k=223, interleave=1, basis=rs_fec.BASIS_CONVENTIONAL
        )
        dual = rs_fec.encode_codeblock(
            RS_MESSAGE_223, k=223, interleave=1, basis=rs_fec.BASIS_DUAL
        )
        assert conv != dual


# --------------------------------------------------------------------------
# CCSDS pseudo-randomizer (131.0-B)
# --------------------------------------------------------------------------


class TestRandomizerVectors:
    def test_published_sequence_start(self):
        assert apply_prng(bytes(8)) == bytes.fromhex("ff480ec09a0d70bc")

    def test_sequence_period_is_255_bytes(self):
        seq = apply_prng(bytes(510))
        assert seq[:255] == seq[255:]


# --------------------------------------------------------------------------
# CRCs
# --------------------------------------------------------------------------


class TestCrcVectors:
    def test_crc16_ccitt_false_check_value(self):
        assert crc16_ccitt(b"123456789") == 0x29B1

    def test_crc8_j1850_vectors(self):
        assert crc8_j1850(b"") == 0x00
        assert crc8_j1850(b"123456789") == 0x4B
        assert crc8_j1850(bytes(31)) == 0x03


# --------------------------------------------------------------------------
# Fountain code: the PRNG and index selection define the wire format
# --------------------------------------------------------------------------


class TestFountainVectors:
    def test_xorshift32_sequence(self):
        rng = _Xorshift32(1)
        assert [rng.next_u32() for _ in range(5)] == [
            270369,
            67634689,
            2647435461,
            307599695,
            2398689233,
        ]

    def test_sample_indices_vector(self):
        assert _Xorshift32(12345).sample_indices(100, 5) == [30, 85, 40, 9, 87]

    def test_encoded_packet_vector(self):
        blocks = [bytes([i] * 16) for i in range(8)]
        packets = lt_encode_blocks(blocks, seed=42, num_packets=3)
        assert [(s, d, blk.hex()) for s, d, blk in packets] == [
            (11355432, 5, "03030303030303030303030303030303"),
            (2836018348, 1, "00000000000000000000000000000000"),
            (476557059, 5, "02020202020202020202020202020202"),
        ]


# --------------------------------------------------------------------------
# Container and text payload
# --------------------------------------------------------------------------


class TestContainerVectors:
    def test_atom_layout(self):
        atom = container.make_atom(
            atom_index=5,
            total_atoms=10,
            message_id=0x1234,
            atom_type=container.FOUNTAIN_PACKET,
            payload21=bytes(range(21)),
        )
        assert atom.hex() == (
            "a5e60205000a00341201"
            "000102030405060708090a0b0c0d0e0f10111213141f"
        )

    def test_text_payload_vector(self):
        encoded = encode_text("The satellite link is nominal.")
        assert encoded.hex() == "02000201002200240008004801012e"
        assert decode_text(encoded) == "The satellite link is nominal."


class TestIntegrityVectors:
    def test_integrity_crc_vector(self):
        header = bytes(range(21))
        assert codec.integrity_crc(header, b"payload") == 0x5385A965
