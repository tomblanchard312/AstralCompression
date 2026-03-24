# Simple dictionary-based text compressor (no external deps).
# - Tokenizes by words/punct
# - Encodes known words as varint IDs
# - Unknown tokens as length-prefixed UTF-8
# - Optional tiny Huffman for punctuation (future)
#
# This is a *starter*; you can swap in ANS/Huffman later while keeping the
# container the same.
import re
from .varint import leb128_encode, leb128_decode

BASE_LEXICON = [
    "the",
    "and",
    "to",
    "of",
    "a",
    "in",
    "that",
    "is",
    "for",
    "on",
    "with",
    "as",
    "are",
    "it",
    "this",
    "we",
    "you",
    "be",
    "or",
    "by",
    "from",
    "at",
    "not",
    "have",
    "can",
    "will",
    "your",
    "our",
    "all",
    "data",
    "message",
    "status",
    "command",
    "satellite",
    "system",
    "link",
    "time",
    "error",
    "ok",
    "yes",
    "no",
    "please",
    "ack",
    "nack",
    "update",
    "point",
    "battery",
    "power",
    "mode",
    "safe",
    "normal",
    "low",
    "high",
    "north",
    "south",
    "east",
    "west",
    "deploy",
    "arm",
    "disarm",
    "reboot",
    "reset",
    "start",
    "stop",
    "record",
    "science",
    "image",
    "voice",
    "text",
    "hello",
    "world",
    "nominal",
    "warning",
]
BASE_DICT = {w: i + 1 for i, w in enumerate(BASE_LEXICON)}

# Fixed tokenization regex
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)


def encode_text(s: str) -> bytes:
    if not isinstance(s, str):
        raise ValueError("input must be a string")

    tokens = TOKEN_RE.findall(s)
    out = bytearray()
    out.append(1)  # version
    out.append(0)  # flags

    for t in tokens:
        w = t.lower()
        idx = BASE_DICT.get(w, 0)
        if idx:
            out.append(0)  # tag: dict word
            out.extend(leb128_encode(idx))
        else:
            try:
                b = t.encode("utf-8")
                out.append(1)  # tag: raw bytes
                out.extend(leb128_encode(len(b)))
                out.extend(b)
            except UnicodeEncodeError:
                # Skip problematic tokens
                continue

    return bytes(out)


def decode_text(b: bytes) -> str:
    if not isinstance(b, bytes):
        raise ValueError("input must be bytes")
    if len(b) < 2:
        return ""

    pos = 0
    pos += 1  # skip version
    pos += 1  # skip flags

    toks = []
    while pos < len(b):
        if pos >= len(b):
            break

        tag = b[pos]
        pos += 1

        if tag == 0:  # dict word
            try:
                idx, pos = leb128_decode(b, pos)
                if 1 <= idx <= len(BASE_LEXICON):
                    w = BASE_LEXICON[idx - 1]
                    toks.append(w)
                else:
                    toks.append(f"W{idx}")
            except (ValueError, EOFError):
                break
        elif tag == 1:  # raw bytes
            try:
                ln, pos = leb128_decode(b, pos)
                if pos + ln <= len(b):
                    s = b[pos:pos + ln].decode("utf-8", errors="replace")
                    pos += ln
                    toks.append(s)
                else:
                    break
            except (ValueError, EOFError):
                break
        else:
            break

    # Improved token joining
    out = []
    for i, t in enumerate(toks):
        if i == 0:
            out.append(t)
        else:
            prev_alnum = toks[i - 1] and toks[i - 1][-1].isalnum()
            curr_alnum = t and t[0].isalnum()
            if prev_alnum and curr_alnum:
                out.append(" " + t)
            else:
                out.append(t)

    return "".join(out)
