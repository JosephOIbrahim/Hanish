"""Incremental resolution, completeness, and terminal concurrency gates."""

from __future__ import annotations

import json
import os
import threading

import pytest

from hanish import Substrate
from hanish.future.claims import (
    Comparator,
    EmissionSemantics,
    Exposure,
    ExposureAmendment,
    ExposureBasis,
    Forecast,
    ObservableSpec,
    ResolutionSpec,
)
from hanish.past.events import (
    CompletenessSeal,
    ObservationEvent,
    Outcome,
    Terminal,
    Validity,
    Verdict,
)
from hanish.past.ledger import Ledger, to_json

SOURCE = "host:fixture"
SUBJECT = "subject:fixture"
EPOCH = "epoch:fixture"
RESULT = "fixture.result"
LEG = "fixture.leg"
CREATED = "2026-01-01T00:00:00+00:00"
ARRIVED = "2026-01-01T01:00:00+00:00"
HORIZON = "2026-01-01T02:00:00+00:00"
AFTER = "2026-01-01T03:00:00+00:00"


def specs() -> dict[str, ObservableSpec]:
    return {
        RESULT: ObservableSpec(
            name=RESULT,
            value_type="bool",
            emission=EmissionSemantics.TERMINAL,
            sources=(SOURCE,),
        ),
        LEG: ObservableSpec(
            name=LEG,
            value_type="bool",
            emission=EmissionSemantics.PER_SUBJECT,
            sources=(SOURCE,),
        ),
    }


def forecast(
    *,
    forecast_id: str = "f_incremental",
    created_at: str = CREATED,
    horizon: str = HORIZON,
) -> Forecast:
    return Forecast(
        subject_ref=SUBJECT,
        claim="the fixture result is true",
        probability=0.6,
        exposure=Exposure.EXPOSED,
        forecast_id=forecast_id,
        created_at=created_at,
        resolution=ResolutionSpec(
            observable=RESULT,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=horizon,
        ),
    )


def blind_forecast(*, forecast_id: str = "f_blind_incremental") -> Forecast:
    return Forecast(
        subject_ref=SUBJECT,
        claim="the fixture result is true",
        probability=0.6,
        exposure=Exposure.BLIND,
        exposure_basis=ExposureBasis(
            author_ref="author:fixture",
            seen_by=(),
            capable_actors=("actor:target",),
            seen_by_complete=True,
            capable_actors_complete=True,
            separation_control_ref="control:fixture",
            attested_by="host:fixture",
            attested_at=CREATED,
        ),
        authored_by="author:fixture",
        forecast_id=forecast_id,
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=RESULT,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=HORIZON,
        ),
    )


def event(
    event_id: str,
    *,
    value: bool = True,
    observable: str = RESULT,
    sequence: int | None = None,
    arrived_at: str = ARRIVED,
    validity: Validity = Validity.VALID,
) -> ObservationEvent:
    return ObservationEvent(
        source_ref=SOURCE,
        event_id=event_id,
        subject_ref=SUBJECT,
        observable=observable,
        value=value,
        source_seq=sequence,
        epoch_ref=EPOCH if sequence is not None else None,
        arrived_at=arrived_at,
        validity=validity,
    )


