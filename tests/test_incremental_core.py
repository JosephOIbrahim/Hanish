"""Incremental resolution, completeness, and terminal concurrency gates."""

from __future__ import annotations

import json
import threading

import pytest

from hanish import Substrate
from hanish.future.claims import (
    Comparator,
    EmissionSemantics,
    Exposure,
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
from hanish.past.ledger import to_json

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
