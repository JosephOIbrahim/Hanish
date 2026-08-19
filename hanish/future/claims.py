"""The FUTURE. Contracts about what will be observed.

Nothing in this layer opens a ledger or touches the present. It is pure
declaration: what a forecast means, what a resolution looks like, what the
comparison vocabulary is. The past may feed the future vocabulary (Validity
is a past concept); the present composes both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..past.events import Validity
from ..time import now

# --------------------------------------------------------------------------
# Enumerations
#
# Several of these carry exactly one legal value in V0.0. The field exists
# because it is expensive to retrofit; the value set stays at one until a
# version actually exercises the alternatives.
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


class Exposure(StrEnum):
    """Was the forecast visible, before resolution, to any actor capable of
    moving its target observable? EXPOSED outcomes are never
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
# The forecast
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
        # A horizon must be parseable AND timezone-aware, or resolution would
        # later compare it against aware arrival timestamps and crash. The
        # gate is at authoring -- a malformed claim never reaches process().
        try:
            horizon = datetime.fromisoformat(self.resolution.horizon)
        except ValueError:
            raise ValueError("resolution.horizon must be an ISO 8601 timestamp") from None
        if horizon.tzinfo is None or horizon.utcoffset() is None:
            raise ValueError("resolution.horizon must be timezone-aware")

    @property
    def hindsight_unprotected(self) -> bool:
        return self.world_ref_capability is WorldRefCapability.NONE


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
