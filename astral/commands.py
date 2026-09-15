"""
Compact satellite command encoding with HMAC-SHA256 authentication.

Commanding is the one place in ASTRAL where getting it wrong is dangerous, so
the API is built to fail closed:

* ``decode_cmd`` verifies by default. Without a key, or with a key that does
  not verify, it raises :class:`CommandAuthError` rather than returning the
  command. Inspecting an unverified command is still possible, but you have to
  ask for it with ``require_auth=False``, and the result then carries
  ``authenticated: False``.
* Every result carries ``authenticated``, so a caller can never read a field
  that is absent and conclude the command was trustworthy.
* :class:`ReplayGuard` rejects a replayed or reordered counter. An
  authenticated command is not a fresh command; without a guard, an attacker
  who records a valid BURN can send it again. Use
  :class:`PersistentReplayGuard` for anything that commands real hardware:
  the in-memory guard forgets everything when the receiver restarts.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile

from .varint import leb128_encode, leb128_decode

# 4-byte counter + 32-byte HMAC-SHA256
MAC_TRAILER_SIZE = 36
COUNTER_MAX = 0xFFFFFFFF


class CommandAuthError(Exception):
    """Raised when a command cannot be shown to be authentic and fresh."""


class ReplayGuard:
    """
    Rejects commands whose counter is not strictly increasing.

    One guard per uplink key. Counters are 32 bits; the guard refuses to wrap,
    because a wrapped counter is indistinguishable from a replay. Rekey before
    4 billion commands.

    This base class keeps its state in memory only, so it protects a single
    run of a single process. A receiver that restarts starts over at -1 and
    will accept a command it has already executed. For anything commanding
    real hardware use :class:`PersistentReplayGuard`.
    """

    __slots__ = ("_last",)

    def __init__(self, last_accepted: int = -1) -> None:
        self._last = int(last_accepted)

    @property
    def last_accepted(self) -> int:
        return self._last

    def validate(self, counter: int) -> None:
        """Raise CommandAuthError unless ``counter`` is newer than the last."""
        self._check(counter)
        self._commit(counter)

    def _check(self, counter) -> None:
        if counter is None:
            raise CommandAuthError("command carries no counter to check")
        if counter <= self._last:
            raise CommandAuthError(
                f"replayed or out-of-order command: counter {counter} is not "
                f"newer than the last accepted counter {self._last}"
            )

    def _commit(self, counter: int) -> None:
        self._last = counter


class ReplayStateError(CommandAuthError):
    """Raised when the persisted replay state cannot be read or written."""


class PersistentReplayGuard(ReplayGuard):
    """
    A :class:`ReplayGuard` whose high-water mark survives a restart.

    The counter is written durably **before** the command is accepted, so a
    crash cannot leave a command executed but its counter unrecorded. The
    trade-off runs the safe way: a crash between the write and acting on the
    command burns that counter, and the sender has to reissue with a new one.
    Losing a command is recoverable; executing one twice may not be.

    ``path`` holds JSON mapping a link id to its last accepted counter, so one
    file can serve several uplinks::

        guard = PersistentReplayGuard("/var/lib/astral/uplink.json", "sat-1")

    A missing file means a first run and starts at -1. A file that exists but
    cannot be parsed is an error rather than a silent reset, because silently
    resetting is exactly the failure this class exists to prevent. Deleting
    the file disables replay protection; treat it as security state.

    Assumes a single writer. Two processes guarding the same link with the
    same file will race.
    """

    __slots__ = ("_path", "_link_id")

    FORMAT_VERSION = 1

    def __init__(
        self,
        path,
        link_id: str = "default",
        require_existing: bool = False,
    ) -> None:
        self._path = os.fspath(path)
        self._link_id = str(link_id)
        super().__init__(self._load(require_existing))

    @property
    def path(self) -> str:
        return self._path

    @property
    def link_id(self) -> str:
        return self._link_id

    def _read_state(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise ReplayStateError(
                f"replay state at {self._path!r} is unreadable ({exc}). "
                f"Refusing to accept commands: starting from scratch here "
                f"would re-open the replay window. Restore the file from "
                f"backup, or recreate it deliberately with the last counter "
                f"you know was accepted."
            ) from exc
        if not isinstance(state, dict) or "links" not in state:
            raise ReplayStateError(
                f"replay state at {self._path!r} is not an ASTRAL replay "
                f"state file"
            )
        return state

    def _load(self, require_existing: bool) -> int:
        state = self._read_state()
        if not state:
            if require_existing:
                raise ReplayStateError(
                    f"no replay state at {self._path!r} and require_existing "
                    f"is set"
                )
            return -1
        links = state.get("links", {})
        if not isinstance(links, dict):
            raise ReplayStateError(f"replay state at {self._path!r} is malformed")
        value = links.get(self._link_id, -1)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ReplayStateError(
                f"replay state for link {self._link_id!r} is not an integer"
            )
        return value

    def _commit(self, counter: int) -> None:
        state = self._read_state() or {"version": self.FORMAT_VERSION, "links": {}}
        state.setdefault("version", self.FORMAT_VERSION)
        state.setdefault("links", {})[self._link_id] = counter
        self._write_atomic(state)
        # Only now is the command accepted in memory.
        super()._commit(counter)

    def _write_atomic(self, state: dict) -> None:
        """Write the whole file via a temporary file and a rename."""
        directory = os.path.dirname(os.path.abspath(self._path)) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=directory, prefix=".astral-replay-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(state, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                # Never leave a stray temporary file behind on failure.
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._fsync_directory(directory)
        except OSError as exc:
            raise ReplayStateError(
                f"could not record the command counter at {self._path!r} "
                f"({exc}). Refusing the command: accepting it without a "
                f"durable record would allow it to be replayed after a "
                f"restart."
            ) from exc

    @staticmethod
    def _fsync_directory(directory: str) -> None:
        """Make the rename itself durable. Not supported on Windows."""
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


class CommandSequencer:
    """Sender-side counterpart: hands out strictly increasing counters."""

    __slots__ = ("_next",)

    def __init__(self, start: int = 0) -> None:
        self._next = int(start)

    def next(self) -> int:
        if self._next > COUNTER_MAX:
            raise ValueError("command counter space exhausted; rekey the link")
        value = self._next
        self._next += 1
        return value


def _verify_trailer(
    b: bytes,
    key: bytes,
    body_end: int,
    counter: int | None,
    replay_guard: ReplayGuard | None,
    require_auth: bool,
    out: dict,
) -> dict:
    """Shared HMAC/counter verification for commands and batches."""
    authenticated = False
    seen_counter = None

    if len(b) - body_end >= MAC_TRAILER_SIZE:
        counter_bytes = b[-MAC_TRAILER_SIZE:-32]
        mac_recv = b[-32:]
        body = b[: len(b) - MAC_TRAILER_SIZE]
        mac_expected = hmac.new(key, counter_bytes + body, hashlib.sha256).digest()
        authenticated = hmac.compare_digest(mac_expected, mac_recv)
        seen_counter = int.from_bytes(counter_bytes, "big")

    out["authenticated"] = authenticated
    out["auth_ok"] = authenticated  # retained name
    out["counter"] = seen_counter if authenticated else None
    if counter is not None:
        out["counter_ok"] = authenticated and seen_counter == counter

    if not authenticated:
        if require_auth:
            raise CommandAuthError(
                "command failed authentication: the HMAC is missing or does "
                "not match the supplied key"
            )
        return out

    if replay_guard is not None:
        # Raises on a replay, and only advances the guard on success.
        replay_guard.validate(seen_counter)
        out["fresh"] = True

    return out


def _require_key(key, require_auth: bool, what: str) -> None:
    if key is None and require_auth:
        raise CommandAuthError(
            f"refusing to return an unverified {what}: pass key= to "
            f"authenticate it, or require_auth=False to inspect it anyway"
        )


# Command IDs (extend as needed)
CMD_IDS = {
    "SET_MODE": 1,  # args: mode(0=SAFE,1=NORMAL,2=SCIENCE)
    "POINT": 2,  # args: az_deg *1e4 (signed), el_deg *1e4 (signed)
    "BURN": 3,  # args: thruster_id(varint), duration_ms(varint)
    "SCHED_WAKE": 4,  # args: tai_offset_s(varint)
    "REBOOT": 5,  # args: none
    "UPLOAD_CHUNK": 6,  # args: seq(varint), len(varint), bytes
    "APPLY_UPDATE": 7,  # args: none
}

MODE_IDS = {"SAFE": 0, "NORMAL": 1, "SCIENCE": 2}


def encode_cmd(
    cmd: dict,
    key: bytes | None = None,
    counter: int = 0,
) -> bytes:
    """cmd example:
    {"name":"POINT","az":-12.3456,"el":30.0}
    or {"name":"SET_MODE","mode":"SCIENCE"}
    If key provided, appends HMAC-SHA256 (32 bytes) trailer.
    """
    # Input validation
    if not isinstance(cmd, dict):
        raise ValueError("cmd must be a dictionary")
    if "name" not in cmd:
        raise ValueError("cmd must contain 'name' field")

    name = cmd.get("name")
    cid = CMD_IDS.get(name, 0)
    if cid == 0:
        raise ValueError(f"unknown command name: {name}")

    out = bytearray()
    out += bytes([(1 & 0x07) | (0 << 3)])  # version header
    out += leb128_encode(cid)

    if name == "SET_MODE":
        mode = MODE_IDS.get(cmd.get("mode", "SAFE"), 0)
        out.append(mode & 0xFF)
    elif name == "POINT":
        az = int(round(cmd.get("az", 0.0) * 10000))
        el = int(round(cmd.get("el", 0.0) * 10000))
        # pack as 3 bytes each signed 24-bit
        for v in (az, el):
            if v < 0:
                v = (1 << 24) + v
            out += bytes([(v) & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])
    elif name == "BURN":
        out += leb128_encode(int(cmd.get("thruster_id", 0)))
        out += leb128_encode(int(cmd.get("duration_ms", 0)))
    elif name == "SCHED_WAKE":
        out += leb128_encode(int(cmd.get("tai_offset_s", 0)))
    elif name == "REBOOT":
        pass
    elif name == "UPLOAD_CHUNK":
        payload = cmd.get("data", b"")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        out += leb128_encode(int(cmd.get("seq", 0)))
        out += leb128_encode(len(payload))
        out += payload
    elif name == "APPLY_UPDATE":
        pass

    if key:
        if not 0 <= counter <= COUNTER_MAX:
            raise ValueError(f"counter must be 0..{COUNTER_MAX}, got {counter}")
        counter_bytes = counter.to_bytes(4, "big")
        mac = hmac.new(key, counter_bytes + bytes(out), hashlib.sha256).digest()
        out += counter_bytes + mac
    return bytes(out)


def decode_cmd(
    b: bytes,
    key: bytes | None = None,
    counter: int | None = None,
    require_auth: bool = True,
    replay_guard: ReplayGuard | None = None,
) -> dict:
    """
    Decode a command, verifying it by default.

    Raises :class:`CommandAuthError` when no key is supplied, when the HMAC is
    missing or wrong, or when ``replay_guard`` recognises the counter as stale.
    Pass ``require_auth=False`` to inspect a command without verifying it; the
    result then carries ``authenticated: False``.
    """
    # Input validation
    if not isinstance(b, bytes):
        raise ValueError("b must be bytes")
    if key is not None and not isinstance(key, bytes):
        raise ValueError("key must be bytes or None")
    _require_key(key, require_auth, "command")

    pos = 0
    pos += 1  # skip version header byte
    cid, pos = leb128_decode(b, pos)

    inv_cmd = {v: k for k, v in CMD_IDS.items()}
    name = inv_cmd.get(cid, f"CMD_{cid}")
    out = {"name": name}

    if name == "SET_MODE":
        mode = b[pos]
        pos += 1
        inv_mode = {v: k for k, v in MODE_IDS.items()}
        out["mode"] = inv_mode.get(mode, f"M{mode}")  # type: ignore
    elif name == "POINT":

        def read24():
            nonlocal pos
            v = b[pos] | (b[pos + 1] << 8) | (b[pos + 2] << 16)
            pos += 3
            if v & (1 << 23):
                v -= 1 << 24
            return v

        az = read24() / 10000.0
        el = read24() / 10000.0
        out.update({"az": az, "el": el})  # type: ignore
    elif name == "BURN":
        thr, pos = leb128_decode(b, pos)
        dur, pos = leb128_decode(b, pos)
        out.update({"thruster_id": thr, "duration_ms": dur})  # type: ignore
    elif name == "SCHED_WAKE":
        off, pos = leb128_decode(b, pos)
        out["tai_offset_s"] = off
    elif name == "REBOOT":
        pass
    elif name == "UPLOAD_CHUNK":
        seq, pos = leb128_decode(b, pos)
        ln, pos = leb128_decode(b, pos)
        data = b[pos : pos + ln]
        pos += ln
        out.update({"seq": seq, "data": data})  # type: ignore
    elif name == "APPLY_UPDATE":
        pass

    if key:
        _verify_trailer(b, key, pos, counter, replay_guard, require_auth, out)
    else:
        # Never leave the caller to infer trust from a missing field.
        out["authenticated"] = False
        out["auth_ok"] = False
        out["signed"] = len(b) - pos >= MAC_TRAILER_SIZE

    return out


def encode_cmd_batch(
    batch: dict,
    key: bytes | None = None,
    counter: int = 0,
) -> bytes:
    # Input validation
    if not isinstance(batch, dict):
        raise ValueError("batch must be a dictionary")
    if "items" not in batch:
        raise ValueError("batch must contain 'items' field")
    if not isinstance(batch["items"], list):
        raise ValueError("batch items must be a list")
    if key is not None and not isinstance(key, bytes):
        raise ValueError("key must be bytes or None")

    """batch example:
    { "policy": {"rollback_on_fail": true},
      "items": [
        {"tai_offset_s": 5, "cmd": {"name":"SET_MODE","mode":"SCIENCE"}},
        {"tai_offset_s": 30, "cmd": {"name":"POINT","az":1.0,"el":5.0}}
      ]
    }"""
    policy = batch.get("policy", {})
    items = batch.get("items", [])
    flags = 0
    if policy.get("rollback_on_fail"):
        flags |= 1
    if policy.get("halt_on_error"):
        flags |= 2
    out = bytearray()
    out.append(1)  # version
    out.append(flags)
    out += leb128_encode(len(items))
    for it in items:
        off = int(it.get("tai_offset_s", 0))
        out += leb128_encode(off)
        cmd = it.get("cmd", {})
        # MAC whole batch at the end (optional)
        body = encode_cmd(cmd, key=None)  # type: ignore
        out += leb128_encode(len(body))
        out += body
    if key:
        if not 0 <= counter <= COUNTER_MAX:
            raise ValueError(f"counter must be 0..{COUNTER_MAX}, got {counter}")
        counter_bytes = counter.to_bytes(4, "big")
        mac = hmac.new(key, counter_bytes + bytes(out), hashlib.sha256).digest()
        out += counter_bytes + mac
    return bytes(out)


def decode_cmd_batch(
    b: bytes,
    key: bytes | None = None,
    counter: int | None = None,
    require_auth: bool = True,
    replay_guard: ReplayGuard | None = None,
) -> dict:
    """
    Decode a command batch, verifying it by default.

    The batch MAC covers every item, so an authenticated batch authenticates
    all of its commands. See :func:`decode_cmd` for the failure behaviour.
    """
    # Input validation
    if not isinstance(b, bytes):
        raise ValueError("b must be bytes")
    if key is not None and not isinstance(key, bytes):
        raise ValueError("key must be bytes or None")
    _require_key(key, require_auth, "command batch")

    pos = 0
    pos += 1  # skip version
    flags = b[pos]
    pos += 1
    n, pos = leb128_decode(b, pos)
    items = []
    for _ in range(n):
        off, pos = leb128_decode(b, pos)
        ln, pos = leb128_decode(b, pos)
        body = b[pos : pos + ln]
        pos += ln
        # Items are covered by the batch MAC, so they are not individually
        # signed; their trust comes from the batch verification below.
        items.append(
            {"tai_offset_s": off, "cmd": decode_cmd(body, require_auth=False)}
        )
    out = {
        "policy": {
            "rollback_on_fail": bool(flags & 1),
            "halt_on_error": bool(flags & 2),
        },
        "items": items,
    }
    if key:
        _verify_trailer(b, key, pos, counter, replay_guard, require_auth, out)
    else:
        out["authenticated"] = False
        out["auth_ok"] = False
        out["signed"] = len(b) - pos >= MAC_TRAILER_SIZE
    return out
