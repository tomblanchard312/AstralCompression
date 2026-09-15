"""
Mission dictionaries: trained zstd dictionaries for short messages.

A general compressor has nothing to work with in a 90-byte status report: the
patterns that make mission traffic compressible live *across* messages, not
within one. A dictionary trained on past traffic gives the compressor that
context up front.

Measured on 100 short mission messages compressed individually:

    zstd -19, no dictionary      7,049 bytes
    McKay text transform         4,578 bytes
    zstd -19 + trained dict      3,350 bytes

which is why this exists. The win is largest exactly where ASTRAL is meant to
operate: small messages over an expensive link.

A dictionary is a shared artifact. Both ends need the same file, and the zstd
frame records which one it needs, so a receiver can tell a missing dictionary
from a corrupt payload. Ship it with the ground software, or uplink it once
and keep it.

Requires the optional ``zstandard`` dependency::

    pip install astral-compression[dict]
"""

from __future__ import annotations

import os
from typing import Iterable

ENV_VAR = "ASTRAL_DICT"

DEFAULT_DICT_SIZE = 16384
DEFAULT_LEVEL = 19
MIN_SAMPLES = 8

_MISSING = (
    "Mission dictionaries require the 'zstandard' package. "
    "Install it with: pip install astral-compression[dict]"
)


def _zstd():
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - exercised by the extras test
        raise ImportError(_MISSING, name="zstandard") from exc
    return zstandard


def available() -> bool:
    """True when dictionary compression can be used in this environment."""
    try:
        _zstd()
    except ImportError:
        return False
    return True


class MissionDictionary:
    """
    A trained zstd dictionary, identified by the id zstd stamps into frames.

    Treat it as mission configuration: version it, ship it to both ends, and
    do not retrain casually. A receiver without the dictionary a frame names
    cannot decompress that frame at all.
    """

    __slots__ = ("_dict", "_name")

    def __init__(self, dict_data, name: str = "") -> None:
        self._dict = dict_data
        self._name = name

    @property
    def dict_id(self) -> int:
        return self._dict.dict_id()

    @property
    def name(self) -> str:
        return self._name

    @property
    def raw(self):
        """The underlying ``zstandard`` dictionary object."""
        return self._dict

    def to_bytes(self) -> bytes:
        return self._dict.as_bytes()

    def save(self, path) -> None:
        with open(path, "wb") as handle:
            handle.write(self.to_bytes())

    @classmethod
    def load(cls, path) -> "MissionDictionary":
        zstandard = _zstd()
        with open(path, "rb") as handle:
            data = handle.read()
        if not data:
            raise ValueError(f"dictionary file {os.fspath(path)!r} is empty")
        return cls(
            zstandard.ZstdCompressionDict(data),
            name=os.path.basename(os.fspath(path)),
        )

    @classmethod
    def from_bytes(cls, data: bytes, name: str = "") -> "MissionDictionary":
        return cls(_zstd().ZstdCompressionDict(data), name=name)

    def compress(self, data: bytes, level: int = DEFAULT_LEVEL) -> bytes:
        compressor = _zstd().ZstdCompressor(level=level, dict_data=self._dict)
        return compressor.compress(data)

    def decompress(self, data: bytes) -> bytes:
        decompressor = _zstd().ZstdDecompressor(dict_data=self._dict)
        return decompressor.decompress(data)

    def __repr__(self) -> str:
        label = f" {self._name!r}" if self._name else ""
        return f"<MissionDictionary{label} id={self.dict_id} size={len(self.to_bytes())}>"


