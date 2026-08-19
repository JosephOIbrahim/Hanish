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

import json
from pathlib import Path

from ..future.claims import (
    Adjudication,
    Exposure,
    Forecast,
    ObservableSpec,
    forecast_from_dict,
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
        self._seen: set[tuple] = set()               # (source_ref, event_id)
        self._observations: list[ObservationEvent] = []
        self._seals: dict[tuple, CompletenessSeal] = {}   # (source_ref, epoch_ref)

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
        for rec in self.forecasts_l.raw():
            if not self._version_ok(rec, self.forecasts_l):
                continue
            try:
                f = forecast_from_dict(rec)
            except (KeyError, TypeError, ValueError):
                self.forecasts_l.corrupted += 1
                continue
            self._register(f)

        for rec in self.evidence_l.raw():
            kind = rec.pop("_kind", None)
            if not self._version_ok(rec, self.evidence_l):
                continue
            if kind == "observation":
                try:
                    obs = observation_from_dict(rec)
                except (KeyError, TypeError, ValueError):
                    self.evidence_l.corrupted += 1
                    continue
                self._seen.add(obs.dedup_key)
                self._observations.append(obs)
            elif kind == "seal":
                try:
                    seal = seal_from_dict(rec)
                except (KeyError, TypeError, ValueError):
                    self.evidence_l.corrupted += 1
                    continue
                self._seals[(seal.source_ref, seal.epoch_ref)] = seal
            else:
                self.evidence_l.corrupted += 1
                continue

        for rec in self.outcomes_l.raw():
            if not self._version_ok(rec, self.outcomes_l):
                continue
            try:
                o = outcome_from_dict(rec)
            except (KeyError, TypeError, ValueError):
                self.outcomes_l.corrupted += 1
                continue
            self.outcomes[o.forecast_id] = o

    @staticmethod
    def _version_ok(rec: dict, ledger: Ledger) -> bool:
        """Gate the schema version of one record; True means readable.

        A record without a version tag is v1. A non-integer tag is
        corruption. A tag NEWER than this reader fails loud -- older code
        must never silently misread newer data. Pops the tag so decoders
        never see a field they do not know."""
        version = rec.pop("_v", 1)
        if isinstance(version, bool) or not isinstance(version, int):
            ledger.corrupted += 1
            return False
        if version > LEDGER_SCHEMA:
            raise ValueError(
                f"ledger written by schema v{version}; this reader "
                f"understands up to v{LEDGER_SCHEMA}"
            )
        return True

    def _register(self, f: Forecast) -> None:
        self.forecasts[f.forecast_id] = f
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
        obs_name = forecast.resolution.observable
        if obs_name not in self.observables:
            raise ValueError(
                f"undeclared observable {obs_name!r}: a forecast must name "
                f"something the host actually emits"
            )
        if forecast.forecast_id in self.forecasts:
            raise ValueError(f"forecast {forecast.forecast_id} already exists")
        self.forecasts_l.append_dict(_tag_record(forecast))
        self._register(forecast)
        return forecast.forecast_id

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
                self._seals[(event.source_ref, event.epoch_ref)] = event
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
            calibration_eligible=(f.exposure is Exposure.BLIND),
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
                    calibration_eligible=(f.exposure is Exposure.BLIND),
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
            if epoch_ref != f.subject_ref:
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
        self.outcomes_l.append_dict(_tag_record(outcome))
        self.outcomes[outcome.forecast_id] = outcome
        return outcome

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
    payload["_v"] = LEDGER_SCHEMA
    return payload


def _tag_record(record) -> dict:
    """Schema-tag a record on a single-type ledger (forecasts, outcomes).
    These need the version too: an untagged record would be read as v1, so
    a future writer could never be distinguished from a v1 record."""
    payload = json.loads(to_json(record))
    payload["_v"] = LEDGER_SCHEMA
    return payload


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def _tally(outcomes) -> dict:
    out: dict[str, int] = {}
    for o in outcomes:
        key = o.terminal.value if o.verdict is None else f"{o.terminal.value}/{o.verdict.value}"
        out[key] = out.get(key, 0) + 1
    return out
