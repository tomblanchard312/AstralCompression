import random
import math

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
    
    # Handle single block case
    if K == 1:
        return [(seed, 1, blocks[0])] * num_packets
    
    # Validate block sizes
    block_size = len(blocks[0])
    if not all(len(block) == block_size for block in blocks):
        raise ValueError("All blocks must have the same size")
    
    rnd = random.Random(seed)
    dist = _robust_soliton(K, c, delta)
    
    packets = []
    for _ in range(num_packets):
        # Generate deterministic packet seed
        packet_seed = rnd.getrandbits(32)
        packet_rng = random.Random(packet_seed)
        
        # Sample degree
        degree = _sample_degree(dist, packet_rng)
        degree = max(1, min(degree, K))
        
        # Use sampling without replacement for better distribution
        indices = packet_rng.sample(range(K), degree)
        
        # XOR selected blocks
        encoded_block = bytearray(block_size)
        for idx in indices:
            block = blocks[idx]
            for j in range(block_size):
                encoded_block[j] ^= block[j]
        
        packets.append((packet_seed, degree, bytes(encoded_block)))
    
    return packets

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
        packet_rng = random.Random(packet_seed)
        
        # Sample degree (must match encoder)
        degree = _sample_degree(dist, packet_rng)
        degree = max(1, min(degree, K))
        
        # Get same indices as encoder
        indices = packet_rng.sample(range(K), degree)
        
        equations.append((set(indices), bytearray(data)))
    
    # Gaussian elimination
    solved = {}
    
    while True:
        progress = False
        
        # Find degree-1 equations
        for i, (indices, data) in enumerate(equations):
            if len(indices) == 1:
                idx = next(iter(indices))
                
                if idx not in solved:
                    # Skip if data is all zeros
                    if all(b == 0 for b in data):
                        continue
                    
                    solved[idx] = bytes(data)
                    progress = True
                    
                    # Update all other equations
                    for j, (other_indices, other_data) in enumerate(equations):
                        if i != j and idx in other_indices:
                            other_indices.remove(idx)
                            # XOR out the solved block
                            for k in range(len(other_data)):
                                other_data[k] ^= data[k]
        
        if not progress:
            break
    
    # Prepare result
    decoded_blocks = [None] * K
    for idx, block in solved.items():
        if 0 <= idx < K:
            decoded_blocks[idx] = block
    
    recovery_fraction = len(solved) / K
    
    if recovery_fraction == 1.0:
        return decoded_blocks, 1.0
    else:
        return None, recovery_fraction