def train(
    samples: Iterable[bytes],
    size: int = DEFAULT_DICT_SIZE,
    name: str = "",
) -> MissionDictionary:
    """
    Train a dictionary on representative traffic.

    ``samples`` should be many individual messages of the kind you will send,
    not one concatenated blob: zstd learns what recurs *between* messages.
    More and more varied samples give a better dictionary; a few hundred real
    messages is plenty.
    """
    zstandard = _zstd()
    samples = [bytes(s) for s in samples if s]
    if len(samples) < MIN_SAMPLES:
        raise ValueError(
            f"need at least {MIN_SAMPLES} sample messages to train a useful "
            f"dictionary, got {len(samples)}"
        )
    if size < 256:
        raise ValueError("dictionary size must be at least 256 bytes")
    return MissionDictionary(zstandard.train_dictionary(size, samples), name=name)


def frame_dict_id(data: bytes) -> int:
    """
    Which dictionary a zstd frame needs, or 0 if it needs none.

    Returns 0 rather than raising for anything that is not a zstd frame, so
    callers can use it as a cheap probe.
    """
    try:
        return _zstd().get_frame_parameters(data).dict_id
    except Exception:
        return 0


def frame_content_size(data: bytes) -> int:
    """
    The decompressed size a zstd frame declares, or 0 if it declares none.

    ``ZstdCompressor.compress`` records it, which is what lets the compact
    container omit a length field entirely.
    """
    try:
        size = _zstd().get_frame_parameters(data).content_size
    except Exception:
        return 0
    # zstd reports an unknown size as a sentinel of all ones.
    return 0 if size is None or size == 0xFFFFFFFFFFFFFFFF else int(size)


def configured_paths() -> list:
    """
    Dictionary paths from the ``ASTRAL_DICT`` environment variable.

    Set it once for an operator's shell or service unit and every send and
    receive picks the dictionary up, instead of threading ``--dict`` through
    every invocation::

        export ASTRAL_DICT=/etc/astral/mission-v3.dict

    Multiple paths are separated by the platform's path separator. A receiver
    normally lists every dictionary still in use, since it must be able to
    decode traffic sent under older ones.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return []
    return [p for p in raw.split(os.pathsep) if p.strip()]


def configured_registry() -> "DictionaryRegistry":
    """Every dictionary named by ``ASTRAL_DICT``, loaded."""
    registry = DictionaryRegistry()
    for path in configured_paths():
        registry.load(path)
    return registry


def default_dictionary():
    """
    The dictionary to compress with by default: the first one configured.

    Returns None when nothing is configured, which is the supported state:
    ASTRAL works without a dictionary, just less well on short messages.
    """
    paths = configured_paths()
    if not paths:
        return None
    return MissionDictionary.load(paths[0])


class DictionaryRegistry:
    """
    The dictionaries a receiver holds, keyed by dict id.

    A decoder consults this to resolve the dictionary a frame names. Missing
    dictionaries produce a clear error naming the id, which is the difference
    between "you need to install mission dictionary 276400698" and "corrupt
    data".
    """

    def __init__(self, dictionaries: Iterable[MissionDictionary] = ()) -> None:
        self._by_id: dict = {}
        for dictionary in dictionaries:
            self.add(dictionary)

    def add(self, dictionary: MissionDictionary) -> None:
        self._by_id[dictionary.dict_id] = dictionary

    def get(self, dict_id: int):
        return self._by_id.get(dict_id)

    def require(self, dict_id: int) -> MissionDictionary:
        dictionary = self._by_id.get(dict_id)
        if dictionary is None:
            raise KeyError(
                f"this payload was compressed with mission dictionary "
                f"{dict_id}, which this receiver does not have. Load it with "
                f"DictionaryRegistry.load(path) or --dict on the command line."
            )
        return dictionary

    def load(self, path) -> MissionDictionary:
        """Load one dictionary file and register it."""
        dictionary = MissionDictionary.load(path)
        self.add(dictionary)
        return dictionary

    def load_dir(self, directory, pattern: str = "*.dict") -> int:
        """Register every dictionary in a directory. Returns how many."""
        import glob

        found = 0
        for path in sorted(glob.glob(os.path.join(os.fspath(directory), pattern))):
            self.load(path)
            found += 1
        return found

    def ids(self) -> list:
        return sorted(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, dict_id: int) -> bool:
        return dict_id in self._by_id
