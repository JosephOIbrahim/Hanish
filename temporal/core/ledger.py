"""Append-only ledger.

One file per ledger, one JSON object per line, fsync on append. The offset a
record lands at is storage metadata -- it is not epistemic time and nothing
in the resolution path may treat it as such.

Nothing here is ever updated or deleted. A correction is a new record.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .types import to_json


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._count = sum(1 for _ in self.raw())

    def append(self, record: Any) -> int:
        """Append one record durably. Returns its offset.

        Durability matters here and only here: everything else in the system
        is derived from the ledgers, so a record that survives fsync is a
        record the system can rebuild itself from.
        """
        line = to_json(record) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        offset = self._count
        self._count += 1
        return offset

    def append_dict(self, payload: dict) -> int:
        """Append a pre-built dict. Used where a record needs a type tag
        alongside its dataclass fields (the evidence ledger holds two kinds)."""
        line = json.dumps(payload, sort_keys=True) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        offset = self._count
        self._count += 1
        return offset

    def read(self, decoder: Callable[[dict], Any]) -> Iterator[Any]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield decoder(json.loads(line))

    def raw(self) -> Iterator[dict]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def __len__(self) -> int:
        return sum(1 for _ in self.raw())
