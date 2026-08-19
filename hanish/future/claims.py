"""The FUTURE. Contracts about what will be observed.

Nothing in this layer opens a ledger or touches the present. It is pure
declaration: what a forecast means, what a resolution looks like, what the
comparison vocabulary is. The past may feed the future vocabulary (Validity
is a past concept); the present composes both.
"""

from __future__ import annotations

import hashlib
import json
import math
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


def canonical_world_commitment(payload: dict) -> str:
    """Return the one canonical JSON representation used by a world ref.

    The contents remain opaque to the core.  Adapters decide which artifacts
    describe their world; this layer only supplies a stable commitment format.
    """
    if not isinstance(payload, dict):
        raise TypeError("world commitment payload must be an object")
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("world commitment must contain canonical JSON values") from exc


def world_ref_for(commitment: str) -> str:
    """Content address for canonical commitment bytes."""
    if not isinstance(commitment, str):
        raise TypeError("world commitment must be canonical JSON text")
    return f"world:sha256:{hashlib.sha256(commitment.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class ExposureBasis:
    """Host attestation from which a conservative exposure is derived.

    ``None`` is different from an explicitly complete empty identity set.
    That distinction prevents an omitted list from accidentally proving
    blindness.  Identity values are opaque to the core.
    """

    author_ref: str
    seen_by: tuple[str, ...] | None = None
    capable_actors: tuple[str, ...] | None = None
    seen_by_complete: bool = False
    capable_actors_complete: bool = False
    separation_control_ref: str | None = None
    attested_by: str | None = None
    attested_at: str | None = None

    def __post_init__(self) -> None:
        _nonempty(self.author_ref, "exposure_basis.author_ref")
        for name in ("seen_by_complete", "capable_actors_complete"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"exposure_basis.{name} must be bool")
        for name in ("seen_by", "capable_actors"):
            refs = getattr(self, name)
            if refs is None:
                continue
            if not isinstance(refs, tuple):
                raise TypeError(f"exposure_basis.{name} must be a tuple or None")
            if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                raise ValueError(f"exposure_basis.{name} contains an empty identity")
            if len(set(refs)) != len(refs):
                raise ValueError(f"exposure_basis.{name} contains duplicate identities")
            object.__setattr__(self, name, tuple(sorted(refs)))
        for name in ("separation_control_ref", "attested_by"):
            value = getattr(self, name)
            if value is not None:
                _nonempty(value, f"exposure_basis.{name}")
        if self.attested_at is not None:
            _aware_timestamp(self.attested_at, "exposure_basis.attested_at")

    def derives_blind(self, *, authored_by: str, created_at: str) -> bool:
        """Whether this complete attestation proves separation."""
        if self.author_ref != authored_by:
            return False
        if self.seen_by is None or self.capable_actors is None:
            return False
        if not self.seen_by_complete or not self.capable_actors_complete:
            return False
        if not self.separation_control_ref or not self.attested_by or not self.attested_at:
            return False
        try:
            attested = _aware_timestamp(self.attested_at, "exposure_basis.attested_at")
            created = _aware_timestamp(created_at, "forecast.created_at")
        except (TypeError, ValueError):
            return False
        if attested > created:
            return False
        viewers = {self.author_ref, *self.seen_by}
        return viewers.isdisjoint(self.capable_actors)


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
    # Which source_refs can emit this observable. The completeness argument
    # needs the channel binding: a seal names a (source, epoch), and only a
    # seal from a source that actually emits this observable may certify its
    # absence. A host that declares no sources forfeits absence-as-MISS and
    # gets UNRESOLVABLE -- fail closed is the only honest default.
    sources: tuple[str, ...] = ()

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

@dataclass(frozen=True)
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
    exposure_basis: ExposureBasis | None = None
    world_commitment: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "exposure", Exposure(self.exposure))
        object.__setattr__(
            self,
            "world_ref_capability",
            WorldRefCapability(self.world_ref_capability),
        )
        if not isinstance(self.resolution, ResolutionSpec):
            raise TypeError("resolution must be a ResolutionSpec")
        if not isinstance(self.assumptions, tuple):
            raise TypeError("assumptions must be a tuple")
        if any(not isinstance(assumption, str) for assumption in self.assumptions):
            raise TypeError("assumptions must contain only immutable strings")
        if self.exposure_basis is not None and not isinstance(
            self.exposure_basis, ExposureBasis
        ):
            raise TypeError("exposure_basis must be an ExposureBasis or None")
        if self.world_commitment is not None and not isinstance(self.world_commitment, str):
            raise TypeError("world_commitment must be canonical JSON text or None")
        if (
            isinstance(self.probability, bool)
            or not isinstance(self.probability, (int, float))
            or not math.isfinite(self.probability)
            or not 0.0 <= self.probability <= 1.0
        ):
            raise ValueError("probability must be in [0, 1]")
        for value, name in (
            (self.subject_ref, "subject_ref"),
            (self.claim, "claim"),
            (self.authored_by, "authored_by"),
            (self.forecast_id, "forecast_id"),
        ):
            _nonempty(value, f"forecast.{name}")
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
        created = _aware_timestamp(self.created_at, "forecast.created_at")
        if created >= horizon:
            raise ValueError("forecast.created_at must precede resolution.horizon")

    @property
    def hindsight_unprotected(self) -> bool:
        return self.world_ref_capability is WorldRefCapability.NONE

    @property
    def structural_exposure(self) -> Exposure:
        """The most permissive classification justified by the record.

        An explicit EXPOSED label is monotone.  BLIND is accepted only when a
        complete basis proves it; missing proof never supplies eligibility.
        """
        if self.exposure is Exposure.EXPOSED:
            return Exposure.EXPOSED
        if self.exposure_basis is None:
            return Exposure.EXPOSED
        if self.exposure_basis.derives_blind(
            authored_by=self.authored_by,
            created_at=self.created_at,
        ):
            return Exposure.BLIND
        return Exposure.EXPOSED


