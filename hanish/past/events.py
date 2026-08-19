"""Records of what happened. The PAST.

An outcome is a record of how a stream of events resolved -- it is history,
not a claim about the future -- so Outcome and its enums live here with the
events they close. Nothing in this module interprets a name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..time import now


class Validity(StrEnum):
    """Exogenous does not mean correct. A runner can malfunction."""
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"


class Terminal(StrEnum):
    RESOLVED = "RESOLVED"
    INVALIDATED = "INVALIDATED"
    INTERVENED = "INTERVENED"
    UNRESOLVABLE = "UNRESOLVABLE"


class Verdict(StrEnum):
    """Only meaningful when terminal == RESOLVED."""
    HIT = "HIT"
    MISS = "MISS"


@dataclass
class ObservationEvent:
    source_ref: str
    event_id: str
    subject_ref: str
    observable: str
    value: Any
    source_seq: int | None = None
    epoch_ref: str | None = None
    emitted_at: str | None = None
    validity: Validity = Validity.VALID
    metadata: dict = field(default_factory=dict)
    arrived_at: str = field(default_factory=now)

    @property
    def dedup_key(self) -> tuple:
        """Uniqueness only ever holds within an emitter's own scope. That is
        the only guarantee a distributed capture layer can actually make."""
        return (self.source_ref, self.event_id)


@dataclass
class CompletenessSeal:
    """Asserted at a natural boundary by the host: 'this stream is finished,
    and it emitted exactly this many records.' Without a seal the substrate
    cannot distinguish 'did not happen' from 'channel died', and must fail
    closed."""
    source_ref: str
    epoch_ref: str
    final_source_seq: int
    complete: bool = True
    sealed_at: str = field(default_factory=now)


@dataclass
class Outcome:
    forecast_id: str
    terminal: Terminal
    verdict: Verdict | None = None
    observation_key: tuple | None = None
    predicted: float | None = None
    observed: Any = None
    brier: float | None = None
    reason: str = ""
    calibration_eligible: bool = False
    outcome_id: str = field(default_factory=lambda: f"o_{uuid.uuid4().hex[:12]}")
    resolved_at: str = field(default_factory=now)


# --------------------------------------------------------------------------
# Decoders. Only here and in claims are ledger records read back into types.
# --------------------------------------------------------------------------

def observation_from_dict(d: dict) -> ObservationEvent:
    d = dict(d)
    d["validity"] = Validity(d["validity"])
    return ObservationEvent(**d)


def seal_from_dict(d: dict) -> CompletenessSeal:
    return CompletenessSeal(**d)


def outcome_from_dict(d: dict) -> Outcome:
    d = dict(d)
    d["terminal"] = Terminal(d["terminal"])
    if d.get("verdict"):
        d["verdict"] = Verdict(d["verdict"])
    if d.get("observation_key"):
        d["observation_key"] = tuple(d["observation_key"])
    return Outcome(**d)
