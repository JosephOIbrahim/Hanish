"""Core temporal types.

Everything here is domain-blind. Identities are opaque strings; the core
never interprets them. If a domain noun (commit, cook, node, render, frame,
shader, build, test) appears in this module, an invariant has been violated.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def now() -> str:
    """Canonical timestamp. UTC, ISO 8601, second resolution is not enough
    for ordering so we keep microseconds. Never used for cross-source
    ordering -- see Substrate.process for the ordering rule."""
    return datetime.now(UTC).isoformat()


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# --------------------------------------------------------------------------
# Enumerations
#
# Several of these carry exactly one legal value in V0.0. The field exists
# because it is expensive to retrofit; the value set stays at one until a
# version actually exercises the alternatives. A field with one legal value
# costs a byte and locks in nothing.
# --------------------------------------------------------------------------

class WorldRefCapability(StrEnum):
    """What hindsight protection the host can offer."""
    REPLAYABLE = "REPLAYABLE"      # state can be reconstructed
    IDENTIFIABLE = "IDENTIFIABLE"  # state can be named but not rebuilt
    NONE = "NONE"                  # no hindsight protection


class EmissionSemantics(StrEnum):
    """What the ABSENCE of a value means. This is the only thing the core
    needs to know about an observable, and it is what lets a missing
    observation avoid being scored as contrary evidence."""
    PER_SUBJECT = "PER_SUBJECT"  # each subject should eventually emit
    TERMINAL = "TERMINAL"        # one final value closes the subject
    PERIODIC = "PERIODIC"        # values continue on a cadence
    SPORADIC = "SPORADIC"        # absence carries no information, ever


class Validity(StrEnum):
    """Exogenous does not mean correct. A runner can malfunction."""
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"


class Exposure(StrEnum):
    """Was the forecast visible, before resolution, to any actor capable of
    moving its target observable?

    Intervention is only knowable if someone reports it. Exposure is knowable
    at authoring time and requires no reporting. EXPOSED outcomes are never
    calibration-eligible."""
    BLIND = "BLIND"
    EXPOSED = "EXPOSED"


class CausalMode(StrEnum):
    OBSERVATIONAL = "OBSERVATIONAL"  # V0.0: only legal value


class Adjudication(StrEnum):
    FIRST_VALID_TERMINAL = "FIRST_VALID_TERMINAL"  # V0.0: only legal value


class Comparator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"


class Terminal(StrEnum):
    RESOLVED = "RESOLVED"
    INVALIDATED = "INVALIDATED"
    INTERVENED = "INTERVENED"
    UNRESOLVABLE = "UNRESOLVABLE"


class Verdict(StrEnum):
    """Only meaningful when terminal == RESOLVED."""
    HIT = "HIT"
    MISS = "MISS"


_COMPARATORS = {
    Comparator.EQ:  lambda a, b: a == b,
    Comparator.NE:  lambda a, b: a != b,
    Comparator.GT:  lambda a, b: a > b,
    Comparator.GTE: lambda a, b: a >= b,
    Comparator.LT:  lambda a, b: a < b,
    Comparator.LTE: lambda a, b: a <= b,
}


def compare(observed: Any, comparator: Comparator, threshold: Any) -> bool:
    return _COMPARATORS[Comparator(comparator)](observed, threshold)


# --------------------------------------------------------------------------
# Observable declaration
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ObservableSpec:
    name: str
    value_type: str                       # "bool" | "int" | "float" | "str"
    emission: EmissionSemantics

    def absence_is_informative(self) -> bool:
        """Can 'nothing arrived' ever mean 'it did not happen'?

        Only if the channel promises a value. SPORADIC never promises one,
        so absence on a SPORADIC observable is always UNRESOLVABLE."""
        return self.emission in (
            EmissionSemantics.PER_SUBJECT,
            EmissionSemantics.TERMINAL,
        )


# --------------------------------------------------------------------------
# Resolution contract
#
# Declared before the evidence arrives, serialized with the forecast, never
# edited afterwards. The rule that judges may not be selected after seeing
# what it judges.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolutionSpec:
    observable: str
    comparator: Comparator
    threshold: Any
    horizon: str                                    # ISO timestamp
    adjudication: Adjudication = Adjudication.FIRST_VALID_TERMINAL
    accept_validity: tuple = (Validity.VALID,)
    causal_mode: CausalMode = CausalMode.OBSERVATIONAL

    def accepts(self, validity: Validity) -> bool:
        return Validity(validity) in tuple(Validity(v) for v in self.accept_validity)


# --------------------------------------------------------------------------
# Ledger records
# --------------------------------------------------------------------------

@dataclass
class Forecast:
    subject_ref: str
    claim: str
    probability: float
    resolution: ResolutionSpec
    exposure: Exposure
    world_ref: str | None = None
    world_ref_capability: WorldRefCapability = WorldRefCapability.NONE
    authored_by: str = "human"
    assumptions: tuple = ()
    forecast_id: str = field(default_factory=lambda: f"f_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=now)

    def __post_init__(self):
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be in [0, 1]")
        if self.world_ref_capability is not WorldRefCapability.NONE and not self.world_ref:
            raise ValueError("host declared world_ref capability but supplied none")

    @property
    def hindsight_unprotected(self) -> bool:
        return self.world_ref_capability is WorldRefCapability.NONE


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
# (de)serialization
# --------------------------------------------------------------------------

def to_json(obj: Any) -> str:
    def enc(o):
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, tuple):
            return list(o)
        raise TypeError(type(o))
    return json.dumps(asdict(obj), default=enc, sort_keys=True)


def forecast_from_dict(d: dict) -> Forecast:
    r = dict(d["resolution"])
    r["comparator"] = Comparator(r["comparator"])
    r["adjudication"] = Adjudication(r["adjudication"])
    r["causal_mode"] = CausalMode(r["causal_mode"])
    r["accept_validity"] = tuple(Validity(v) for v in r["accept_validity"])
    d = dict(d)
    d["resolution"] = ResolutionSpec(**r)
    d["exposure"] = Exposure(d["exposure"])
    d["world_ref_capability"] = WorldRefCapability(d["world_ref_capability"])
    d["assumptions"] = tuple(d.get("assumptions", ()))
    return Forecast(**d)


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
