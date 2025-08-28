# DICT_UPDATE atom payloads and helpers.
# We keep it simple:
# Each DICT_UPDATE atom payload[0] = flags (bit0=more follows), payload[1..] = UTF-8 bytes with 0x00 separators.
# The receiver concatenates sequences until a flags byte with bit0=0, then splits on 0x00 to get words.
# Words are appended after BASE_LEXICON for dynamic token mapping during this message.
from typing import List

def split_words_from_atoms(atom_payloads: List[bytes]) -> list[str]:
    if not isinstance(atom_payloads, list):
        raise TypeError("atom_payloads must be a list")
    if not all(isinstance(p, bytes) for p in atom_payloads):
        raise TypeError("all atom_payloads must be bytes")
    
    buf = bytearray()
    for p in atom_payloads:
        if len(p) == 0:
            continue
        flags = p[0]
        # Remove trailing nulls properly
        payload_data = p[1:]
        # Find the last non-null byte
        while payload_data and payload_data[-1] == 0:
            payload_data = payload_data[:-1]
        buf.extend(payload_data)
        if (flags & 0x01) == 0:
            break
    
    # Split on 0x00 more carefully
    parts = []
    b = bytes(buf)
    if not b:
        return []
    
    # Split on null bytes
    segments = b.split(b'\x00')
    for segment in segments:
        if segment:  # Skip empty segments
            try:
                decoded = segment.decode('utf-8', errors='replace')
                word = decoded.strip().lower()
                if word:
                    parts.append(word)
            except:
                continue
    
    # Deduplicate while preserving order
    seen = set()
    out = []
    for w in parts:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out

def make_dict_update_atoms(words: list[str]) -> list[bytes]:
    if not isinstance(words, list):
        raise TypeError("words must be a list")
    if not all(isinstance(w, str) for w in words):
        raise TypeError("all words must be strings")
    
    # Filter and encode words
    valid_words = []
    for w in words:
        if w and isinstance(w, str):
            try:
                encoded = w.strip().encode('utf-8')
                if encoded:
                    valid_words.append(encoded)
            except:
                continue
    
    if not valid_words:
        return [bytes([0])]  # Empty update
    
    # Join with null separators
    data = b'\x00'.join(valid_words)
    max_chunk = 20
    atoms = []
    
    for i in range(0, len(data), max_chunk):
        chunk = data[i:i+max_chunk]
        more = 1 if (i + max_chunk) < len(data) else 0
        atoms.append(bytes([more]) + chunk)
    
    return atoms