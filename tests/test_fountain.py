"""Tests for astral/fountain.py - xorshift32 PRNG and LT fountain code."""
import pytest
from astral.fountain import (
    _Xorshift32,
    _robust_soliton,
    lt_decode_blocks,
    lt_encode_blocks,
)


class TestXorshift32:
    def test_test_vector(self):
        """Canonical test vector: seed=1 must produce 270369 on first call."""
        assert _Xorshift32(1).next_u32() == 270369

    def test_deterministic(self):
        """Same seed always produces same sequence."""
        r1 = _Xorshift32(0xDEADBEEF)
        r2 = _Xorshift32(0xDEADBEEF)
        seq1 = [r1.next_u32() for _ in range(20)]
        seq2 = [r2.next_u32() for _ in range(20)]
        assert seq1 == seq2

    def test_zero_seed_becomes_one(self):
        """Seed 0 is invalid for xorshift32; must be substituted with 1."""
        r = _Xorshift32(0)
        assert r.next_u32() == _Xorshift32(1).next_u32()

    def test_random_in_unit_interval(self):
        r = _Xorshift32(99)
        for _ in range(100):
            v = r.random()
            assert 0.0 <= v < 1.0

    def test_getrandbits_32(self):
        r = _Xorshift32(42)
        for _ in range(50):
            v = r.getrandbits(32)
            assert 0 <= v < 2**32

    def test_sample_indices_length(self):
        r = _Xorshift32(7)
        indices = r.sample_indices(10, 4)
        assert len(indices) == 4

    def test_sample_indices_unique(self):
        r = _Xorshift32(7)
        indices = r.sample_indices(10, 4)
        assert len(set(indices)) == 4

    def test_sample_indices_in_range(self):
        r = _Xorshift32(7)
        for _ in range(20):
            indices = r.sample_indices(8, 3)
            assert all(0 <= i < 8 for i in indices)

    def test_sample_indices_k_zero(self):
        r = _Xorshift32(1)
        assert r.sample_indices(5, 0) == []

    def test_sample_indices_k_equals_n(self):
        r = _Xorshift32(1)
        indices = r.sample_indices(5, 5)
        assert sorted(indices) == [0, 1, 2, 3, 4]


class TestLtFountain:
    SYMBOL_SIZE = 16

    def _make_blocks(self, n: int) -> list[bytes]:
        """Create n distinct 16-byte blocks."""
        return [bytes([i] * self.SYMBOL_SIZE) for i in range(n)]

    def test_k1_roundtrip(self):
        """K=1: single block encodes and decodes correctly."""
        block = bytes(range(self.SYMBOL_SIZE))
        packets = lt_encode_blocks([block], seed=42, num_packets=5)
        assert all(len(p[2]) == self.SYMBOL_SIZE for p in packets), (
            "All K=1 packets must have exactly symbol_size bytes"
        )
        recovered, frac = lt_decode_blocks(packets, K=1, symbol_size=self.SYMBOL_SIZE)
        assert recovered is not None
        assert frac == 1.0
        assert recovered[0] == block

    def test_k1_all_zero_block(self):
        """K=1 with an all-zero block must not be silently dropped."""
        block = bytes(self.SYMBOL_SIZE)
        packets = lt_encode_blocks([block], seed=7, num_packets=5)
        recovered, frac = lt_decode_blocks(packets, K=1, symbol_size=self.SYMBOL_SIZE)
        assert recovered is not None, "All-zero block must not be skipped"
        assert frac == 1.0

    def test_k_many_clean_roundtrip(self):
        """Multi-block: full packet set always decodes completely."""
        blocks = self._make_blocks(8)
        packets = lt_encode_blocks(blocks, seed=0xCAFE, num_packets=24)
        recovered, frac = lt_decode_blocks(packets, K=8, symbol_size=self.SYMBOL_SIZE)
        assert recovered is not None
        assert frac == 1.0
        for i, b in enumerate(blocks):
            assert recovered[i] == b

    def test_deterministic_encoding(self):
        """Same seed always produces identical packet sequence."""
        blocks = self._make_blocks(4)
        p1 = lt_encode_blocks(blocks, seed=123, num_packets=10)
        p2 = lt_encode_blocks(blocks, seed=123, num_packets=10)
        assert p1 == p2

    def test_partial_recovery(self):
        """Partial packet set returns non-zero fraction."""
        blocks = self._make_blocks(16)
        all_packets = lt_encode_blocks(blocks, seed=1, num_packets=40)
        _, frac = lt_decode_blocks(all_packets[:2], K=16, symbol_size=self.SYMBOL_SIZE)
        assert 0.0 <= frac <= 1.0

    def test_empty_blocks_raises(self):
        with pytest.raises(ValueError):
            lt_encode_blocks([], seed=1, num_packets=5)

    def test_empty_packets_returns_none(self):
        recovered, frac = lt_decode_blocks([], K=4, symbol_size=self.SYMBOL_SIZE)
        assert recovered is None
        assert frac == 0.0

    def test_robust_soliton_normalised(self):
        """Robust soliton distribution must sum to approximately 1.0."""
        for k in [1, 4, 16, 64]:
            dist = _robust_soliton(k)
            total = sum(dist[1:])
            assert abs(total - 1.0) < 1e-9, f"K={k}: sum={total}"
