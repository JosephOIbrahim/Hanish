"""Records of what happened. The PAST.

An outcome is a record of how a stream of events resolved -- it is history,
not a claim about the future -- so Outcome and its enums live here with the
events they close. Nothing in this module interprets a name.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
    # Last to preserve the v1 positional constructor contract. V1 records and
    # compatibility callers omit it and migrate to subject == epoch; v2 hosts
    # pass the independently stable subject explicitly.
    subject_ref: str | None = None

    def __post_init__(self) -> None:
        subject = self.epoch_ref if self.subject_ref is None else self.subject_ref
        for value, name in (
            (self.source_ref, "source_ref"),
            (self.epoch_ref, "epoch_ref"),
            (subject, "subject_ref"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"seal {name} must be a non-empty string")
        object.__setattr__(self, "subject_ref", subject)


@dataclass(frozen=True)
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "terminal", Terminal(self.terminal))
        if self.verdict is not None:
            object.__setattr__(self, "verdict", Verdict(self.verdict))
        for value, name in (
            (self.forecast_id, "forecast_id"),
            (self.outcome_id, "outcome_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"outcome.{name} must be a non-empty string")
        if not isinstance(self.reason, str):
            raise TypeError("outcome.reason must be a string")
        if type(self.calibration_eligible) is not bool:
            raise TypeError("outcome.calibration_eligible must be bool")
        _aware_timestamp(self.resolved_at, "outcome.resolved_at")

        if self.observation_key is not None:
            if (
                not isinstance(self.observation_key, tuple)
                or len(self.observation_key) != 2
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in self.observation_key
                )
            ):
                raise ValueError(
                    "outcome.observation_key must be a pair of non-empty strings"
                )

        predicted = _finite_unit_number(self.predicted, "outcome.predicted")
        score = _finite_unit_number(self.brier, "outcome.brier")
        if score is not None and predicted is None:
            raise ValueError("outcome.brier requires outcome.predicted")
        if isinstance(self.observed, float) and not math.isfinite(self.observed):
            raise ValueError("outcome.observed must be finite")
        if self.observed is not None and type(self.observed) not in {
            bool,
            int,
            float,
            str,
        }:
            raise TypeError("outcome.observed must be a scalar JSON value or None")

        if self.terminal is Terminal.RESOLVED:
            if self.verdict is None:
                raise ValueError("a RESOLVED outcome requires a verdict")
        elif self.verdict is not None:
            raise ValueError("only a RESOLVED outcome may carry a verdict")

        if self.calibration_eligible:
            if self.terminal is not Terminal.RESOLVED:
                raise ValueError("only a RESOLVED outcome may be calibration eligible")
            if predicted is None or score is None:
                raise ValueError("calibration-eligible outcome requires prediction and Brier score")
        if self.terminal is Terminal.UNRESOLVABLE:
            if (
                self.observation_key is not None
                or self.observed is not None
                or score is not None
            ):
                raise ValueError("UNRESOLVABLE outcome cannot claim an observation or score")


# --------------------------------------------------------------------------
# Decoders. Only here and in claims are ledger records read back into types.
# --------------------------------------------------------------------------

def observation_from_dict(d: dict) -> ObservationEvent:
    d = dict(d)
    d["validity"] = Validity(d["validity"])
    return ObservationEvent(**d)


def seal_from_dict(d: dict, *, schema_version: int = 1) -> CompletenessSeal:
    payload = dict(d)
    if schema_version == 1:
        payload.setdefault("subject_ref", payload.get("epoch_ref"))
    elif "subject_ref" not in payload:
        raise ValueError("schema-v2 seal requires subject_ref")
    return CompletenessSeal(**payload)


def outcome_from_dict(d: dict) -> Outcome:
    d = dict(d)
    d["terminal"] = Terminal(d["terminal"])
    if d.get("verdict"):
        d["verdict"] = Verdict(d["verdict"])
    if d.get("observation_key"):
        d["observation_key"] = tuple(d["observation_key"])
    return Outcome(**d)


def _finite_unit_number(value: object, field_name: str) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{field_name} must be a finite number in [0, 1] or None")
    return value


def _aware_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed
