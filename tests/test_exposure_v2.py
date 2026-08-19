"""Schema-v2 structural exposure and honest world commitments."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

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
    WorldRefCapability,
    canonical_world_commitment,
    world_ref_for,
)
from hanish.past.events import (
    CompletenessSeal,
    ObservationEvent,
    Outcome,
    Terminal,
    Verdict,
)
from hanish.past.ledger import to_json

CREATED = "2026-01-01T00:00:00+00:00"
ATTESTED = "2025-12-31T23:59:00+00:00"
ARRIVED = "2026-01-01T00:10:00+00:00"
AMENDED = "2026-01-01T00:20:00+00:00"
HORIZON = "2026-01-02T00:00:00+00:00"
OBSERVABLE = "host.required_result"
SOURCE = "host-source"


def specs() -> dict[str, ObservableSpec]:
    return {
        OBSERVABLE: ObservableSpec(
            name=OBSERVABLE,
            value_type="bool",
            emission=EmissionSemantics.TERMINAL,
            sources=(SOURCE,),
        )
    }


def blind_basis(**changes) -> ExposureBasis:
    values = {
        "author_ref": "forecaster",
        "seen_by": ("auditor",),
        "capable_actors": ("executor",),
        "seen_by_complete": True,
        "capable_actors_complete": True,
        "separation_control_ref": "embargo:control-1",
        "attested_by": "host-attestor",
        "attested_at": ATTESTED,
    }
    values.update(changes)
    return ExposureBasis(**values)


def forecast(*, exposure=Exposure.BLIND, basis=None, forecast_id="f_v2") -> Forecast:
    return Forecast(
        subject_ref="subject:fixture",
        claim="the required result is true",
        probability=0.75,
        exposure=exposure,
        exposure_basis=blind_basis() if basis is None and exposure is Exposure.BLIND else basis,
        authored_by="forecaster",
        forecast_id=forecast_id,
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=OBSERVABLE,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=HORIZON,
        ),
    )


def resolve_true(substrate: Substrate) -> Outcome:
    assert substrate.capture(
        ObservationEvent(
            source_ref=SOURCE,
            event_id="event-1",
            subject_ref="subject:fixture",
            observable=OBSERVABLE,
            value=True,
            arrived_at=ARRIVED,
        )
    )
    return substrate.process()[0]


def test_forecast_basis_and_outcome_are_frozen():
    value = forecast()
    with pytest.raises(FrozenInstanceError):
        value.exposure = Exposure.EXPOSED
    with pytest.raises(FrozenInstanceError):
        value.exposure_basis.attested_by = "replacement"

    outcome = Outcome(forecast_id="f", terminal=Terminal.UNRESOLVABLE)
    with pytest.raises(FrozenInstanceError):
        outcome.calibration_eligible = True


@pytest.mark.parametrize(
    "basis",
    [
        None,
        blind_basis(seen_by_complete=False),
        blind_basis(capable_actors=None),
        blind_basis(capable_actors=("forecaster",)),
        blind_basis(author_ref="someone-else"),
        blind_basis(separation_control_ref=None),
        blind_basis(attested_by=None),
        blind_basis(attested_at="2026-01-01T00:01:00+00:00"),
    ],
)
def test_blind_authoring_requires_complete_disjoint_attested_basis(tmp_path, basis):
    substrate = Substrate(tmp_path, observables=specs())
    value = Forecast(
        subject_ref="subject:fixture",
        claim="unsupported blindness",
        probability=0.5,
        exposure=Exposure.BLIND,
        exposure_basis=basis,
        authored_by="forecaster",
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=OBSERVABLE,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=HORIZON,
        ),
    )
    with pytest.raises(ValueError, match="complete, disjoint exposure basis"):
        substrate.author(value)


def test_exposure_basis_rejects_mutable_or_ambiguous_identity_sets():
    with pytest.raises(TypeError, match="must be a tuple"):
        blind_basis(seen_by=["auditor"])
    with pytest.raises(ValueError, match="duplicate"):
        blind_basis(capable_actors=("executor", "executor"))
    with pytest.raises(TypeError, match="must be bool"):
        blind_basis(seen_by_complete=1)


def test_valid_blind_and_explicit_exposed_are_structural(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    blind_id = substrate.author(forecast())
    exposed_id = substrate.author(
        forecast(exposure=Exposure.EXPOSED, basis=None, forecast_id="f_exposed")
    )
    assert substrate.effective_exposure(blind_id) is Exposure.BLIND
    assert substrate.effective_exposure(exposed_id) is Exposure.EXPOSED


def test_author_registers_a_detached_canonical_forecast(tmp_path):
    threshold = {"answer": [True]}
    value = Forecast(
        subject_ref="subject:fixture",
        claim="detached nested input",
        probability=0.5,
        exposure=Exposure.EXPOSED,
        authored_by="forecaster",
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=OBSERVABLE,
            comparator=Comparator.EQ,
            threshold=threshold,
            horizon=HORIZON,
        ),
    )
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(value)
    threshold["answer"].append(False)
    assert substrate.forecasts[forecast_id] is not value
    assert substrate.forecasts[forecast_id].resolution.threshold is not threshold
    assert substrate.forecasts[forecast_id].resolution.threshold == {"answer": [True]}


def test_amendment_is_digest_bound_monotone_and_does_not_rewrite_outcome(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    original = resolve_true(substrate)
    assert original.verdict is Verdict.HIT
    assert original.calibration_eligible is True
    raw_outcomes = substrate.outcomes_l.path.read_bytes()

    amendment = ExposureAmendment(
        forecast_id=forecast_id,
        target_forecast_digest=substrate.forecast_digest(forecast_id),
        reason_code="CAUSAL_EXPOSURE",
        amended_by="reviewer",
        amended_at=AMENDED,
    )
    substrate.amend_exposure(amendment)
    forecast_bytes = substrate.forecasts_l.path.read_bytes()
    substrate.amend_exposure(amendment)

    assert substrate.outcomes_l.path.read_bytes() == raw_outcomes
    assert substrate.forecasts_l.path.read_bytes() == forecast_bytes
    assert substrate.effective_exposure(forecast_id) is Exposure.EXPOSED
    assert substrate.outcomes[forecast_id].calibration_eligible is False
    assert substrate.outcomes[forecast_id].verdict is original.verdict
    assert len(substrate.amendments) == 1

    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.effective_exposure(forecast_id) is Exposure.EXPOSED
    assert reopened.outcomes[forecast_id].calibration_eligible is False
    assert len(reopened.amendments) == 1


def test_wrong_digest_is_rejected_before_append(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    before = substrate.forecasts_l.path.read_bytes()
    amendment = ExposureAmendment(
        forecast_id=forecast_id,
        target_forecast_digest=f"sha256:{'0' * 64}",
        reason_code="CAUSAL_EXPOSURE",
        amended_by="reviewer",
        amended_at=AMENDED,
    )
    with pytest.raises(ValueError, match="digest"):
        substrate.amend_exposure(amendment)
    assert substrate.forecasts_l.path.read_bytes() == before
    assert substrate.effective_exposure(forecast_id) is Exposure.BLIND


def test_amendment_race_loser_merges_the_durable_winner(tmp_path):
    first = Substrate(tmp_path, observables=specs())
    forecast_id = first.author(forecast())
    resolve_true(first)
    second = Substrate(tmp_path, observables=specs())
    amendment = ExposureAmendment(
        forecast_id=forecast_id,
        target_forecast_digest=first.forecast_digest(forecast_id),
        reason_code="CAUSAL_EXPOSURE",
        amended_by="reviewer",
        amended_at=AMENDED,
    )

    second.amend_exposure(amendment)
    durable = first.forecasts_l.path.read_bytes()
    first.amend_exposure(amendment)

    assert first.forecasts_l.path.read_bytes() == durable
    assert first.effective_exposure(forecast_id) is Exposure.EXPOSED
    assert first.outcomes[forecast_id].calibration_eligible is False
    assert first.amendments == [amendment]


def test_durable_invalid_amendment_quarantines_but_never_changes_raw_outcome(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    resolve_true(substrate)
    outcome_bytes = substrate.outcomes_l.path.read_bytes()
    substrate.forecasts_l.append_dict(
        {
            "_kind": "exposure_amendment",
            "_v": 2,
            "amended_at": AMENDED,
            "amended_by": "reviewer",
            "forecast_id": forecast_id,
            "from_exposure": "BLIND",
            "reason_code": "CAUSAL_EXPOSURE",
            "target_forecast_digest": f"sha256:{'0' * 64}",
            "to_exposure": "EXPOSED",
        }
    )

    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.effective_exposure(forecast_id) is Exposure.EXPOSED
    assert reopened.outcomes[forecast_id].calibration_eligible is False
    assert reopened.outcomes_l.path.read_bytes() == outcome_bytes
    assert reopened.forecasts_l.corrupted == 1


def test_v2_envelopes_are_explicit_and_invalid_versions_fail_closed(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    resolve_true(substrate)
    forecast_record = next(substrate.forecasts_l.raw())
    outcome_record = next(substrate.outcomes_l.raw())
    assert (forecast_record["_v"], forecast_record["_kind"]) == (2, "forecast")
    assert (outcome_record["_v"], outcome_record["_kind"]) == (2, "outcome")

    for version in (0, -1, True, "2", 2.0):
        substrate.forecasts_l.append_dict({"_v": version, "forecast_id": "bad"})
    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.forecasts_l.corrupted == 5
    assert forecast_id in reopened.forecasts


def test_future_version_still_fails_loud(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    substrate.forecasts_l.append_dict({"_kind": "forecast", "_v": 3})
    with pytest.raises(ValueError, match="schema v3"):
        Substrate(tmp_path, observables=specs())


def test_illegal_kinds_and_v2_fields_on_v1_fail_closed(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    substrate.forecasts_l.append_dict(
        {
            "_kind": "future_correction",
            "_v": 2,
            "forecast_id": forecast_id,
        }
    )
    substrate.forecasts_l.append_dict(
        {
            "_v": 1,
            "exposure_basis": {},
            "forecast_id": "f_disguised_v2",
        }
    )

    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.forecasts_l.corrupted == 2
    assert reopened.effective_exposure(forecast_id) is Exposure.EXPOSED
    assert "f_disguised_v2" not in reopened.forecasts


def test_legacy_replayable_reference_loads_as_identifiable(tmp_path):
    payload = json.loads(to_json(forecast(exposure=Exposure.EXPOSED, basis=None)))
    payload.pop("exposure_basis")
    payload.pop("world_commitment")
    payload["world_ref"] = "world:0123456789abcdef"
    payload["world_ref_capability"] = "REPLAYABLE"
    payload["_v"] = 1
    (tmp_path / "forecasts.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    reopened = Substrate(tmp_path, observables=specs())
    assert reopened.forecasts["f_v2"].world_ref_capability is WorldRefCapability.IDENTIFIABLE


def test_full_canonical_world_commitment_is_verified(tmp_path):
    commitment = canonical_world_commitment(
        {
            "_kind": "world_commitment",
            "_v": 1,
            "artifacts": [],
            "capability": "REPLAYABLE",
        }
    )
    value = Forecast(
        subject_ref="subject:fixture",
        claim="committed state",
        probability=0.5,
        exposure=Exposure.EXPOSED,
        world_ref=world_ref_for(commitment),
        world_ref_capability=WorldRefCapability.REPLAYABLE,
        world_commitment=commitment,
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=OBSERVABLE,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=HORIZON,
        ),
    )
    substrate = Substrate(tmp_path, observables=specs())
    substrate.author(value)

    bad = Forecast(
        subject_ref=value.subject_ref,
        claim=value.claim,
        probability=value.probability,
        exposure=Exposure.EXPOSED,
        world_ref=f"world:sha256:{'0' * 64}",
        world_ref_capability=WorldRefCapability.REPLAYABLE,
        world_commitment=commitment,
        forecast_id="f_bad_world",
        created_at=CREATED,
        resolution=value.resolution,
    )
    with pytest.raises(ValueError, match="does not match"):
        substrate.author(bad)


def test_new_manifestless_replayable_forecast_is_rejected(tmp_path):
    value = Forecast(
        subject_ref="subject:fixture",
        claim="unsupported replay claim",
        probability=0.5,
        exposure=Exposure.EXPOSED,
        world_ref="world:0123456789abcdef",
        world_ref_capability=WorldRefCapability.REPLAYABLE,
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=OBSERVABLE,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=HORIZON,
        ),
    )
    with pytest.raises(ValueError, match="requires a world commitment"):
        Substrate(tmp_path, observables=specs()).author(value)


def test_v2_seal_persists_subject_separately_from_epoch(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    forecast_id = substrate.author(forecast())
    seal = CompletenessSeal(
        source_ref=SOURCE,
        epoch_ref="run:fixture-1",
        subject_ref="subject:fixture",
        final_source_seq=0,
        sealed_at="2026-01-02T00:01:00+00:00",
    )
    assert substrate.capture(seal)
    record = next(substrate.evidence_l.raw())
    assert (record["_v"], record["_kind"]) == (2, "seal")
    assert record["subject_ref"] == "subject:fixture"

    outcome = substrate.process(at="2026-01-02T00:02:00+00:00")[0]
    assert outcome.forecast_id == forecast_id
    assert outcome.verdict is Verdict.MISS


def test_v1_seal_migrates_missing_subject_to_epoch(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    substrate.evidence_l.append_dict(
        {
            "_kind": "seal",
            "_v": 1,
            "complete": True,
            "epoch_ref": "legacy-subject",
            "final_source_seq": 0,
            "sealed_at": "2026-01-01T00:00:00+00:00",
            "source_ref": SOURCE,
        }
    )
    reopened = Substrate(tmp_path, observables=specs())
    assert reopened._seals[(SOURCE, "legacy-subject")].subject_ref == "legacy-subject"
    assert reopened._seal_versions[(SOURCE, "legacy-subject")] == 1


def test_v2_seal_without_subject_fails_closed(tmp_path):
    substrate = Substrate(tmp_path, observables=specs())
    substrate.evidence_l.append_dict(
        {
            "_kind": "seal",
            "_v": 2,
            "complete": True,
            "epoch_ref": "run:fixture-1",
            "final_source_seq": 0,
            "sealed_at": "2026-01-01T00:00:00+00:00",
            "source_ref": SOURCE,
        }
    )
    reopened = Substrate(tmp_path, observables=specs())
    assert reopened._seals == {}
    assert reopened.evidence_l.corrupted == 1
