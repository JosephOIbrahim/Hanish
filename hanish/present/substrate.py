"""The PRESENT. The substrate.

It knows there are named observables, claims about them, an append-only
evidence history, and outcomes. It knows nothing else -- not what any name
means, not what units anything is in, not what actions exist, not whether
anything is good.

Two failure directions, and they point opposite ways:

    OUTWARD   the substrate may never raise into its host.
              Losing an observation is acceptable. Breaking a build is not.
    INWARD    incomplete evidence may never become knowledge.
              A missing observation is UNRESOLVABLE, never MISS.

No scheduler on the epistemic path: resolution happens when someone asks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from ..future.claims import (
    Adjudication,
    Exposure,
    ExposureAmendment,
    Forecast,
    ObservableSpec,
    exposure_amendment_from_dict,
    forecast_from_dict,
    validate_world_contract,
)
from ..future.scoring import brier, compare
from ..past.events import (
    CompletenessSeal,
    ObservationEvent,
    Outcome,
    Terminal,
    Verdict,
    observation_from_dict,
    outcome_from_dict,
    seal_from_dict,
)
from ..past.ledger import LEDGER_SCHEMA, Ledger, to_json
from ..time import now, parse

_SCHEMA_V2 = 2
_FORECAST_KIND = "forecast"
_AMENDMENT_KIND = "exposure_amendment"
_OUTCOME_KIND = "outcome"


class Substrate:
    """File-backed, derived state. Everything is derived from the three
    ledgers, so restart is free: reopen and rebuild."""

    def __init__(self, root: str | Path, observables: dict[str, ObservableSpec] | None = None):
        self.root = Path(root)
        self.forecasts_l = Ledger(self.root / "forecasts.jsonl")
        self.evidence_l = Ledger(self.root / "evidence.jsonl")
        self.outcomes_l = Ledger(self.root / "outcomes.jsonl")

        self.observables: dict[str, ObservableSpec] = dict(observables or {})

        # Derived state. All of it rebuilt from the ledgers on construction.
        self.forecasts: dict[str, Forecast] = {}
        self.index: dict[tuple, list[str]] = {}      # (subject, observable) -> [forecast_id]
        self.outcomes: dict[str, Outcome] = {}
        self.amendments: list[ExposureAmendment] = []
        self._forecast_versions: dict[str, int] = {}
        self._forecast_digests: dict[str, str] = {}
        self._base_exposure: dict[str, Exposure] = {}
        self._effective_exposure: dict[str, Exposure] = {}
        self._exposure_quarantined: set[str] = set()
        self._seen: set[tuple] = set()               # (source_ref, event_id)
        self._observations: list[ObservationEvent] = []
        self._seals: dict[tuple, CompletenessSeal] = {}   # (source_ref, epoch_ref)
        self._seal_versions: dict[tuple, int] = {}

        # Capture health. Not epistemic -- operational.
        self.dropped = 0
        self.duplicates = 0
        self.invalid_compare = 0
        self.process_errors = 0
        self._invalid: set[tuple] = set()            # (obs_key, forecast_id)

        self._rebuild()

    # -- rebuild ------------------------------------------------------------

    def _rebuild(self) -> None:
        """Rebuild all derived state from the three ledgers.

        No single damaged record may brick the rebuild -- not a torn tail,
        not an interior corruption, and not a record that parses as JSON
        but is not a valid forecast/observation/seal/outcome. A record this
        build cannot interpret is corruption: skipped and counted
        (corrupted), the same way the ledger counts a bad line, and the
        ledger's own promise stands: 'a damaged byte is not allowed to
        brick a rebuild.'

        The one intentional exception is schema versioning: a record from a
        NEWER writer fails loud (G4), because silently misreading a future
        format is how corruption enters a calibration feed."""
        pending_amendments: list[tuple[ExposureAmendment | None, str | None]] = []
        for record in self.forecasts_l.raw():
            version = self._record_version(record, self.forecasts_l)
            if version is None:
                pending_amendments.append((None, record.get("forecast_id")))
                continue
            kind = record.get("_kind")
            payload = _without_envelope(record)
            if version == 1:
                if kind is not None or {
                    "exposure_basis",
                    "world_commitment",
                }.intersection(payload):
                    self.forecasts_l.corrupted += 1
                    pending_amendments.append((None, payload.get("forecast_id")))
                    continue
                record_kind = _FORECAST_KIND
            else:
                record_kind = kind
            if record_kind == _FORECAST_KIND:
                try:
                    forecast = forecast_from_dict(payload, schema_version=version)
                except (KeyError, TypeError, ValueError):
                    self.forecasts_l.corrupted += 1
                    pending_amendments.append((None, payload.get("forecast_id")))
                    continue
                if forecast.forecast_id in self.forecasts:
                    # Forecast identity is first-write authoritative.  A
                    # conflicting later declaration makes the identity unsafe
                    # for calibration but never replaces history.
                    self.forecasts_l.corrupted += 1
                    self._quarantine_exposure(forecast.forecast_id)
                    continue
                if (
                    version == _SCHEMA_V2
                    and forecast.exposure is Exposure.BLIND
                    and forecast.structural_exposure is not Exposure.BLIND
                ):
                    self.forecasts_l.corrupted += 1
                    self._exposure_quarantined.add(forecast.forecast_id)
                self._register(
                    forecast,
                    version=version,
                    digest=_canonical_record_digest(record),
                )
            elif version == _SCHEMA_V2 and record_kind == _AMENDMENT_KIND:
                target_hint = payload.get("forecast_id")
                try:
                    amendment = exposure_amendment_from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    self.forecasts_l.corrupted += 1
                    pending_amendments.append((None, target_hint))
                    continue
                pending_amendments.append((amendment, amendment.forecast_id))
            else:
                self.forecasts_l.corrupted += 1
                target_hint = payload.get("forecast_id")
                pending_amendments.append((None, target_hint))

        # A correction can only be interpreted after its target declaration.
        # Folding in a second pass also makes duplicate amendments idempotent.
        for amendment, target_hint in pending_amendments:
            if amendment is None:
                if isinstance(target_hint, str):
                    self._quarantine_exposure(target_hint)
                continue
            self._apply_amendment(amendment, rebuilding=True)

        for record in self.evidence_l.raw():
            version = self._record_version(record, self.evidence_l)
            if version is None:
                continue
            kind = record.get("_kind")
            payload = _without_envelope(record)
            if kind == "observation":
                try:
                    obs = observation_from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    self.evidence_l.corrupted += 1
                    continue
                self._seen.add(obs.dedup_key)
                self._observations.append(obs)
            elif kind == "seal":
                try:
                    seal = seal_from_dict(payload, schema_version=version)
                except (KeyError, TypeError, ValueError):
                    self.evidence_l.corrupted += 1
                    continue
                seal_key = (seal.source_ref, seal.epoch_ref)
                self._seals[seal_key] = seal
                self._seal_versions[seal_key] = version
            else:
                self.evidence_l.corrupted += 1

        for record in self.outcomes_l.raw():
            version = self._record_version(record, self.outcomes_l)
            if version is None:
                continue
            kind = record.get("_kind")
            if version == 1:
                if kind is not None:
                    self.outcomes_l.corrupted += 1
                    continue
            elif kind != _OUTCOME_KIND:
                self.outcomes_l.corrupted += 1
                continue
            try:
                outcome = outcome_from_dict(_without_envelope(record))
            except (KeyError, TypeError, ValueError):
                self.outcomes_l.corrupted += 1
                continue
            self.outcomes[outcome.forecast_id] = self._fold_outcome_exposure(outcome)

    @staticmethod
    def _record_version(rec: dict, ledger: Ledger) -> int | None:
        """Return a supported record version without mutating its payload."""
        version = rec.get("_v", 1)
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version <= 0
        ):
            ledger.corrupted += 1
            return None
        if version > _SCHEMA_V2:
            raise ValueError(
                f"ledger written by schema v{version}; this reader "
                f"understands up to v{_SCHEMA_V2}"
            )
        return version

    def _register(self, f: Forecast, *, version: int, digest: str) -> None:
        self.forecasts[f.forecast_id] = f
        self._forecast_versions[f.forecast_id] = version
        self._forecast_digests[f.forecast_id] = digest
        base = f.exposure if version == 1 else f.structural_exposure
        self._base_exposure[f.forecast_id] = base
        self._effective_exposure[f.forecast_id] = (
            Exposure.EXPOSED
            if f.forecast_id in self._exposure_quarantined
            else base
        )
        key = (f.subject_ref, f.resolution.observable)
        self.index.setdefault(key, []).append(f.forecast_id)

    # -- declaration ----------------------------------------------------------

    def declare(self, spec: ObservableSpec) -> None:
        self.observables[spec.name] = spec

    # -- authoring ------------------------------------------------------------

    def author(self, forecast: Forecast) -> str:
        """Well-formedness gate. A forecast whose condition names an
        observable the host does not declare cannot be authored -- not
        rejected later, not resolved UNRESOLVABLE later. Refused here.

        An unfalsifiable claim is free to produce and therefore worthless."""
        if not isinstance(forecast, Forecast):
            raise TypeError("author() requires a Forecast")
        payload = _tag_v2(forecast, _FORECAST_KIND)
        # Register the value decoded from the exact bytes about to be
        # persisted, never the caller-owned object.  This also validates that
        # every nested value survives canonical serialization.
        detached = forecast_from_dict(
            _without_envelope(payload),
            schema_version=_SCHEMA_V2,
        )
        obs_name = detached.resolution.observable
        if obs_name not in self.observables:
            raise ValueError(
                f"undeclared observable {obs_name!r}: a forecast must name "
                f"something the host actually emits"
            )
        if (
            detached.exposure is Exposure.BLIND
            and detached.structural_exposure is not Exposure.BLIND
        ):
            raise ValueError("BLIND forecast requires a complete, disjoint exposure basis")
        validate_world_contract(detached, schema_version=_SCHEMA_V2)
        if detached.forecast_id in self.forecasts:
            raise ValueError(f"forecast {detached.forecast_id} already exists")
        self.forecasts_l.append_dict(payload)
        self._register(
            detached,
            version=_SCHEMA_V2,
            digest=_canonical_record_digest(payload),
        )
        return detached.forecast_id

    def forecast_digest(self, forecast_id: str) -> str:
        """Canonical digest to which an exposure amendment must bind."""
        try:
            return self._forecast_digests[forecast_id]
        except KeyError:
            raise KeyError(f"unknown forecast {forecast_id}") from None

    def effective_exposure(self, forecast_id: str) -> Exposure:
        """Exposure after structural validation and monotone amendments."""
        try:
            return self._effective_exposure[forecast_id]
        except KeyError:
            raise KeyError(f"unknown forecast {forecast_id}") from None

    def amend_exposure(self, amendment: ExposureAmendment) -> None:
        """Append a digest-bound BLIND -> EXPOSED correction.

        This is an authoring API, not a host capture path: malformed requests
        raise before anything is appended.  Replaying the same valid
        amendment remains effect-idempotent.
        """
        if not isinstance(amendment, ExposureAmendment):
            raise TypeError("amend_exposure() requires an ExposureAmendment")
        payload = _tag_v2(amendment, _AMENDMENT_KIND)
        detached = exposure_amendment_from_dict(_without_envelope(payload))
        error = self._amendment_error(detached)
        if error is not None:
            raise ValueError(error)
        result = self.forecasts_l.compare_and_append(
            payload,
            ("_kind", "forecast_id", "target_forecast_digest"),
            lambda _existing, _candidate: True,
        )
        # compare_and_append synchronized the complete durable tail under the
        # same lock as its decision.  Merge every discovered record, not just
        # our candidate, so a race cannot disappear until restart.
        self._merge_forecast_tail(result.records)
        if result.conflict:
            raise ValueError("conflicting exposure amendment transition")

    def _amendment_error(self, amendment: ExposureAmendment) -> str | None:
        forecast = self.forecasts.get(amendment.forecast_id)
        if forecast is None:
            return f"unknown forecast {amendment.forecast_id}"
        if self._forecast_digests[amendment.forecast_id] != amendment.target_forecast_digest:
            return "exposure amendment target digest does not match forecast"
        if self._base_exposure[amendment.forecast_id] is not Exposure.BLIND:
            return "exposure amendment target was not structurally BLIND"
        try:
            if parse(amendment.amended_at) < parse(forecast.created_at):
                return "exposure amendment predates its target forecast"
        except (TypeError, ValueError):
            return "exposure amendment timestamp cannot be compared with target"
        return None

    def _apply_amendment(
        self,
        amendment: ExposureAmendment,
        *,
        rebuilding: bool,
    ) -> bool:
        error = self._amendment_error(amendment)
        if error is not None:
            if amendment.forecast_id in self.forecasts:
                self._quarantine_exposure(amendment.forecast_id)
            if rebuilding:
                self.forecasts_l.corrupted += 1
                return False
            raise ValueError(error)
        if amendment not in self.amendments:
            self.amendments.append(amendment)
        self._effective_exposure[amendment.forecast_id] = Exposure.EXPOSED
        existing = self.outcomes.get(amendment.forecast_id)
        if existing is not None:
            self.outcomes[amendment.forecast_id] = self._fold_outcome_exposure(existing)
        return True

    def _merge_forecast_tail(self, records: tuple[dict, ...]) -> None:
        """Fold forecast-ledger records discovered by an atomic append."""
        pending: list[tuple[ExposureAmendment | None, str | None]] = []
        for record in records:
            version = self._record_version(record, self.forecasts_l)
            if version is None:
                pending.append((None, record.get("forecast_id")))
                continue
            kind = record.get("_kind")
            payload = _without_envelope(record)
            if version == 1:
                if kind is not None or {
                    "exposure_basis",
                    "world_commitment",
                }.intersection(payload):
                    self.forecasts_l.corrupted += 1
                    pending.append((None, payload.get("forecast_id")))
                    continue
                record_kind = _FORECAST_KIND
            else:
                record_kind = kind

            if record_kind == _FORECAST_KIND:
                try:
                    forecast = forecast_from_dict(payload, schema_version=version)
                except (KeyError, TypeError, ValueError):
                    self.forecasts_l.corrupted += 1
                    pending.append((None, payload.get("forecast_id")))
                    continue
                digest = _canonical_record_digest(record)
                existing_digest = self._forecast_digests.get(forecast.forecast_id)
                if existing_digest is not None:
                    if existing_digest != digest:
                        self.forecasts_l.corrupted += 1
                        self._quarantine_exposure(forecast.forecast_id)
                    continue
                if (
                    version == _SCHEMA_V2
                    and forecast.exposure is Exposure.BLIND
                    and forecast.structural_exposure is not Exposure.BLIND
                ):
                    self.forecasts_l.corrupted += 1
                    self._exposure_quarantined.add(forecast.forecast_id)
                self._register(forecast, version=version, digest=digest)
            elif version == _SCHEMA_V2 and record_kind == _AMENDMENT_KIND:
                target_hint = payload.get("forecast_id")
                try:
                    decoded = exposure_amendment_from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    self.forecasts_l.corrupted += 1
                    pending.append((None, target_hint))
                    continue
                pending.append((decoded, decoded.forecast_id))
            else:
                self.forecasts_l.corrupted += 1
                pending.append((None, payload.get("forecast_id")))

        for discovered, target_hint in pending:
            if discovered is None:
                if isinstance(target_hint, str):
                    self._quarantine_exposure(target_hint)
                continue
            self._apply_amendment(discovered, rebuilding=True)

    def _quarantine_exposure(self, forecast_id: str) -> None:
        self._exposure_quarantined.add(forecast_id)
        if forecast_id in self._effective_exposure:
            self._effective_exposure[forecast_id] = Exposure.EXPOSED
        existing = self.outcomes.get(forecast_id)
        if existing is not None:
            self.outcomes[forecast_id] = self._fold_outcome_exposure(existing)

    # -- capture (host path) --------------------------------------------------

    def capture(self, event: ObservationEvent | CompletenessSeal) -> bool:
        """Called from the host's own path. MUST NOT RAISE.

        Returns True if the record was durably accepted. A False here means
        the substrate lost something and knows it -- which is the only
        acceptable kind of loss.

        Cross-process once-only: when the in-memory dedup set misses (a fresh
        process racing with another), the decision is made under the append
        lock against the file, so two hosts capturing the same envelope
        cannot both win."""
        try:
            if isinstance(event, ObservationEvent):
                if event.observable not in self.observables:
                    self.dropped += 1
                    return False
                if event.dedup_key in self._seen:
                    self.duplicates += 1
                    return True          # at-least-once transport, effect-once ingest
                if self.evidence_l.append_observation_once(
                        _tag(event, "observation"), event.dedup_key):
                    self._seen.add(event.dedup_key)
                    self._observations.append(event)
                else:
                    # A racing host already persisted this envelope. Accepted,
                    # not re-ingested -- and remembered so it stays cheap.
                    self.duplicates += 1
                    self._seen.add(event.dedup_key)
                return True
            elif isinstance(event, CompletenessSeal):
                self.evidence_l.append_dict(_tag(event, "seal"))
                seal_key = (event.source_ref, event.epoch_ref)
                self._seals[seal_key] = event
                self._seal_versions[seal_key] = _SCHEMA_V2
                return True
            self.dropped += 1
            return False
        except Exception:                 # noqa: BLE001 -- deliberate
            self.dropped += 1
            return False

    # -- resolution ------------------------------------------------------------

    def process(self, at: str | None = None) -> list[Outcome]:
        """Drain evidence against the index, then sweep expiry.

        Called at explicit boundaries -- query, flush, a host finalizer.
        Nothing in the system requires a process to be running.

        Guaranteed not to raise. An internal defect is counted in
        process_errors and surfaced by status(), never thrown at the host."""
        at = at or now()
        produced: list[Outcome] = []
        try:
            produced += self._resolve_from_evidence()
            produced += self._sweep_expired(at)
        except Exception:                 # noqa: BLE001 -- OUTWARD law
            self.process_errors += 1
        return produced

    def _resolve_from_evidence(self) -> list[Outcome]:
        produced = []
        for obs in self._observations:
            key = (obs.subject_ref, obs.observable)
            for fid in self.index.get(key, []):        # indexed. never a scan.
                f = self.forecasts[fid]
                existing = self.outcomes.get(fid)
                if existing is not None and existing.terminal is not Terminal.UNRESOLVABLE:
                    continue                            # already terminal
                try:
                    if not f.resolution.accepts(obs.validity):
                        continue                        # exogenous != correct
                    if parse(obs.arrived_at) > parse(f.resolution.horizon):
                        continue                        # arrived after horizon
                except (TypeError, ValueError, KeyError):
                    # A malformed observation must never abort the pass --
                    # and, since it persists, must never be allowed to
                    # abort every later pass either. Fail closed: counted
                    # once, never a verdict, keep draining.
                    if (obs.dedup_key, fid) not in self._invalid:
                        self._invalid.add((obs.dedup_key, fid))
                        self.invalid_compare += 1
                    continue
                assert f.resolution.adjudication is Adjudication.FIRST_VALID_TERMINAL
                try:
                    produced.append(self._score(f, obs))
                except (TypeError, ValueError):
                    # An incomparable value is not evidence. Fail closed: a
                    # malformed observation must never become a HIT or a
                    # MISS. The forecast stays open; the attempt is counted.
                    if (obs.dedup_key, fid) not in self._invalid:
                        self._invalid.add((obs.dedup_key, fid))
                        self.invalid_compare += 1
        return produced

    def _score(self, f: Forecast, obs: ObservationEvent) -> Outcome:
        held = compare(obs.value, f.resolution.comparator, f.resolution.threshold)
        y = 1.0 if held else 0.0
        outcome = Outcome(
            forecast_id=f.forecast_id,
            terminal=Terminal.RESOLVED,
            verdict=Verdict.HIT if held else Verdict.MISS,
            observation_key=obs.dedup_key,
            predicted=f.probability,
            observed=obs.value,
            brier=brier(f.probability, y),
            reason="first valid terminal observation",
            # An EXPOSED forecast was visible to something that could move
            # its target. It may be interesting. It is not calibration data.
            calibration_eligible=(
                self._effective_exposure.get(f.forecast_id) is Exposure.BLIND
            ),
        )
        return self._emit(outcome)

    def _sweep_expired(self, at: str) -> list[Outcome]:
        """Housekeeping, not the epistemic path. May lag arbitrarily.

        An UNRESOLVABLE closure is a housekeeping closure, not a verdict:
        valid in-time evidence that arrives later may reopen it (see
        _resolve_from_evidence). A settled RESOLVED can never be changed."""

        produced: list[Outcome] = []
        for fid, f in self.forecasts.items():
            if fid in self.outcomes:
                continue
            if parse(at) <= parse(f.resolution.horizon):
                continue

            spec = self.observables.get(f.resolution.observable)
            complete = self._stream_complete(f)

            if spec and spec.absence_is_informative() and complete:
                # The channel promised a value, the channel is sealed, and
                # nothing matching arrived. Absence is now evidence.
                produced.append(self._emit(Outcome(
                    forecast_id=fid,
                    terminal=Terminal.RESOLVED,
                    verdict=Verdict.MISS,
                    predicted=f.probability,
                    observed=None,
                    brier=brier(f.probability, 0.0),
                    reason="horizon passed; stream sealed complete; no matching observation",
                    calibration_eligible=(
                        self._effective_exposure.get(f.forecast_id) is Exposure.BLIND
                    ),
                )))
            else:
                # We do not know whether it did not happen or whether the
                # channel died. Fail closed.
                produced.append(self._emit(Outcome(
                    forecast_id=fid,
                    terminal=Terminal.UNRESOLVABLE,
                    predicted=f.probability,
                    reason=(
                        "horizon passed; evidence completeness unknown"
                        if not complete else
                        "horizon passed; absence carries no information for this observable"
                    ),
                    calibration_eligible=False,
                )))
        return produced

    def _stream_complete(self, f: Forecast) -> bool:
        """A stream is complete only if the observable's OWN channel sealed
        it AND every source_seq from 1..final_source_seq was received.

        A seal names a (source, epoch). A forecast is about an OBSERVABLE,
        and only a seal from a source that actually emits that observable
        can certify the channel's end. A seal from an unrelated source --
        even one that legitimately closed its own stream for this epoch --
        says nothing about this forecast, and must never turn its absence
        into a MISS. A host that declares no sources for an observable
        forfeits absence-as-MISS entirely.

        A drop counter alone is insufficient: it only knows about failures
        it observed."""
        spec = self.observables.get(f.resolution.observable)
        if spec is None or not spec.sources:
            return False                        # channel identity unknown: fail closed
        allowed = spec.sources
        for (source_ref, epoch_ref), seal in self._seals.items():
            if not seal.complete:
                continue
            if seal.subject_ref != f.subject_ref:
                continue
            if source_ref not in allowed:
                continue                        # not this observable's channel
            seen = {
                o.source_seq for o in self._observations
                if o.source_ref == source_ref and o.epoch_ref == epoch_ref
                and o.source_seq is not None
            }
            if seen == set(range(1, seal.final_source_seq + 1)):
                return True
        return False

    def _emit(self, outcome: Outcome) -> Outcome:
        payload = _tag_v2(outcome, _OUTCOME_KIND)
        detached = outcome_from_dict(_without_envelope(payload))
        self.outcomes_l.append_dict(payload)
        effective = self._fold_outcome_exposure(detached)
        self.outcomes[effective.forecast_id] = effective
        return effective

    def _fold_outcome_exposure(self, outcome: Outcome) -> Outcome:
        """Eligibility can only stay unchanged or become false."""
        eligible = bool(outcome.calibration_eligible) and (
            self._effective_exposure.get(outcome.forecast_id) is Exposure.BLIND
        )
        if outcome.calibration_eligible is eligible:
            return outcome
        return replace(outcome, calibration_eligible=eligible)

    # -- health -------------------------------------------------------------

    def status(self, at: str | None = None) -> dict:
        """Three numbers that matter, then drill-down.

        Separating closure from scoreability from capture integrity is what
        lets 'closure 99%, scoreable 71%, drop 0.1%' be told apart from
        'closure 72%, scoreable 71%, drop 27%'. Very different failures."""
        at = at or now()
        try:
            parsed_at = parse(at)
        except ValueError:
            parsed_at = parse(now())            # a broken clock is still a clock
        due = [f for f in self.forecasts.values()
               if parsed_at > parse(f.resolution.horizon)]
        terminal = [f for f in due if f.forecast_id in self.outcomes]
        scoreable = [f for f in due
                     if (o := self.outcomes.get(f.forecast_id))
                     and o.terminal is Terminal.RESOLVED
                     and o.calibration_eligible]
        captured = len(self._observations) + self.duplicates + self.dropped

        return {
            "closure_rate":   _rate(len(terminal), len(due)),
            "scoreable_rate": _rate(len(scoreable), len(due)),
            "capture": {
                "accepted": len(self._observations),
                "duplicates": self.duplicates,
                "dropped": self.dropped,
                "drop_rate": _rate(self.dropped, captured),
                "sealed_epochs": len(self._seals),
                "invalid_compare": self.invalid_compare,
                "process_errors": self.process_errors,
                "tail_loss": (
                    self.forecasts_l.tail_loss
                    + self.evidence_l.tail_loss
                    + self.outcomes_l.tail_loss
                ),
                "corrupted": (
                    self.forecasts_l.corrupted
                    + self.evidence_l.corrupted
                    + self.outcomes_l.corrupted
                ),
            },
            "forecasts": {
                "total": len(self.forecasts),
                "due": len(due),
                "active": len(self.forecasts) - len(self.outcomes),
                "prediction_debt": len(due) - len(terminal),
            },
            "outcomes": _tally(self.outcomes.values()),
            "index": {
                "keys": len(self.index),
                "max_watchers": max((len(v) for v in self.index.values()), default=0),
            },
        }


# --------------------------------------------------------------------------

def _tag(record, kind: str) -> dict:
    """Evidence ledger holds two record types; tag them on the way in."""
    payload = json.loads(to_json(record))
    payload["_kind"] = kind
    payload["_v"] = _SCHEMA_V2 if kind == "seal" else LEDGER_SCHEMA
    return payload


def _tag_v2(record, kind: str) -> dict:
    """Envelope a schema-v2 record with an explicit semantic kind."""
    payload = json.loads(to_json(record))
    payload["_kind"] = kind
    payload["_v"] = _SCHEMA_V2
    return payload


def _without_envelope(record: dict) -> dict:
    return {key: value for key, value in record.items() if key not in {"_kind", "_v"}}


def _canonical_record_digest(record: dict) -> str:
    canonical = json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def _tally(outcomes) -> dict:
    out: dict[str, int] = {}
    for o in outcomes:
        key = o.terminal.value if o.verdict is None else f"{o.terminal.value}/{o.verdict.value}"
        out[key] = out.get(key, 0) + 1
    return out
