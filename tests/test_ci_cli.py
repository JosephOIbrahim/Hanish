"""End-to-end standard-library CLI checks for Host 0."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from hanish.adapters.ci import CILegReport, Host0Plan, LegConclusion
from hanish.adapters.ci_cli import main
from hanish.adapters.ci_receipt import export_receipt, verify_receipt
from hanish.receipts import build_manifest, receipt_directory_name

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / ".github" / "host0-plan.json"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PROJECT = ROOT / "pyproject.toml"
SHA = "c" * 40
CREATED = "2026-08-19T20:00:00+00:00"
REPORTED = "2026-08-19T20:05:00+00:00"
AGGREGATED = "2026-08-19T20:10:00+00:00"


def identity_arguments():
    return [
        "--repository",
        "JosephOIbrahim/Hanish",
        "--workflow-ref",
        "JosephOIbrahim/Hanish/.github/workflows/ci.yml@refs/heads/main",
        "--run-id",
        "777",
        "--run-attempt",
        "1",
        "--tested-sha",
        SHA,
    ]


def author(tmp_path):
    output = tmp_path / "forecast"
    result = main(
        [
            "author-forecast",
            *identity_arguments(),
            "--plan",
            str(PLAN),
            "--workflow",
            str(WORKFLOW),
            "--project",
            str(PROJECT),
            "--created-at",
            CREATED,
            "--output",
            str(output),
        ]
    )
    assert result == 0
    return output


def write_report(tmp_path, leg_id, python_version, *, gate="success"):
    artifact = tmp_path / "reports" / f"artifact-{leg_id}"
    artifact.mkdir(parents=True)
    output = artifact / "leg-report.json"
    with (
        patch(
            "hanish.adapters.ci_cli._runtime_python_version",
            return_value=python_version,
        ),
        patch(
            "hanish.adapters.ci_cli._runtime_interpreter",
            return_value=f"{python_version}.0 (test runtime)",
        ),
    ):
        result = main(
            [
            "write-leg-report",
            *identity_arguments(),
            "--plan",
            str(PLAN),
            "--leg-id",
            leg_id,
            "--checkout-outcome",
            "success",
            "--setup-outcome",
            "success",
            "--install-outcome",
            "success",
            "--gate-outcome",
            gate,
            "--python-version",
            python_version,
            "--runner-os",
            "Linux",
            "--runner-image",
            "ubuntu24",
            "--runner-image-version",
            "20260817.1",
            "--created-at",
            REPORTED,
            "--output",
            str(output),
            ]
        )
    assert result == 0
    return output


def prepare_complete_run(tmp_path, *, failed_leg=None):
    forecast = author(tmp_path)
    plan = Host0Plan.load(PLAN)
    for leg in plan.ordered_legs:
        gate = "failure" if leg.leg_id == failed_leg else "success"
        write_report(tmp_path, leg.leg_id, leg.python_version, gate=gate)
    run = tmp_path / "run"
    result = main(
        [
            "aggregate",
            "--forecast-dir",
            str(forecast),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--plan",
            str(PLAN),
            "--aggregated-at",
            AGGREGATED,
            "--output",
            str(run),
        ]
    )
    assert result == 0
    return forecast, run


def receipt_bytes(receipt):
    return {
        path.relative_to(receipt).as_posix(): path.read_bytes()
        for path in receipt.rglob("*")
        if path.is_file()
    }


def remanifest(receipt, relative_path, payload):
    (receipt / relative_path).write_bytes(payload)
    (receipt / "manifest.json").unlink()
    manifest = build_manifest(receipt)
    renamed = receipt.parent / receipt_directory_name("777", 1, manifest.manifest_root)
    receipt.rename(renamed)
    return renamed


def test_authoring_precedes_evidence_and_is_structurally_exposed(tmp_path):
    forecast, run = prepare_complete_run(tmp_path)
    authored = json.loads((forecast / "state" / "forecasts.jsonl").read_text())
    evidence_text = (run / "state" / "evidence.jsonl").read_text()
    evidence = [json.loads(line) for line in evidence_text.splitlines()]
    observations = [record for record in evidence if record.get("_kind") == "observation"]
    assert authored["exposure"] == "EXPOSED"
    assert authored["created_at"] == CREATED
    assert authored["resolution"]["horizon"] > authored["created_at"]
    assert authored["world_ref"].startswith("world:sha256:")
    assert len(authored["world_ref"].removeprefix("world:sha256:")) == 64
    assert all(record["arrived_at"] == AGGREGATED for record in observations)
    assert CREATED < AGGREGATED


def test_cli_preserves_a_genuine_false_as_valid_complete_evidence(tmp_path):
    _, run = prepare_complete_run(tmp_path, failed_leg="python-3.13")
    aggregate = json.loads((run / "aggregation.json").read_text())
    outcomes_text = (run / "state" / "outcomes.jsonl").read_text()
    outcomes = [json.loads(line) for line in outcomes_text.splitlines()]
    assert aggregate["complete"] is True
    assert aggregate["capture_complete"] is True
    assert aggregate["required_checks_pass"] is False
    assert outcomes[-1]["observed"] is False
    assert outcomes[-1]["calibration_eligible"] is False


def test_report_cli_marks_prerequisite_failure_as_infrastructure(tmp_path):
    output = tmp_path / "report.json"
    with (
        patch("hanish.adapters.ci_cli._runtime_python_version", return_value="3.11"),
        patch(
            "hanish.adapters.ci_cli._runtime_interpreter",
            return_value="3.11.0 (test runtime)",
        ),
    ):
        result = main(
            [
            "write-leg-report",
            *identity_arguments(),
            "--plan",
            str(PLAN),
            "--leg-id",
            "python-3.11",
            "--checkout-outcome",
            "success",
            "--setup-outcome",
            "failure",
            "--install-outcome",
            "skipped",
            "--gate-outcome",
            "skipped",
            "--python-version",
            "3.11",
            "--created-at",
            REPORTED,
            "--output",
            str(output),
            ]
        )
    assert result == 0
    report = CILegReport.from_bytes(output.read_bytes())
    assert report.conclusion is LegConclusion.INFRASTRUCTURE_FAILURE
    assert report.evidence_valid is False


def test_report_cli_rejects_a_matrix_interpreter_mismatch(tmp_path):
    output = tmp_path / "report.json"
    with patch("hanish.adapters.ci_cli._runtime_python_version", return_value="3.12"):
        result = main(
            [
                "write-leg-report",
                *identity_arguments(),
                "--plan",
                str(PLAN),
                "--leg-id",
                "python-3.11",
                "--checkout-outcome",
                "success",
                "--setup-outcome",
                "success",
                "--install-outcome",
                "success",
                "--gate-outcome",
                "success",
                "--python-version",
                "3.11",
                "--created-at",
                REPORTED,
                "--output",
                str(output),
            ]
        )
    assert result == 1
    assert not output.exists()


def test_missing_leg_returns_incomplete_without_a_commit_aggregate(tmp_path):
    forecast = author(tmp_path)
    plan = Host0Plan.load(PLAN)
    for leg in plan.ordered_legs[:-1]:
        write_report(tmp_path, leg.leg_id, leg.python_version)
    run = tmp_path / "run"
    result = main(
        [
            "aggregate",
            "--forecast-dir",
            str(forecast),
            "--reports-dir",
            str(tmp_path / "reports"),
            "--plan",
            str(PLAN),
            "--aggregated-at",
            AGGREGATED,
            "--output",
            str(run),
        ]
    )
    assert result == 2
    aggregate = json.loads((run / "aggregation.json").read_text())
    assert aggregate["complete"] is False
    assert aggregate["required_checks_pass"] is None


def test_cli_reports_host0_input_failure_without_raising(tmp_path):
    arguments = identity_arguments()
    arguments[arguments.index("--tested-sha") + 1] = "not-a-git-object"
    result = main(
        [
            "author-forecast",
            *arguments,
            "--plan",
            str(PLAN),
            "--workflow",
            str(WORKFLOW),
            "--project",
            str(PROJECT),
            "--output",
            str(tmp_path / "forecast"),
        ]
    )
    assert result == 1


def test_receipt_export_and_semantic_replay_round_trip(tmp_path):
    _, run = prepare_complete_run(tmp_path)
    receipts = tmp_path / "receipts"
    assert main(
        [
            "export-receipt",
            "--run-dir",
            str(run),
            "--output-parent",
            str(receipts),
        ]
    ) == 0
    receipt = next(path for path in receipts.iterdir() if path.is_dir())
    assert len(receipt.name.rsplit("-", 1)[-1]) == 16
    assert main(["verify-receipt", "--receipt", str(receipt)]) == 0


def test_receipt_export_is_byte_stable_and_idempotent(tmp_path):
    _, run = prepare_complete_run(tmp_path)
    output = tmp_path / "receipts"
    first = export_receipt(run, output)
    before = receipt_bytes(first)
    second = export_receipt(run, output)
    assert second == first
    assert receipt_bytes(first) == before
    assert not any(path.name.startswith(".host0-receipt-") for path in output.iterdir())


@pytest.mark.parametrize("collision", ["malformed_directory", "regular_file"])
def test_preexisting_receipt_collision_is_never_mutated(tmp_path, collision):
    _, run = prepare_complete_run(tmp_path)
    source = export_receipt(run, tmp_path / "source")
    destination = tmp_path / "destination"
    destination.mkdir()
    existing = destination / source.name
    if collision == "malformed_directory":
        shutil.copytree(source, existing)
        (existing / "aggregation.json").write_bytes(b"malformed\n")
        before = receipt_bytes(existing)
    else:
        existing.write_bytes(b"reserved\n")
        before = existing.read_bytes()
    with pytest.raises((OSError, ValueError)):
        export_receipt(run, destination)
    if collision == "malformed_directory":
        assert receipt_bytes(existing) == before
    else:
        assert existing.read_bytes() == before


def test_receipt_directory_prefix_binds_run_and_attempt(tmp_path):
    _, run = prepare_complete_run(tmp_path)
    receipt = export_receipt(run, tmp_path / "receipts")
    wrong = receipt.with_name(f"other-9-{receipt.name.rsplit('-', 1)[-1]}")
    receipt.rename(wrong)
    with pytest.raises(ValueError, match="run identity"):
        verify_receipt(wrong)


def test_reproduction_record_is_exact_and_identity_bound(tmp_path):
    _, run = prepare_complete_run(tmp_path)
    receipt = export_receipt(run, tmp_path / "receipts")
    payload = json.loads((receipt / "reproduction.json").read_text())
    payload["command"] = "python -m pytest"
    receipt = remanifest(
        receipt,
        "reproduction.json",
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n",
    )
    with pytest.raises(ValueError, match="reproduction"):
        verify_receipt(receipt)


@pytest.mark.parametrize(
    ("relative_path", "mutation", "message"),
    [
        (
            "aggregation.json",
            lambda raw: raw.replace(b'"_v":1', b'"_v":1,"_v":1', 1),
            "duplicate key",
        ),
        (
            "aggregation.json",
            lambda raw: raw.replace(b'"capture_complete":true', b'"capture_complete":NaN'),
            "non-finite",
        ),
        ("aggregation.json", lambda raw: b" " + raw, "not canonical"),
        (
            "state/forecasts.jsonl",
            lambda raw: raw.replace(b"{", b'{"_kind": "forecast", ', 1),
            "duplicate key",
        ),
        ("state/forecasts.jsonl", lambda raw: b" " + raw, "ledger serializer"),
    ],
)
def test_receipt_rejects_ambiguous_or_noncanonical_signed_json(
    tmp_path,
    relative_path,
    mutation,
    message,
):
    _, run = prepare_complete_run(tmp_path)
    receipt = export_receipt(run, tmp_path / "receipts")
    receipt = remanifest(
        receipt,
        relative_path,
        mutation((receipt / relative_path).read_bytes()),
    )
    with pytest.raises(ValueError, match=message):
        verify_receipt(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verdict", "MISS"),
        ("brier", 0.75),
        ("observation_key", ["forged", "event"]),
        ("resolved_at", CREATED),
    ],
)
def test_receipt_independently_reconstructs_every_outcome_field(tmp_path, field, value):
    _, run = prepare_complete_run(tmp_path)
    receipt = export_receipt(run, tmp_path / "receipts")
    path = receipt / "state" / "outcomes.jsonl"
    outcome = json.loads(path.read_text())
    outcome[field] = value
    payload = json.dumps(outcome, allow_nan=False, sort_keys=True).encode() + b"\n"
    receipt = remanifest(receipt, "state/outcomes.jsonl", payload)
    with pytest.raises(ValueError, match="independent Host 0 reconstruction"):
        verify_receipt(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [("claim", "forged claim"), ("probability", 0.9), ("authored_by", "forged")],
)
def test_receipt_binds_the_exact_operational_forecast_contract(tmp_path, field, value):
    _, run = prepare_complete_run(tmp_path)
    receipt = export_receipt(run, tmp_path / "receipts")
    path = receipt / "state" / "forecasts.jsonl"
    forecast = json.loads(path.read_text())
    forecast[field] = value
    payload = json.dumps(forecast, allow_nan=False, sort_keys=True).encode() + b"\n"
    receipt = remanifest(receipt, "state/forecasts.jsonl", payload)
    with pytest.raises(ValueError, match="exact Host 0 contract"):
        verify_receipt(receipt)
