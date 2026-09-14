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

FORMAT_VERSION = 2

# Record tags
TAG_DICT_LOWER = 0
TAG_RAW = 1
TAG_DICT_TITLE = 2
TAG_DICT_UPPER = 3
TAG_GAP = 4

_CASE_TAGS = {
    TAG_DICT_LOWER: str.lower,
    TAG_DICT_TITLE: str.title,
    TAG_DICT_UPPER: str.upper,
}


def _implicit_gap(is_first: bool, token: str) -> str:
    """
    The separator the decoder inserts for free before ``token``.

    Ordinary prose is mostly "single space before a word, nothing before
    punctuation", so encoding that shape implicitly keeps the common case
    cheap; anything else is written out as an explicit gap record.
    """
    if is_first:
        return ""
    return " " if token[:1].isalnum() else ""


def _dict_tag(token: str) -> tuple[int, int] | None:
    """Return ``(tag, dict_index)`` if the token is a dictionary word."""
    idx = BASE_DICT.get(token.lower(), 0)
    if not idx:
        return None
    if token.islower():
        return TAG_DICT_LOWER, idx
    if token.istitle():
        return TAG_DICT_TITLE, idx
    if token.isupper() and len(token) > 1:
        return TAG_DICT_UPPER, idx
    # Mixed case such as "hELLo" cannot be reconstructed from a flag, so it
    # goes out as a literal rather than being silently case-folded.
    return None


def encode_text(s: str) -> bytes:
    """
    Encode text to the ASTRAL text payload format.

    Dictionary words become one-byte tags plus a varint index; everything else
    is a literal. Separators that differ from what the decoder reinserts
    implicitly are written explicitly, so ``decode_text(encode_text(s)) == s``
    for any string.
    """
    if not isinstance(s, str):
        raise ValueError("input must be a string")

    out = bytearray()
    out.append(FORMAT_VERSION)
    out.append(0)  # flags

    def emit_gap(gap: str) -> None:
        b = gap.encode("utf-8")
        out.append(TAG_GAP)
        out.extend(leb128_encode(len(b)))
        out.extend(b)

    pos = 0
    is_first = True
    for m in TOKEN_RE.finditer(s):
        token = m.group(0)
        gap = s[pos : m.start()]
        if gap != _implicit_gap(is_first, token):
            emit_gap(gap)

        dict_hit = _dict_tag(token)
        if dict_hit is not None:
            tag, idx = dict_hit
            out.append(tag)
            out.extend(leb128_encode(idx))
        else:
            b = token.encode("utf-8")
            out.append(TAG_RAW)
            out.extend(leb128_encode(len(b)))
            out.extend(b)

        pos = m.end()
        is_first = False

    trailing = s[pos:]
    if trailing:
        emit_gap(trailing)

    return bytes(out)


def _decode_v1(b: bytes) -> str:
    """Decode the legacy v1 payload, which was case- and whitespace-lossy."""
    pos = 2
    toks = []
    while pos < len(b):
        tag = b[pos]
        pos += 1
        if tag == TAG_DICT_LOWER:
            idx, pos = leb128_decode(b, pos)
            if not 1 <= idx <= len(BASE_LEXICON):
                raise ValueError(f"dictionary index {idx} out of range")
            toks.append(BASE_LEXICON[idx - 1])
        elif tag == TAG_RAW:
            ln, pos = leb128_decode(b, pos)
            if pos + ln > len(b):
                raise ValueError("literal runs past end of payload")
            toks.append(b[pos : pos + ln].decode("utf-8"))
            pos += ln
        else:
            raise ValueError(f"unknown v1 tag {tag}")

    out = []
    for i, t in enumerate(toks):
        out.append(t if i == 0 or not t[:1].isalnum() else " " + t)
    return "".join(out)


def decode_text(b: bytes) -> str:
    """
    Decode an ASTRAL text payload.

    Raises ValueError on a malformed payload rather than returning a partial
    or invented string: callers (``codec.unpack_stream``) treat that as "not
    recovered yet", which is the truthful answer for a damaged transmission.
    """
    if not isinstance(b, bytes):
        raise ValueError("input must be bytes")
    if len(b) < 2:
        return ""

    version = b[0]
    if version == 1:
        return _decode_v1(b)
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported text payload version {version}")

    pos = 2
    parts: list[str] = []
    pending_gap: str | None = None
    is_first = True

    while pos < len(b):
        tag = b[pos]
        pos += 1

        if tag == TAG_GAP:
            ln, pos = leb128_decode(b, pos)
            if pos + ln > len(b):
                raise ValueError("gap runs past end of payload")
            pending_gap = b[pos : pos + ln].decode("utf-8")
            pos += ln
            continue

        if tag in _CASE_TAGS:
            idx, pos = leb128_decode(b, pos)
            if not 1 <= idx <= len(BASE_LEXICON):
                raise ValueError(f"dictionary index {idx} out of range")
            token = _CASE_TAGS[tag](BASE_LEXICON[idx - 1])
        elif tag == TAG_RAW:
            ln, pos = leb128_decode(b, pos)
            if pos + ln > len(b):
                raise ValueError("literal runs past end of payload")
            token = b[pos : pos + ln].decode("utf-8")
            pos += ln
        else:
            raise ValueError(f"unknown text payload tag {tag}")

        if pending_gap is not None:
            parts.append(pending_gap)
            pending_gap = None
        else:
            parts.append(_implicit_gap(is_first, token))
        parts.append(token)
        is_first = False

    if pending_gap is not None:
        parts.append(pending_gap)

    return "".join(parts)
