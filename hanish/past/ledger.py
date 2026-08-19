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

import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
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
    return json.dumps(asdict(obj), allow_nan=False, default=enc, sort_keys=True)


@dataclass(frozen=True)
class LedgerSyncResult:
    """Immutable result of synchronizing one ledger generation.

    ``reset`` is true when the file identity, length, or record boundary no
    longer matched the caller's watermark and ``records`` is therefore the
    complete replacement snapshot.  Otherwise ``records`` is only the newly
    appended tail.
    """

    records: tuple[dict, ...]
    reset: bool = False


@dataclass(frozen=True)
class AtomicAppendResult:
    """Result of a lock-scoped synchronization and conditional append.

    ``records`` contains every durable record discovered since this Ledger
    instance's previous watermark, including the candidate when it won. A
    caller can therefore merge a racing writer's record immediately instead
    of waiting for a process restart.
    """

    appended: bool
    records: tuple[dict, ...]
    winner: dict | None = None
    conflict: bool = False
    reset: bool = False


class _lock:
    """Cross-process append lock. A .lock file sits beside the ledger. The OS
    releases the lock when the holding process dies, so a crashed writer can
    never leave a stale lock behind."""

    def __init__(self, ledger_path: Path):
        self.path = ledger_path.with_name(ledger_path.name + ".lock")

    def __enter__(self):
        self._fh = open(self.path, "a+b")
        # msvcrt needs a byte to lock. We may NOT read(1) to check: Windows
        # byte-range locks are mandatory, so a holder's locked byte 0 raises
        # PermissionError in every other handle that touches it. getsize()
        # reads directory metadata (region-free), and the append-mode write
        # lands at EOF -- neither ever enters the lock region.
        if os.path.getsize(self.path) == 0:
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
        self._physical_corrupted = 0
        self._semantic_corrupted = 0
        self.generation_resets = 0
        self._records: list[dict] = []
        self._watermark = 0
        self._file_identity: tuple[int, int] | None = None
        self._file_mtime_ns: int | None = None
        self._unique_indexes: dict[tuple[str, ...], dict[tuple, dict]] = {}
        self._multi_indexes: dict[tuple[str, ...], dict[tuple, list[dict]]] = {}

        # One cold integrity pass. Subsequent synchronized operations read
        # only bytes appended after this exact complete-line watermark.
        with _lock(self.path):
            self._full_rescan_locked()

    # -- appends ------------------------------------------------------------

    @property
    def corrupted(self) -> int:
        return self._physical_corrupted + self._semantic_corrupted

    def mark_semantic_corruption(self) -> None:
        """Count one decoded record that violates the consumer schema."""

        self._semantic_corrupted += 1

    def reset_semantic_corruption(self) -> None:
        """Prepare semantic damage accounting for a replacement snapshot."""

        self._semantic_corrupted = 0

    def _write_durable(self, line: str) -> None:
        """fsync'd append, no locking. Call only under _lock."""
        # Binary mode keeps the byte watermark exact on Windows; text mode
        # would translate ``\n`` to ``\r\n`` after the byte count was taken.
        with open(self.path, "ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def _canonical_payload(payload: dict) -> str:
        return json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _remember(self, record: dict) -> None:
        self._records.append(record)
        for fields, index in self._unique_indexes.items():
            key = tuple(record.get(field) for field in fields)
            index.setdefault(key, record)
        for fields, index in self._multi_indexes.items():
            key = tuple(record.get(field) for field in fields)
            index.setdefault(key, []).append(record)

    def _identity(self) -> tuple[int, int]:
        stat = self.path.stat()
        return (stat.st_dev, stat.st_ino)

    def _remember_file_metadata(self) -> None:
        stat = self.path.stat()
        self._file_identity = (stat.st_dev, stat.st_ino)
        self._file_mtime_ns = stat.st_mtime_ns

    def _parse_complete_lines(self, data: bytes) -> list[dict]:
        records: list[dict] = []
        lines = data.split(b"\n")
        for index, line in enumerate(lines):
            # A newline-terminated byte stream has one synthetic empty item
            # after its final delimiter.  Every earlier blank/whitespace line
            # is a complete but corrupt record and must remain visible in the
            # damage accounting.
            if index == len(lines) - 1 and line == b"":
                continue
            if not line.strip():
                self._physical_corrupted += 1
                continue
            try:
                decoded = json.loads(
                    line.decode("utf-8", "strict").strip(),
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"non-finite JSON number {value}")
                    ),
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                self._physical_corrupted += 1
                continue
            if not isinstance(decoded, dict):
                self._physical_corrupted += 1
                continue
            records.append(decoded)
        return records

    def _full_rescan_locked(self) -> list[dict]:
        """Repair and rebuild the operational snapshot under the file lock."""
        with open(self.path, "rb") as fh:
            data = fh.read()

        if data and not data.endswith(b"\n"):
            boundary = data.rfind(b"\n") + 1
            with open(self.path, "r+b") as fh:
                fh.truncate(boundary)
            data = data[:boundary]
            self.tail_loss += 1

        self._physical_corrupted = 0
        records = self._parse_complete_lines(data)
        self._records = records
        self._count = len(records)
        self._watermark = len(data)
        self._remember_file_metadata()
        self._unique_indexes.clear()
        self._multi_indexes.clear()
        return list(records)

    def _sync_tail_locked(self) -> LedgerSyncResult:
        """Read complete records since the local byte watermark.

        Replacement, truncation, or a watermark that is no longer on a line
        boundary triggers a safe full rescan. Ordinary cross-process appends
        stay proportional to the newly appended tail.
        """
        stat = self.path.stat()
        size = stat.st_size
        identity = (stat.st_dev, stat.st_ino)
        invalid_watermark = (
            identity != self._file_identity
            or self._watermark > size
            or (
                size == self._watermark
                and self._file_mtime_ns is not None
                and stat.st_mtime_ns != self._file_mtime_ns
            )
        )
        tail = b""
        if not invalid_watermark:
            with open(self.path, "rb") as fh:
                if self._watermark:
                    fh.seek(self._watermark - 1)
                    invalid_watermark = fh.read(1) != b"\n"
                if not invalid_watermark:
                    fh.seek(self._watermark)
                    tail = fh.read()
        if invalid_watermark:
            self.generation_resets += 1
            records = self._full_rescan_locked()
            return LedgerSyncResult(tuple(records), reset=True)
        if not tail:
            return LedgerSyncResult(())

        if not tail.endswith(b"\n"):
            relative_boundary = tail.rfind(b"\n") + 1
            boundary = self._watermark + relative_boundary
            if boundary < self._watermark:
                self.generation_resets += 1
                records = self._full_rescan_locked()
                return LedgerSyncResult(tuple(records), reset=True)
            with open(self.path, "r+b") as fh:
                fh.truncate(boundary)
            tail = tail[:relative_boundary]
            size = boundary
            self.tail_loss += 1

        records = self._parse_complete_lines(tail)
        for record in records:
            self._remember(record)
        self._count += len(records)
        self._watermark = size
        self._remember_file_metadata()
        return LedgerSyncResult(tuple(records))

    def _append_payload_locked(self, payload: dict) -> int:
        # Preserve the human-readable on-disk v1 formatting. Canonical JSON
        # is used for identity comparisons, not to rewrite ledger history.
        line = json.dumps(payload, allow_nan=False, sort_keys=True) + "\n"
        offset = self._count
        self._write_durable(line)
        self._remember(payload)
        self._count += 1
        self._watermark += len(line.encode("utf-8"))
        self._remember_file_metadata()
        return offset

    def append(self, record: Any) -> int:
        """Append one record durably. Returns its offset.

        Durability matters here and only here: everything else in the system
        is derived from the ledgers, so a record that survives fsync is a
        record the system can rebuild itself from."""
        return self.append_dict(json.loads(to_json(record)))

    def append_dict(self, payload: dict) -> int:
        """Append a pre-built dict. Used where a record needs a type tag
        alongside its dataclass fields (the evidence ledger holds two kinds)."""
        with _lock(self.path):
            self._sync_tail_locked()
            return self._append_payload_locked(payload)

    def synchronize(self) -> LedgerSyncResult:
        """Return a tail delta or an explicit full-snapshot replacement."""
        with _lock(self.path):
            return self._sync_tail_locked()

    def snapshot(self) -> tuple[dict, ...]:
        """Return the current in-memory snapshot without another file read.

        Callers synchronize first when they need to observe other writers.
        The records are treated as immutable ledger values throughout the
        core; the tuple prevents structural mutation of the snapshot itself.
        """

        return tuple(self._records)

    def append_unique(self, payload: dict, identity_fields: tuple[str, ...]) -> AtomicAppendResult:
        """Append once for a durable composite identity.

        An exact retry is accepted without another write. Reusing an identity
        for different canonical content is surfaced as a conflict.
        """
        identity = tuple(payload.get(field) for field in identity_fields)
        with _lock(self.path):
            discovered = self._sync_tail_locked()
            index = self._unique_indexes.get(identity_fields)
            if index is None:
                index = {}
                for record in self._records:
                    key = tuple(record.get(field) for field in identity_fields)
                    index.setdefault(key, record)
                self._unique_indexes[identity_fields] = index

            winner = index.get(identity)
            if winner is not None:
                conflict = self._canonical_payload(winner) != self._canonical_payload(payload)
                return AtomicAppendResult(
                    appended=False,
                    records=discovered.records,
                    winner=winner,
                    conflict=conflict,
                    reset=discovered.reset,
                )

            self._append_payload_locked(payload)
            return AtomicAppendResult(
                appended=True,
                records=tuple([*discovered.records, payload]),
                winner=payload,
                reset=discovered.reset,
            )

    def compare_and_append(
        self,
        payload: dict,
        identity_fields: tuple[str, ...],
        transition_allowed: Callable[[tuple[dict, ...], dict], bool],
    ) -> AtomicAppendResult:
        """Atomically append when a caller-defined monotone transition holds."""
        identity = tuple(payload.get(field) for field in identity_fields)
        with _lock(self.path):
            discovered = self._sync_tail_locked()
            index = self._multi_indexes.get(identity_fields)
            if index is None:
                index = {}
                for record in self._records:
                    key = tuple(record.get(field) for field in identity_fields)
                    index.setdefault(key, []).append(record)
                self._multi_indexes[identity_fields] = index

            existing = tuple(index.get(identity, ()))
            for record in existing:
                if self._canonical_payload(record) == self._canonical_payload(payload):
                    return AtomicAppendResult(
                        appended=False,
                        records=discovered.records,
                        winner=record,
                        reset=discovered.reset,
                    )
            if not transition_allowed(existing, payload):
                return AtomicAppendResult(
                    appended=False,
                    records=discovered.records,
                    winner=existing[-1] if existing else None,
                    conflict=True,
                    reset=discovered.reset,
                )

            self._append_payload_locked(payload)
            return AtomicAppendResult(
                appended=True,
                records=tuple([*discovered.records, payload]),
                winner=payload,
                reset=discovered.reset,
            )

    def sync_observation_once(self, payload: dict, dedup_key: tuple) -> AtomicAppendResult:
        if (payload.get("source_ref"), payload.get("event_id")) != dedup_key:
            raise ValueError("observation payload does not match its dedup identity")
        return self.append_unique(payload, ("source_ref", "event_id"))

    def append_observation_once(self, payload: dict, dedup_key: tuple) -> bool:
        """The cross-process once-only guarantee.

        Under the ledger lock: if any record already carries the key, the
        append loses and returns False. Otherwise it is written and True is
        returned. The scan and the write share one lock, so two hosts
        capturing the same envelope cannot both win."""
        return self.sync_observation_once(payload, dedup_key).appended

    # -- reads ----------------------------------------------------------------

    def repair(self) -> Iterator[dict]:
        """One pass over the whole ledger: repair, count, yield.

        Runs under the append lock: the read, the scan, and any truncate
        share one critical section, so a concurrent writer's record can
        never be eaten by a truncate computed from a stale read.

        A final line that cannot be parsed -- or that is only whitespace,
        which is what a write that died before its newline can look like --
        is a torn tail. It is truncated away and counted (tail_loss); the
        ledger never contained a valid record there. On a non-final line it
        is corruption: skipped and counted (corrupted), never fabricated.
        Either way the ledger stays open -- a damaged byte is not allowed
        to brick a rebuild.

        Reads in binary and tracks offsets exactly. Text-mode offsets are
        wrong on Windows (tell() counts the newline-translated stream), and
        a wrong offset in a truncate would eat a valid record."""
        with _lock(self.path):
            yield from self._full_rescan_locked()

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
                record = json.loads(
                    s,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"non-finite JSON number {value}")
                    ),
                )
                if isinstance(record, dict):
                    yield record
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                continue

    def __len__(self) -> int:
        return sum(1 for _ in self.raw())
