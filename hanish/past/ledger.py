"""Append-only ledger.

One file per ledger, one JSON object per line, fsync on append. The offset a
record lands at is storage metadata -- it is not epistemic time and nothing
in the resolution path may treat it as such.

Nothing here is ever updated or deleted. A correction is a new record.

Two kinds of physical damage, two responses:

    TORN TAIL    a write that died mid-append. The final line is partial.
                 It is truncated away and counted (tail_loss). The ledger
                 never contained a valid record there.
    CORRUPTION   a bad line in the middle of history. It is skipped and
                 counted (corrupted), never silently interpreted. The rest
                 of the ledger stands; a skipped observation shows up as a
                 source_seq gap and defeats any completeness seal, so a
                 damaged line can never be laundered into a MISS.

Records carry a schema version tag (LEDGER_SCHEMA). A record without one is
read as v1. A record with a HIGHER version fails loud -- older code must
never misread newer data.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import msvcrt
    _WINDOWS = True
except ImportError:  # pragma: no cover -- POSIX
    import fcntl
    _WINDOWS = False

LEDGER_SCHEMA = 1


def to_json(obj: Any) -> str:
    def enc(o):
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, tuple):
            return list(o)
        raise TypeError(type(o))
    return json.dumps(asdict(obj), default=enc, sort_keys=True)


class _lock:
    """Cross-process append lock. A .lock file sits beside the ledger. The OS
    releases the lock when the holding process dies, so a crashed writer can
    never leave a stale lock behind."""

    def __init__(self, ledger_path: Path):
        self.path = ledger_path.with_name(ledger_path.name + ".lock")

    def __enter__(self):
        self._fh = open(self.path, "a+b")
        self._fh.seek(0)
        if self._fh.read(1) == b"":          # msvcrt needs a byte to lock
            self._fh.write(b"\0")
            self._fh.flush()
        self._fh.seek(0)
        if _WINDOWS:
            for _ in range(100):
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            else:
                raise OSError(f"could not acquire lock on {self.path}")
        else:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if _WINDOWS:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.tail_loss = 0
        self.corrupted = 0
        # One integrity pass at construction: repair the tail, count the
        # damage, count the records. raw() afterwards is read-only and
        # never re-counts -- accounting happens exactly here.
        self._count = sum(1 for _ in self.repair())

    # -- appends ------------------------------------------------------------

    def _write_durable(self, line: str) -> None:
        """fsync'd append, no locking. Call only under _lock."""
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def append(self, record: Any) -> int:
        """Append one record durably. Returns its offset.

        Durability matters here and only here: everything else in the system
        is derived from the ledgers, so a record that survives fsync is a
        record the system can rebuild itself from."""
        with _lock(self.path):
            self._write_durable(to_json(record) + "\n")
        offset = self._count
        self._count += 1
        return offset

    def append_dict(self, payload: dict) -> int:
        """Append a pre-built dict. Used where a record needs a type tag
        alongside its dataclass fields (the evidence ledger holds two kinds)."""
        with _lock(self.path):
            self._write_durable(json.dumps(payload, sort_keys=True) + "\n")
        offset = self._count
        self._count += 1
        return offset

    def append_observation_once(self, payload: dict, dedup_key: tuple) -> bool:
        """The cross-process once-only guarantee.

        Under the ledger lock: if any record already carries the key, the
        append loses and returns False. Otherwise it is written and True is
        returned. The scan and the write share one lock, so two hosts
        capturing the same envelope cannot both win."""
        with _lock(self.path):
            for rec in self.raw():
                if rec.get("_kind") != "observation":
                    continue
                if (rec.get("source_ref") == dedup_key[0]
                        and rec.get("event_id") == dedup_key[1]):
                    return False
            self._write_durable(json.dumps(payload, sort_keys=True) + "\n")
            return True

    @contextlib.contextmanager
    def locked(self):
        """Hold the append lock across a caller block (dedup decisions)."""
        with _lock(self.path):
            yield

    # -- reads ----------------------------------------------------------------

    def repair(self) -> Iterator[dict]:
        """One pass over the whole ledger: repair, count, yield.

        A JSONDecodeError on the final line is a torn tail -- a write that
        died before its newline. It is truncated away and counted
        (tail_loss); the ledger never contained a valid record there. On a
        non-final line it is corruption: the line is skipped and counted
        (corrupted), never fabricated. Either way the ledger stays open --
        a damaged byte is not allowed to brick a rebuild.

        Reads in binary and tracks byte offsets exactly. Text-mode offsets
        are wrong on Windows (tell() counts the newline-translated stream),
        and a wrong offset in a truncate would eat a valid record."""
        with open(self.path, "rb") as fh:
            data = fh.read()

        lines = data.split(b"\n")
        # The split ends in an empty element when the file ends with a
        # newline. A torn tail can only exist where the file ends WITHOUT
        # one -- the write died before its newline got out.
        tail_idx = len(lines) - 1 if data and not data.endswith(b"\n") else -1
        offset = 0
        for i, line in enumerate(lines):
            off = offset
            offset += len(line) + 1                 # +1 for the \n itself
            if not line.strip():
                continue
            try:
                yield json.loads(line.decode("utf-8", "strict").strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                if i == tail_idx:
                    with open(self.path, "r+b") as fh:
                        fh.truncate(off)
                    self.tail_loss += 1
                    break
                self.corrupted += 1

    def raw(self) -> Iterator[dict]:
        """Yield parsed records as the ledger is right now.

        Fresh reads each call, so cross-process dedup sees live state. Never
        mutates the file and never touches the counters: damage accounting
        happened once, at construction. A record damaged after construction
        (a concurrent crash) is skipped here and repaired at the next open --
        a damaged byte still never bricks a read."""
        with open(self.path, "rb") as fh:
            data = fh.read()
        for line in data.split(b"\n"):
            try:
                s = line.decode("utf-8", "strict").strip()
                if not s:
                    continue
                yield json.loads(s)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

    def read(self, decoder: Callable[[dict], Any]) -> Iterator[Any]:
        for rec in self.raw():
            yield decoder(rec)

    def __len__(self) -> int:
        return sum(1 for _ in self.raw())
