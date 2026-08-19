"""Host 0 identity, aggregation, and completeness contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hanish.adapters.ci import (
    REQUIRED_CHECKS_PASS,
    REQUIRED_LEG_PASS,
    CIAdapter,
    CILegReport,
    CIRunIdentity,
    Host0Plan,
    aggregate_reports,
    canonical_json,
    classify_leg_outcome,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / ".github" / "host0-plan.json"
CREATED = "2026-08-19T20:00:00+00:00"
AGGREGATED = "2026-08-19T20:10:00+00:00"


@pytest.fixture
def plan_and_digest():
    payload = PLAN_PATH.read_bytes()
    return Host0Plan.from_bytes(payload), Host0Plan.digest(payload)


@pytest.fixture
def identity():
    return CIRunIdentity(
        repository="JosephOIbrahim/Hanish",
        workflow_ref="JosephOIbrahim/Hanish/.github/workflows/ci.yml@refs/heads/main",
        run_id=123456,
        run_attempt=2,
        tested_sha="a" * 40,
    )


def report(identity, plan, digest, leg_id, *, gate="success", setup="success"):
    leg = plan.leg(leg_id)
    conclusion = classify_leg_outcome("success", setup, "success", gate)
    return CILegReport(
        identity=identity,
        plan_digest=digest,
        leg_id=leg.leg_id,
        slot=leg.slot,
        conclusion=conclusion,
        checkout_outcome="success",
        setup_outcome=setup,
        install_outcome="success",
        gate_outcome=gate,
        python_version=leg.python_version,
        interpreter=f"{leg.python_version}.0 (main, Aug 1 2026)",
        implementation="cpython",
        executed_commands=leg.commands if conclusion.evidence_valid else (),
        distributions=(("hanish", "0.2.0"), ("pytest", "8.4.1")),
        dependency_capture_complete=True,
        runner_os="Linux",
        runner_image="ubuntu24",
        runner_image_version="20260817.1",
        created_at=CREATED,
    )


def all_reports(identity, plan, digest):
    return [report(identity, plan, digest, leg.leg_id) for leg in plan.ordered_legs]


def test_plan_is_the_independent_six_slot_authority(plan_and_digest):
    plan, digest = plan_and_digest
    assert [leg.slot for leg in plan.ordered_legs] == [1, 2, 3, 4, 5]
    assert [leg.leg_id for leg in plan.ordered_legs] == [
        "python-3.11",
        "python-3.12",
        "python-3.13",
        "python-3.14",
        "package-build",
    ]
    assert plan.aggregate_id == "aggregate-required-checks"
    assert plan.aggregate_slot == 6
    assert len(digest) == 64


def test_run_identity_is_repository_scoped_and_restart_stable(identity):
    same = CIRunIdentity.from_dict(identity.to_dict())
    other = replace(identity, repository="other/Hanish")
    retry = replace(identity, run_attempt=3)
    assert identity.source_ref == "github-actions:josephoibrahim/hanish"
    assert same.epoch_ref == identity.epoch_ref
    assert other.source_ref != identity.source_ref
    assert other.epoch_ref != identity.epoch_ref
    assert retry.epoch_ref != identity.epoch_ref
    assert identity.subject_ref == f"git:{'a' * 40}"


def test_all_pass_is_order_independent_and_emits_exact_slots(identity, plan_and_digest):
    plan, digest = plan_and_digest
    adapter = CIAdapter.for_repository(identity.repository)
    reports = list(reversed(all_reports(identity, plan, digest)))
    result = aggregate_reports(
        adapter,
        identity,
        plan,
        digest,
        reports,
        aggregated_at=AGGREGATED,
    )
    assert result.complete is True
    assert result.required_checks_pass is True
    assert result.issues == ()
    assert [event.source_seq for event in result.events] == [1, 2, 3, 4, 5, 6]
    assert [event.observable for event in result.events[:-1]] == [REQUIRED_LEG_PASS] * 5
    assert result.events[-1].observable == REQUIRED_CHECKS_PASS
    assert result.events[-1].value is True
    assert all(event.epoch_ref == identity.epoch_ref for event in result.events)
    assert all(event.subject_ref == identity.subject_ref for event in result.events)

    seal = adapter.finalize_run(identity, plan, complete=True, sealed_at=AGGREGATED)
    assert seal.subject_ref == identity.subject_ref
    assert seal.epoch_ref == identity.epoch_ref
    assert seal.final_source_seq == 6


def test_genuine_product_failure_is_complete_false_evidence(identity, plan_and_digest):
    plan, digest = plan_and_digest
    reports = all_reports(identity, plan, digest)
    reports[2] = report(identity, plan, digest, "python-3.13", gate="failure")
    result = aggregate_reports(
        CIAdapter.for_repository(identity.repository),
        identity,
        plan,
        digest,
        reports,
        aggregated_at=AGGREGATED,
    )
    assert result.complete is True
    assert result.required_checks_pass is False
    assert result.events[2].value is False
    assert result.events[-1].value is False


@pytest.mark.parametrize("mode", ["missing", "duplicate", "infrastructure", "wrong_sha"])
def test_untrustworthy_membership_never_emits_an_aggregate(
    identity,
    plan_and_digest,
    mode,
):
    plan, digest = plan_and_digest
    reports = all_reports(identity, plan, digest)
    if mode == "missing":
        reports.pop()
    elif mode == "duplicate":
        reports.append(reports[0])
    elif mode == "infrastructure":
        reports[0] = report(identity, plan, digest, "python-3.11", setup="failure")
    else:
        reports[0] = replace(
            reports[0],
            identity=replace(identity, tested_sha="b" * 40),
        )
    result = aggregate_reports(
        CIAdapter.for_repository(identity.repository),
        identity,
        plan,
        digest,
        reports,
        aggregated_at=AGGREGATED,
    )
    assert result.complete is False
    assert result.required_checks_pass is None
    assert all(event.observable != REQUIRED_CHECKS_PASS for event in result.events)
    seal = CIAdapter.for_repository(identity.repository).finalize_run(
        identity,
        plan,
        complete=result.complete,
        sealed_at=AGGREGATED,
    )
    assert seal.complete is False


def test_unknown_leg_and_external_parse_failure_are_inward_failures(identity, plan_and_digest):
    plan, digest = plan_and_digest
    reports = all_reports(identity, plan, digest)
    reports.append(replace(reports[0], leg_id="surprise"))
    result = aggregate_reports(
        CIAdapter.for_repository(identity.repository),
        identity,
        plan,
        digest,
        reports,
        aggregated_at=AGGREGATED,
        external_issues=("malformed_report:x/leg-report.json",),
    )
    assert result.complete is False
    assert "unknown_leg:surprise" in result.issues
    assert "malformed_report:x/leg-report.json" in result.issues


def test_report_authored_after_aggregation_is_not_evidence(identity, plan_and_digest):
    plan, digest = plan_and_digest
    reports = all_reports(identity, plan, digest)
    reports[0] = replace(reports[0], created_at="2026-08-19T20:11:00+00:00")
    result = aggregate_reports(
        CIAdapter.for_repository(identity.repository),
        identity,
        plan,
        digest,
        reports,
        aggregated_at=AGGREGATED,
    )
    assert result.complete is False
    assert "future_report:python-3.11" in result.issues
    assert all(event.observable != REQUIRED_CHECKS_PASS for event in result.events)


def test_incomplete_dependency_capture_cannot_seal_a_receipt(identity, plan_and_digest):
    plan, digest = plan_and_digest
    reports = all_reports(identity, plan, digest)
    reports[0] = replace(reports[0], dependency_capture_complete=False)
    result = aggregate_reports(
        CIAdapter.for_repository(identity.repository),
        identity,
        plan,
        digest,
        reports,
        aggregated_at=AGGREGATED,
    )
    assert result.complete is False
    assert "dependency_capture_incomplete:python-3.11" in result.issues
    assert all(event.observable != REQUIRED_CHECKS_PASS for event in result.events)


def test_strict_values_and_integer_identities_reject_bool(identity, plan_and_digest):
    plan, digest = plan_and_digest
    adapter = CIAdapter.for_repository(identity.repository)
    with pytest.raises(ValueError, match="strict bool"):
        adapter.aggregate_event(identity, plan, digest, 1, arrived_at=AGGREGATED)
    with pytest.raises(ValueError, match="positive integer"):
        replace(identity, run_attempt=True)
    with pytest.raises(ValueError, match="positive integer"):
        replace(plan.legs[0], slot=True)


def test_report_rejects_a_declared_version_that_differs_from_its_interpreter(
    identity,
    plan_and_digest,
):
    plan, digest = plan_and_digest
    with pytest.raises(ValueError, match="does not match"):
        replace(
            report(identity, plan, digest, "python-3.11"),
            interpreter="3.12.0 (main)",
        )


def test_two_repositories_cannot_collide_on_the_same_run_event(identity, plan_and_digest):
    plan, digest = plan_and_digest
    other = replace(identity, repository="Other/Hanish")
    first = aggregate_reports(
        CIAdapter.for_repository(identity.repository),
        identity,
        plan,
        digest,
        all_reports(identity, plan, digest),
        aggregated_at=AGGREGATED,
    ).events[-1]
    second = aggregate_reports(
        CIAdapter.for_repository(other.repository),
        other,
        plan,
        digest,
        all_reports(other, plan, digest),
        aggregated_at=AGGREGATED,
    ).events[-1]
    assert first.event_id == second.event_id
    assert first.dedup_key != second.dedup_key


def test_plan_and_report_parsers_reject_ambiguous_json(identity, plan_and_digest):
    plan, digest = plan_and_digest
    plan_bytes = PLAN_PATH.read_bytes().replace(
        b'"_v": 1',
        b'"_v": 1, "_v": 1',
        1,
    )
    with pytest.raises(ValueError, match="duplicate key"):
        Host0Plan.from_bytes(plan_bytes)

    report_bytes = canonical_json(
        report(identity, plan, digest, "python-3.11").to_dict()
    ).encode()
    with pytest.raises(ValueError, match="duplicate key"):
        CILegReport.from_bytes(
            report_bytes.replace(b'"_v":1', b'"_v":1,"_v":1', 1)
        )
    with pytest.raises(ValueError, match="non-finite"):
        CILegReport.from_bytes(report_bytes.replace(b'"_v":1', b'"_v":NaN', 1))
