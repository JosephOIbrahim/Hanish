"""Time vocabulary. The one thing every layer shares.

Canonical timestamps are UTC ISO 8601 with microseconds; second resolution is
not enough for ordering. parse() accepts exactly what now() emits.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> str:
    """Canonical timestamp. Never used for cross-source ordering -- see
    Substrate.process for the ordering rule."""
    return datetime.now(UTC).isoformat()


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)
