import math


class _Xorshift32:
    """
    Portable xorshift32 PRNG (Marsaglia 2003).
    Identical output on every platform and language given the same seed.
    Shift triple: 13, 17, 5.

    Test vector: _Xorshift32(1).next_u32() == 270369
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int):
        # Seed must be a non-zero 32-bit unsigned integer.
        # Force into range and substitute 1 for the invalid seed of 0.
        seed = int(seed) & 0xFFFFFFFF
        self._state = seed if seed != 0 else 1

    def next_u32(self) -> int:
        """Return the next 32-bit unsigned integer."""
        x = self._state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self._state = x & 0xFFFFFFFF
        return self._state

    def random(self) -> float:
        """Return a float in [0.0, 1.0)."""
        return self.next_u32() / 0x100000000

    def getrandbits(self, k: int) -> int:
        """Return a non-negative integer with k random bits (k <= 32)."""
        if k <= 0:
            return 0
        if k > 32:
            raise ValueError("_Xorshift32.getrandbits supports k <= 32 only")
        return self.next_u32() >> (32 - k)

    def sample_indices(self, n: int, k: int) -> list:
        """
        Return a list of k distinct indices chosen from range(n),
        using a Fisher-Yates partial shuffle seeded from this RNG.
        Equivalent to random.sample(range(n), k) but portable.

        The shuffle is done over a sparse dict rather than a materialised
        ``list(range(n))``: only the positions actually touched are stored, so
        drawing a degree-3 packet from 20,000 blocks costs three operations
        instead of twenty thousand. The draw sequence, and therefore the wire
        format, is identical to the dense version.
        """
        if k < 0 or k > n:
            raise ValueError(f"sample size {k} out of range for population {n}")
        if k == 0:
            return []

        pool: dict = {}
        get = pool.get
        next_u32 = self.next_u32
        out = []
        append = out.append
        for i in range(k):
            j = i + (next_u32() % (n - i))
            vi = get(i, i)
            vj = get(j, j)
            pool[i] = vj
            pool[j] = vi
            append(vj)
        return out


def _ideal_soliton(K):
    if K <= 0:
        raise ValueError("K must be positive")

    p = [0.0] * (K + 1)
    p[1] = 1.0 / K
    for d in range(2, K + 1):
        p[d] = 1.0 / (d * (d - 1))
    return p


def _robust_soliton(K, c=0.1, delta=0.05):
    if K <= 0:
        raise ValueError("K must be positive")
    if c <= 0:
        raise ValueError("c must be positive")
    if delta <= 0 or delta >= 1:
        raise ValueError("delta must be in (0, 1)")

    # More stable R calculation
    R = c * math.log(K / delta) * math.sqrt(K)
    p = _ideal_soliton(K)
    t = [0.0] * (K + 1)

    # Calculate threshold more carefully
    K_over_R = max(1, int(K / max(R, 1)))

    for d in range(1, K + 1):
        if d < K_over_R:
            t[d] = R / (d * K)
        elif d == K_over_R:
            t[d] = (R * math.log(R / delta)) / K
        else:
            t[d] = 0.0

    # Ensure normalization is stable
    Z = sum(p[1:]) + sum(t[1:])
    if Z <= 0:
        raise ValueError("Invalid distribution normalization")

    return [(p[d] + t[d]) / Z for d in range(K + 1)]


def _sample_degree(dist, rnd):
    if not dist or len(dist) < 2:
        raise ValueError("dist must have at least 2 elements")

    r = rnd.random()
    cumulative = 0.0

    for d in range(1, len(dist)):
        cumulative += dist[d]
        if r <= cumulative:
            return d

    return len(dist) - 1


def lt_encode_blocks(blocks, seed, num_packets, c=0.1, delta=0.05):
    if not blocks:
        raise ValueError("blocks cannot be empty")
    if num_packets <= 0:
        raise ValueError("num_packets must be positive")

    K = len(blocks)

    # Handle single block case.
    if K == 1:
        block = blocks[0]
        block_size = len(blocks[0])
        if len(block) < block_size:
            block = block + bytes(block_size - len(block))
        return [(seed, 1, block)] * num_packets

    # Validate block sizes
    block_size = len(blocks[0])
    if not all(len(block) == block_size for block in blocks):
        raise ValueError("All blocks must have the same size")

    rnd = _Xorshift32(seed)
    dist = _robust_soliton(K, c, delta)

    # XOR whole blocks as big integers rather than byte by byte. Python's
    # bignum XOR runs in C over machine words, which is roughly an order of
    # magnitude faster than a per-byte loop for a 16-byte symbol and scales
    # better for larger ones.
    block_ints = [int.from_bytes(b, "big") for b in blocks]

    def draw():
        """One packet: its seed, degree, index set and index bitmask."""
        packet_seed = rnd.getrandbits(32)
        packet_rng = _Xorshift32(packet_seed)
        degree = _sample_degree(dist, packet_rng)
        degree = max(1, min(degree, K))
        indices = packet_rng.sample_indices(K, degree)
        mask = 0
        for idx in indices:
            mask |= 1 << idx
        return packet_seed, degree, indices, mask

    drawn = [draw() for _ in range(num_packets)]

    # Guarantee the emitted set is solvable.
    #
    # Left to chance, a randomly drawn set can be linearly dependent even with
    # every block covered and no packet lost: measured at K=3 with 13 packets,
    # 0.5% of seeds produced a set that cannot be decoded on a perfect link.
    # For a command message that is a real failure, not a rounding error.
    #
    # The decoder derives each packet's indices from that packet's own seed, so
    # the encoder is free to choose which seeds it emits. Redundant packets are
    # therefore replaced with ones that raise the rank, which costs nothing on
    # the wire and makes "all packets arrived" mean "decodes".
    if K <= RANK_GUARANTEE_MAX_K:
        drawn = _ensure_full_rank(drawn, K, draw)

    packets = []
    for packet_seed, degree, indices, _mask in drawn:
        acc = 0
        for idx in indices:
            acc ^= block_ints[idx]
        packets.append((packet_seed, degree, acc.to_bytes(block_size, "big")))

    return packets


MAX_RANK_REPAIR_DRAWS = 4096

# Above this many source blocks the guarantee is skipped. Measured
# rank-deficiency rates for an unrepaired draw at the default redundancy:
#
#     K=3   1.00%     K=20  0.25%     K=160  none observed
#     K=5   0.50%     K=40  0.25%     K=320  none observed
#     K=10  0.50%     K=80  none observed
#
# The risk lives at small K, where the check costs microseconds; the check
# costs O(M*K) and reaches 142 ms at K=2000, where the risk is unmeasurable.
# 512 leaves a wide margin over the last K at which any deficiency was seen.
RANK_GUARANTEE_MAX_K = 512


def _independent_and_dependent(drawn, K):
    """
    Split packets by whether each raises the rank of the set, over GF(2).

    Index sets are bitmasks, so elimination is a handful of integer XORs per
    packet rather than anything proportional to the payload.
    """
    pivots: dict = {}
    independent, dependent = [], []
    for item in drawn:
        residual = item[3]
        while residual:
            top = residual.bit_length() - 1
            if top not in pivots:
                pivots[top] = residual
                independent.append(item)
                break
            residual ^= pivots[top]
        else:
            dependent.append(item)
    return independent, dependent, pivots


def _ensure_full_rank(drawn, K, draw):
    """
    Replace redundant packets until the set spans all K source blocks.

    Returns the packets in their original order where possible. If the rank
    cannot be completed within a bounded number of draws the set is returned
    as it stands: a best effort beats refusing to transmit.
    """
    independent, dependent, pivots = _independent_and_dependent(drawn, K)
    if len(pivots) >= K or not dependent:
        return drawn

    replacements = {}
    attempts = 0
    spare = list(dependent)
    while len(pivots) < K and spare and attempts < MAX_RANK_REPAIR_DRAWS:
        attempts += 1
        candidate = draw()
        residual = candidate[3]
        while residual:
            top = residual.bit_length() - 1
            if top not in pivots:
                pivots[top] = residual
                victim = spare.pop()
                replacements[id(victim)] = candidate
                break
            residual ^= pivots[top]
        else:
            continue

    if not replacements:
        return drawn
    return [replacements.get(id(item), item) for item in drawn]


def _gaussian_eliminate(equations, solved: dict, symbol_size: int) -> None:
    """
    Solve the residual system left by peeling, in place, over GF(2).

    Each equation is ``[set_of_block_indices, value]`` where ``value`` is the
    symbol as a big integer. Equations are reduced against a pivot set keyed by
    their lowest remaining index; anything that reduces to the empty set is
    redundant and dropped. Back substitution then runs from the highest pivot
    down, which is safe because a pivot is by construction the smallest index
    in its own equation.

    Newly recovered blocks are added to ``solved``.
    """
    pivots: dict = {}

    for indices, value in equations:
        cur = set(indices)
        cur_value = value

        # Fold in everything peeling already recovered.
        for idx in cur & solved.keys():
            cur_value ^= solved[idx]
        cur -= solved.keys()

        while cur:
            p = min(cur)
            pivot = pivots.get(p)
            if pivot is None:
                pivots[p] = (cur, cur_value)
                break
            p_indices, p_value = pivot
            cur ^= p_indices
            cur_value ^= p_value
        # An empty `cur` means the packet was linearly dependent: no new
        # information, nothing to record.

    for p in sorted(pivots, reverse=True):
        p_indices, p_value = pivots[p]
        value = p_value
        resolvable = True
        for idx in p_indices:
            if idx == p:
                continue
            known = solved.get(idx)
            if known is None:
                resolvable = False
                break
            value ^= known
        if resolvable and p not in solved:
            solved[p] = value & ((1 << (symbol_size * 8)) - 1)


def lt_decode_blocks(packets, K, symbol_size, c=0.1, delta=0.05):
    if not packets:
        return None, 0.0
    if K <= 0:
        raise ValueError("K must be positive")
    if symbol_size <= 0:
        raise ValueError("symbol_size must be positive")

    # Reconstruct equations using same RNG sequence as encoder
    equations = []
    dist = _robust_soliton(K, c, delta)

    for packet_seed, reported_degree, data in packets:
        if len(data) != symbol_size:
            continue  # Skip malformed packets

        # Use same RNG sequence as encoder
        packet_rng = _Xorshift32(packet_seed)

        # Sample degree (must match encoder)
        degree = _sample_degree(dist, packet_rng)
        degree = max(1, min(degree, K))

        # Get same indices as encoder
        indices = packet_rng.sample_indices(K, degree)

        equations.append([set(indices), int.from_bytes(data, "big")])

    # Stage 1: belief-propagation peeling.
    #
    # Equations are indexed by the unknowns they still contain, so resolving a
    # block touches only the equations that actually mention it. The previous
    # implementation rescanned every equation for every solved block, which is
    # O(M*K) and dominated decode time for anything but tiny messages.
    containing: dict = {}
    for eq in equations:
        for idx in eq[0]:
            containing.setdefault(idx, []).append(eq)

    solved: dict = {}
    ready = [eq for eq in equations if len(eq[0]) == 1]

    while ready:
        indices, value = ready.pop()
        if len(indices) != 1:
            continue
        idx = next(iter(indices))
        if idx in solved:
            continue
        solved[idx] = value

        for eq in containing.get(idx, ()):
            if eq[0] is indices or idx not in eq[0]:
                continue
            eq[0].discard(idx)
            eq[1] ^= value
            if len(eq[0]) == 1:
                ready.append(eq)
        containing.pop(idx, None)

    # Stage 2: full Gaussian elimination over GF(2) on whatever peeling left
    # behind. Peeling alone stalls whenever the residual graph has no degree-1
    # equation, which wastes packets that are in fact linearly independent.
    if len(solved) < K:
        _gaussian_eliminate(equations, solved, symbol_size)

    # Prepare result
    decoded_blocks = [None] * K
    for idx, value in solved.items():
        if 0 <= idx < K:
            decoded_blocks[idx] = value.to_bytes(symbol_size, "big")

    recovery_fraction = len(solved) / K

    if recovery_fraction == 1.0:
        return decoded_blocks, 1.0
    else:
        return None, recovery_fraction