def test_repeated_process_does_not_rescan_observations(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    substrate.author(forecast())
    substrate.capture(event("invalid", validity=Validity.INVALID))

    assert substrate.process(at="2026-01-01T01:30:00+00:00") == []
    assert substrate.evidence_comparisons == 1
    assert substrate.process(at="2026-01-01T01:31:00+00:00") == []
    assert substrate.evidence_comparisons == 1

    substrate.capture(event("valid", arrived_at="2026-01-01T01:10:00+00:00"))
    produced = substrate.process(at="2026-01-01T01:32:00+00:00")
    assert produced[0].verdict is Verdict.HIT
    assert substrate.evidence_comparisons == 2


def test_late_registration_backfills_only_evidence_authored_before_arrival(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    substrate.capture(event("historical"))

    eligible = substrate.author(forecast(forecast_id="f_eligible"))
    substrate.process(at="2026-01-01T01:30:00+00:00")
    assert substrate.outcomes[eligible].verdict is Verdict.HIT

    hindsight = substrate.author(
        forecast(
            forecast_id="f_hindsight",
            created_at="2026-01-01T01:30:00+00:00",
        )
    )
    assert substrate.process(at="2026-01-01T01:40:00+00:00") == []
    substrate.process(at=AFTER)
    assert substrate.outcomes[hindsight].terminal is Terminal.UNRESOLVABLE


def test_capture_race_loser_ingests_winner_without_restart(tmp_path):
    first = Substrate(tmp_path, observables=specs())
    first.author(forecast())
    second = Substrate(tmp_path, observables=specs())
    envelope = event("same-envelope")

    assert first.capture(envelope)
    assert second.capture(envelope)
    assert second.duplicates == 1
    assert second.process(at="2026-01-01T01:30:00+00:00")[0].verdict is Verdict.HIT


def test_cross_process_exposure_amendment_is_folded_before_next_decision(tmp_path):
    first = Substrate(tmp_path, observables=specs())
    forecast_id = first.author(blind_forecast())
    second = Substrate(tmp_path, observables=specs())
    assert first.capture(event("blind-result"))
    assert first.process(at="2026-01-01T01:30:00+00:00")[0].calibration_eligible

    second.amend_exposure(
        ExposureAmendment(
            forecast_id=forecast_id,
            target_forecast_digest=second.forecast_digest(forecast_id),
            reason_code="EXPOSURE_DISCOVERED",
            amended_by="host:fixture",
            amended_at="2026-01-01T01:31:00+00:00",
        )
    )

    assert first.process(at="2026-01-01T01:32:00+00:00") == []
    assert first.effective_exposure(forecast_id) is Exposure.EXPOSED
    assert not first.outcomes[forecast_id].calibration_eligible
    assert len(first.amendments) == 1
    assert len(list(first.outcomes_l.raw())) == 1


@pytest.mark.parametrize("mode", ["truncate", "replace"])
def test_generation_reset_replaces_evidence_and_outcome_state_without_ghosts(
    tmp_path,
    mode,
):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    assert substrate.capture(event("old-generation"))
    assert substrate.process(at="2026-01-01T01:30:00+00:00")[0].verdict is Verdict.HIT

    for name in ("evidence.jsonl", "outcomes.jsonl"):
        path = tmp_path / name
        if mode == "truncate":
            path.write_bytes(b"")
        else:
            replacement = tmp_path / f"{name}.replacement"
            replacement.write_bytes(b"")
            replacement.replace(path)

    assert substrate.process(at="2026-01-01T01:31:00+00:00") == []
    assert substrate._observations == []
    assert forecast_id not in substrate.outcomes
    assert substrate.status(at="2026-01-01T01:31:00+00:00")["capture"][
        "generation_resets"
    ] == 2

    assert substrate.capture(event("new-generation"))
    assert substrate.process(at="2026-01-01T01:32:00+00:00")[0].verdict is Verdict.HIT


def test_forecast_generation_reset_removes_forecast_and_dependent_outcome_ghosts(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    assert substrate.capture(event("old-forecast"))
    assert substrate.process(at="2026-01-01T01:30:00+00:00")[0].verdict is Verdict.HIT
    replacement = tmp_path / "forecasts.replacement"
    replacement.write_bytes(b"")
    replacement.replace(tmp_path / "forecasts.jsonl")

    assert substrate.process(at="2026-01-01T01:31:00+00:00") == []
    assert forecast_id not in substrate.forecasts
    assert forecast_id not in substrate.outcomes
    assert substrate.forecasts_l.generation_resets == 1


def test_generation_reset_recounts_semantic_damage_once(tmp_path):
    path = tmp_path / "forecasts.jsonl"
    Ledger(path).append_dict({"_v": 0, "forecast_id": "f_corrupt"})
    substrate = Substrate(tmp_path, observables=specs())
    assert substrate.forecasts_l.corrupted == 1
    original = path.stat()
    os.utime(
        path,
        ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000),
    )

    assert substrate.process(at="2026-01-01T01:30:00+00:00") == []
    assert substrate.forecasts_l.generation_resets == 1
    assert substrate.forecasts_l.corrupted == 1


def test_reused_event_identity_with_different_content_fails_closed(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    assert substrate.capture(event("identity", value=True))
    assert not substrate.capture(event("identity", value=False))
    assert substrate.identity_conflicts == 1

    substrate.process(at="2026-01-01T01:30:00+00:00")
    assert substrate.outcomes[forecast_id].verdict is Verdict.HIT
    assert len(list(substrate.evidence_l.raw())) == 1


def test_duplicate_sequence_cannot_certify_completeness(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    substrate.capture(event("leg-a", observable=LEG, sequence=1))
    substrate.capture(event("leg-b", observable=LEG, sequence=1))
    substrate.capture(
        CompletenessSeal(
            source_ref=SOURCE,
            epoch_ref=EPOCH,
            subject_ref=SUBJECT,
            final_source_seq=2,
        )
    )

    substrate.process(at=AFTER)
    assert substrate.outcomes[forecast_id].terminal is Terminal.UNRESOLVABLE


@pytest.mark.parametrize(
    "sealed_at",
    ["2025-12-31T23:59:59+00:00", CREATED],
)
def test_seal_must_postdate_forecast_to_certify_absence(tmp_path, sealed_at):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    assert substrate.capture(
        CompletenessSeal(
            source_ref=SOURCE,
            epoch_ref=EPOCH,
            subject_ref=SUBJECT,
            final_source_seq=0,
            sealed_at=sealed_at,
        )
    )

    substrate.process(at=AFTER)

    assert substrate.outcomes[forecast_id].terminal is Terminal.UNRESOLVABLE
    assert substrate.outcomes[forecast_id].verdict is None


def test_late_complete_seal_advances_one_provisional_outcome_to_miss(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())

    first = substrate.process(at=AFTER)
    assert first[0].terminal is Terminal.UNRESOLVABLE
    assert len(list(substrate.outcomes_l.raw())) == 1
    assert substrate.process(at=AFTER) == []
    assert len(list(substrate.outcomes_l.raw())) == 1

    assert substrate.capture(
        CompletenessSeal(
            source_ref=SOURCE,
            epoch_ref=EPOCH,
            subject_ref=SUBJECT,
            final_source_seq=0,
            sealed_at="2026-01-01T02:01:00+00:00",
        )
    )
    settled = substrate.process(at=AFTER)

    assert settled[0].terminal is Terminal.RESOLVED
    assert settled[0].verdict is Verdict.MISS
    assert substrate.outcomes[forecast_id].verdict is Verdict.MISS
    assert len(list(substrate.outcomes_l.raw())) == 2
    assert substrate.process(at=AFTER) == []
    assert len(list(substrate.outcomes_l.raw())) == 2


@pytest.mark.parametrize("final_sequence", [-1, True, 1_000_001])
def test_invalid_seal_bounds_are_dropped(final_sequence, tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    seal = CompletenessSeal(
        source_ref=SOURCE,
        epoch_ref=EPOCH,
        subject_ref=SUBJECT,
        final_source_seq=final_sequence,
    )
    assert not substrate.capture(seal)
    assert list(substrate.evidence_l.raw()) == []


def test_conflicting_refinalization_cannot_upgrade_an_incomplete_stream(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    incomplete = CompletenessSeal(
        source_ref=SOURCE,
        epoch_ref=EPOCH,
        subject_ref=SUBJECT,
        final_source_seq=0,
        complete=False,
        sealed_at="2026-01-01T02:01:00+00:00",
    )
    complete = CompletenessSeal(
        source_ref=SOURCE,
        epoch_ref=EPOCH,
        subject_ref=SUBJECT,
        final_source_seq=0,
        complete=True,
        sealed_at="2026-01-01T02:02:00+00:00",
    )
    assert substrate.capture(incomplete)
    assert not substrate.capture(complete)
    substrate.process(at=AFTER)
    assert substrate.outcomes[forecast_id].terminal is Terminal.UNRESOLVABLE
    assert substrate.seal_conflicts == 1


def test_terminal_outcome_compare_and_append_has_one_winner(tmp_path):
    first = Substrate(tmp_path, observables=specs())
    forecast_id = first.author(forecast())
    second = Substrate(tmp_path, observables=specs())
    barrier = threading.Barrier(2)
    results = []

    def emit(substrate: Substrate, verdict: Verdict, outcome_id: str) -> None:
        barrier.wait()
        results.append(
            substrate._emit(
                Outcome(
                    forecast_id=forecast_id,
                    terminal=Terminal.RESOLVED,
                    verdict=verdict,
                    predicted=0.6,
                    observed=verdict is Verdict.HIT,
                    outcome_id=outcome_id,
                    resolved_at="2026-01-01T01:05:00+00:00",
                )
            )
        )

    threads = [
        threading.Thread(target=emit, args=(first, Verdict.HIT, "o_hit")),
        threading.Thread(target=emit, args=(second, Verdict.MISS, "o_miss")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(appended for _outcome, appended in results) == 1
    assert len(list(first.outcomes_l.raw())) == 1
    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.outcomes[forecast_id].outcome_id in {"o_hit", "o_miss"}


def test_replay_uses_first_settled_outcome_not_last_record(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    first = Outcome(
        forecast_id=forecast_id,
        terminal=Terminal.RESOLVED,
        verdict=Verdict.HIT,
        outcome_id="o_first",
        resolved_at="2026-01-01T01:05:00+00:00",
    )
    second = Outcome(
        forecast_id=forecast_id,
        terminal=Terminal.RESOLVED,
        verdict=Verdict.MISS,
        outcome_id="o_second",
        resolved_at="2026-01-01T01:06:00+00:00",
    )
    substrate.outcomes_l.append_dict(
        {**json.loads(to_json(first)), "_kind": "outcome", "_v": 2}
    )
    substrate.outcomes_l.append_dict(
        {**json.loads(to_json(second)), "_kind": "outcome", "_v": 2}
    )

    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.outcomes[forecast_id].outcome_id == "o_first"
    assert reopened.outcome_conflicts == 1


def test_declaration_collision_and_strict_bool_value_fail_closed(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    with pytest.raises(ValueError, match="namespace collision"):
        substrate.declare(
            ObservableSpec(
                name=RESULT,
                value_type="int",
                emission=EmissionSemantics.TERMINAL,
                sources=(SOURCE,),
            )
        )
    assert not substrate.capture(event("integer-bool", value=1))