@dataclass(frozen=True)
class ExposureAmendment:
    """Append-only removal of calibration eligibility."""

    forecast_id: str
    target_forecast_digest: str
    reason_code: str
    amended_by: str
    amended_at: str = field(default_factory=now)
    from_exposure: Exposure = Exposure.BLIND
    to_exposure: Exposure = Exposure.EXPOSED

    def __post_init__(self) -> None:
        for name in ("forecast_id", "reason_code", "amended_by"):
            _nonempty(getattr(self, name), f"exposure_amendment.{name}")
        if not _has_digest_shape(self.target_forecast_digest, "sha256:"):
            raise ValueError(
                "exposure_amendment.target_forecast_digest must be sha256:<64 hex>"
            )
        object.__setattr__(self, "from_exposure", Exposure(self.from_exposure))
        object.__setattr__(self, "to_exposure", Exposure(self.to_exposure))
        if (
            self.from_exposure is not Exposure.BLIND
            or self.to_exposure is not Exposure.EXPOSED
        ):
            raise ValueError("the only exposure amendment is BLIND -> EXPOSED")
        _aware_timestamp(self.amended_at, "exposure_amendment.amended_at")


def forecast_from_dict(d: dict, *, schema_version: int = 1) -> Forecast:
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
    if d.get("exposure_basis") is not None:
        basis = dict(d["exposure_basis"])
        if basis.get("seen_by") is not None:
            basis["seen_by"] = tuple(basis["seen_by"])
        if basis.get("capable_actors") is not None:
            basis["capable_actors"] = tuple(basis["capable_actors"])
        d["exposure_basis"] = ExposureBasis(**basis)
    if schema_version == 1 and d["world_ref_capability"] is WorldRefCapability.REPLAYABLE:
        # V1 carried no reconstructible commitment.  Its short digest names a
        # state, but cannot rebuild it; loading it as REPLAYABLE would renew a
        # provenance claim that the record never proved.
        d["world_ref_capability"] = WorldRefCapability.IDENTIFIABLE
        d["world_commitment"] = None
    forecast = Forecast(**d)
    validate_world_contract(forecast, schema_version=schema_version)
    return forecast


def exposure_amendment_from_dict(d: dict) -> ExposureAmendment:
    return ExposureAmendment(**d)


def validate_world_contract(forecast: Forecast, *, schema_version: int = 2) -> None:
    """Validate only generic commitment presence, capability, and digest.

    The adapter remains responsible for deciding whether its domain-specific
    artifact set is actually sufficient to claim REPLAYABLE.
    """
    capability = forecast.world_ref_capability
    commitment = forecast.world_commitment
    if schema_version == 1:
        return
    if capability is WorldRefCapability.REPLAYABLE and commitment is None:
        raise ValueError("REPLAYABLE world_ref requires a world commitment")
    if commitment is None:
        return
    try:
        payload = json.loads(commitment)
    except json.JSONDecodeError as exc:
        raise ValueError("world commitment must be valid canonical JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("world commitment must be a JSON object")
    if canonical_world_commitment(payload) != commitment:
        raise ValueError("world commitment must use canonical JSON")
    commitment_version = payload.get("_v")
    if (
        payload.get("_kind") != "world_commitment"
        or type(commitment_version) is not int
        or commitment_version != 1
    ):
        raise ValueError("world commitment kind/version is invalid")
    if payload.get("capability") != capability.value:
        raise ValueError("world commitment capability does not match forecast")
    if not _has_digest_shape(forecast.world_ref, "world:sha256:"):
        raise ValueError("committed world_ref must be world:sha256:<64 hex>")
    if world_ref_for(commitment) != forecast.world_ref:
        raise ValueError("world_ref does not match world commitment")


def _nonempty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _has_digest_shape(value: object, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value[len(prefix):]
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


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
