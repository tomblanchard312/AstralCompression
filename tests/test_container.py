"""Tests for astral/container.py - 32-byte atom framing."""
import pytest
from astral.container import (
    ATOM_SIZE,
    FOUNTAIN_PACKET,
    HEADER_GIST,
    SYNC0,
    SYNC1,
    make_atom,
    parse_atoms,
)


class TestMakeAtom:
    def test_output_size(self):
        atom = make_atom(0, 1, 1, HEADER_GIST, bytes(21))
        assert len(atom) == ATOM_SIZE

    def test_sync_bytes(self):
        atom = make_atom(0, 1, 1, HEADER_GIST, bytes(21))
        assert atom[0] == SYNC0
        assert atom[1] == SYNC1

    def test_roundtrip_fields(self):
        payload = bytes(range(21))
        atom = make_atom(
            atom_index=5,
            total_atoms=10,
            message_id=0x1234,
            atom_type=FOUNTAIN_PACKET,
            payload21=payload,
        )
        parsed = parse_atoms(atom)
        assert len(parsed) == 1
        idx, total, mid, typ, pl = parsed[0]
        assert idx == 5
        assert total == 10
        assert mid == 0x1234
        assert typ == FOUNTAIN_PACKET
        assert pl == payload

    def test_short_payload_padded(self):
        atom = make_atom(0, 1, 1, HEADER_GIST, b"short")
        assert len(atom) == ATOM_SIZE

    def test_payload_too_long_raises(self):
        with pytest.raises(ValueError):
            make_atom(0, 1, 1, HEADER_GIST, bytes(22))

    def test_atom_index_out_of_range_raises(self):
        with pytest.raises(ValueError):
            make_atom(-1, 1, 1, HEADER_GIST, bytes(21))
        with pytest.raises(ValueError):
            make_atom(65536, 1, 1, HEADER_GIST, bytes(21))


class TestParseAtoms:
    def test_crc_corruption_skipped(self):
        atom = bytearray(make_atom(0, 1, 1, HEADER_GIST, bytes(21)))
        atom[10] ^= 0xFF
        assert parse_atoms(bytes(atom)) == []

    def test_bad_sync_skipped(self):
        atom = bytearray(make_atom(0, 1, 1, HEADER_GIST, bytes(21)))
        atom[0] = 0x00
        assert parse_atoms(bytes(atom)) == []

    def test_multiple_atoms(self):
        atoms = b"".join(
            make_atom(i, 5, 0xABCD, FOUNTAIN_PACKET, bytes([i] * 21))
            for i in range(5)
        )
        parsed = parse_atoms(atoms)
        assert len(parsed) == 5
        for i, (idx, total, mid, typ, _) in enumerate(parsed):
            assert idx == i
            assert total == 5
            assert mid == 0xABCD

    def test_empty_stream(self):
        assert parse_atoms(b"") == []

    def test_non_bytes_raises(self):
        with pytest.raises((ValueError, TypeError)):
            parse_atoms("not bytes")  # type: ignore[arg-type]
