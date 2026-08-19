"""Static contract between the committed Host 0 plan and Actions workflow."""

from __future__ import annotations

import re
from pathlib import Path

from hanish.adapters.ci import ACTION_COMMITS, Host0Plan

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PLAN_PATH = ROOT / ".github" / "host0-plan.json"


def workflow():
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def job_block(text, name):
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)", text)
    assert match, f"missing workflow job {name}"
    return match.group(1)


def test_every_action_is_an_audited_immutable_commit():
    text = workflow()
    uses = re.findall(r"uses:\s+([^@\s]+)@([0-9a-f]+)", text)
    assert uses
    assert all(len(commit) == 40 for _, commit in uses)
    assert {name for name, _ in uses} == set(ACTION_COMMITS)
    for name, commit in uses:
        assert commit == ACTION_COMMITS[name]
    assert "actions/checkout@v" not in text
    assert "actions/setup-python@v" not in text
    assert "actions/upload-artifact@v" not in text
    assert "actions/download-artifact@v" not in text


def test_workflow_is_read_only_and_cannot_self_publish():
    text = workflow()
    assert re.search(r"(?m)^permissions:\n  contents: read$", text)
    assert "contents: write" not in text
    assert "pull_request_target" not in text
    assert "git push" not in text
    assert "gh release" not in text
    assert "persist-credentials: false" in text


def test_matrix_and_commands_match_the_independent_plan():
    text = workflow()
    plan = Host0Plan.load(PLAN_PATH)
    test_job = job_block(text, "test")
    package_job = job_block(text, "package")
    for leg in plan.ordered_legs:
        target = test_job if leg.kind == "test" else package_job
        assert f"leg-id: {leg.leg_id}" in target or f"--leg-id {leg.leg_id}" in target
        assert f'python-version: "{leg.python_version}"' in target
        assert f"slot: {leg.slot}" in target or leg.kind == "build"
        for command in leg.commands:
            assert command in target
    assert plan.aggregate_slot == len(plan.legs) + 1 == 6


def test_forecast_precedes_evidence_without_blocking_product_work():
    text = workflow()
    forecast = job_block(text, "forecast")
    test_job = job_block(text, "test")
    package = job_block(text, "package")
    assert "author-forecast" in forecast
    assert "exposure" not in forecast  # exposure is fixed by the CLI, not a caller label
    for block in (test_job, package):
        assert "needs: forecast" in block
        assert "if: ${{ always() }}" in block
        assert "write-leg-report" in block
        assert "continue-on-error: true" in block
        assert "Preserve product conclusion" in block


def test_artifacts_are_unique_to_leg_run_and_attempt():
    text = workflow()
    assert "host0-forecast-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert (
        "host0-leg-${{ matrix.leg-id }}-${{ github.run_id }}-"
        "${{ github.run_attempt }}"
    ) in text
    assert "host0-leg-package-build-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "host0-receipt-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert text.count("overwrite: false") >= 4
    assert text.count("include-hidden-files: true") == 4


def test_g9_and_fail_open_host0_are_structural_workflow_properties():
    text = workflow()
    package = job_block(text, "package")
    host0 = job_block(text, "host0")
    assert "Package build (G9)" in package
    assert "python -m build" in package
    assert "needs: [forecast, test, package]" in host0
    assert "if: ${{ always() }}" in host0
    assert "merge-multiple: false" in host0
    assert "digest-mismatch: error" in host0
    assert "aggregate" in host0
    assert "export-receipt" in host0
    assert "continue-on-error: true" in host0
    assert "No receipt may be promoted" in host0


def test_research_integrity_is_a_full_history_fail_closed_product_gate():
    block = job_block(workflow(), "research-integrity")
    assert "fetch-depth: 0" in block
    assert "check-research-integrity" in block
    assert "github.event.pull_request.base.sha" in block
    assert "github.event.before" in block
    assert "github.event.repository.default_branch" in block
    assert "github.sha" in block
    assert "continue-on-error" not in block
    assert "if: always()" not in block
