"""Host-specific assembly and semantic verification of Host 0 receipts."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from ..future.claims import (
    Adjudication,
    CausalMode,
    Comparator,
    EmissionSemantics,
    Exposure,
    ObservableSpec,
    WorldRefCapability,
    canonical_world_commitment,
    world_ref_for,
)
from ..past.events import Terminal, Validity, Verdict
from ..past.ledger import LEDGER_SCHEMA, to_json
from ..present.substrate import Substrate
from ..receipts import (
    build_manifest,
    canonical_json_bytes,
    receipt_directory_name,
    verify_manifest,
)
from .ci import (
    ACTION_COMMITS,
    REQUIRED_CHECKS_PASS,
    REQUIRED_LEG_PASS,
    CIAdapter,
    CILegReport,
    CIRunIdentity,
    Host0Plan,
    aggregate_reports,
    canonical_json,
    sha256_bytes,
)

_BASE_PAYLOADS = {
    "aggregation.json",
    "authoring.json",
    "execution-inventory.json",
    "exposure-amendments.jsonl",
    "identity.json",
    "observable-specs.json",
    "plan.json",
    "reproduction.json",
    "semantic-state.json",
    "state/evidence.jsonl",
    "state/forecasts.jsonl",
    "state/outcomes.jsonl",
    "world-commitment.json",
}

_OUTCOME_ID = re.compile(r"o_[0-9a-f]{12}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORECAST_CLAIM = "the required Host 0 checks for this tested checkout pass"
_FORECAST_ASSUMPTIONS = ("operational forecast; never calibration eligible",)
_REPRODUCTION_COMMAND = "python -m hanish.adapters.ci_cli verify-receipt --receipt ."
_DEPENDENCY_RESOLUTION = (
    "reporter environments captured per leg; isolated build environment "
    "and mutable runner image keep the world IDENTIFIABLE"
)


def _strict_json(payload: bytes, path: Path) -> object:
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid JSON payload {path}") from exc

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON payload {path} contains duplicate key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON payload {path} contains non-finite number {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON payload {path}") from exc


def _load_object(path: Path) -> dict:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"invalid JSON payload {path}") from exc
    value = _strict_json(payload, path)
    if not isinstance(value, dict):
        raise ValueError(f"payload {path} must be an object")
    if payload != canonical_json_bytes(value) + b"\n":
        raise ValueError(f"JSON payload is not canonical newline-terminated JSON: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read JSONL payload {path}") from exc
    if payload and not payload.endswith(b"\n"):
        raise ValueError(f"JSONL payload has a torn tail: {path}")
    records = []
    lines = payload[:-1].split(b"\n") if payload else []
    for line in lines:
        if not line.strip():
            raise ValueError(f"JSONL payload contains a blank record: {path}")
        value = _strict_json(line, path)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL record is not an object: {path}")
        # Runtime ledgers deliberately use the core's readable serializer;
        # receipts bind that exact representation without changing the core.
        expected = json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")
        if line != expected:
            raise ValueError(f"JSONL record does not use the ledger serializer: {path}")
        records.append(value)
    return records


def _json_model(value: object) -> object:
    return json.loads(to_json(value))


def _observable_specs(path: Path, expected_source: str) -> dict[str, ObservableSpec]:
    payload = _load_object(path)
    if set(payload) != {"_kind", "_v", "source_ref", "specs"}:
        raise ValueError("observable declaration fields differ from the Host 0 contract")
    if payload.get("_kind") != "observable_declarations" or payload.get("_v") != 1:
        raise ValueError("unsupported observable declaration payload")
    if payload.get("source_ref") != expected_source:
        raise ValueError("observable declaration source differs from the adapter")
    raw_specs = payload.get("specs")
    if not isinstance(raw_specs, list):
        raise ValueError("observable declarations must contain a specs array")
    specs: dict[str, ObservableSpec] = {}
    for value in raw_specs:
        if not isinstance(value, dict) or set(value) != {
            "emission",
            "name",
            "sources",
            "value_type",
        }:
            raise ValueError("malformed observable declaration")
        sources = value["sources"]
        if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources):
            raise ValueError("observable sources must be strings")
        spec = ObservableSpec(
            name=value["name"],
            value_type=value["value_type"],
            emission=EmissionSemantics(value["emission"]),
            sources=tuple(sources),
        )
        if spec.name in specs:
            raise ValueError("duplicate observable declaration")
        specs[spec.name] = spec
    expected_payload = {
        "_kind": "observable_declarations",
        "_v": 1,
        "source_ref": expected_source,
        "specs": [
            {
                "emission": spec.emission.value,
                "name": spec.name,
                "sources": list(spec.sources),
                "value_type": spec.value_type,
            }
            for spec in sorted(specs.values(), key=lambda item: item.name)
        ],
    }
    if payload != expected_payload:
        raise ValueError("observable declarations are not in canonical Host 0 order")
    return specs


def _semantic_state(
    root: Path,
    specs: dict[str, ObservableSpec],
    at: str,
) -> tuple[dict, Substrate]:
    with tempfile.TemporaryDirectory(prefix="hanish-host0-replay-") as temporary:
        copied = Path(temporary) / "state"
        shutil.copytree(root, copied)
        substrate = Substrate(copied, observables=specs)
        damaged = sum(
            ledger.tail_loss + ledger.corrupted
            for ledger in (substrate.forecasts_l, substrate.evidence_l, substrate.outcomes_l)
        )
        if damaged:
            raise ValueError("receipt replay encountered damaged ledger records")
        produced = substrate.process(at=at)
        if produced:
            raise ValueError("receipt omitted outcomes that semantic replay had to append")
        if substrate.process_errors:
            raise ValueError("receipt semantic replay encountered a processing error")
        state = {
            "forecasts": [
                _json_model(substrate.forecasts[key]) for key in sorted(substrate.forecasts)
            ],
            "observations": [_json_model(event) for event in substrate._observations],
            "outcomes": [
                _json_model(substrate.outcomes[key]) for key in sorted(substrate.outcomes)
            ],
            "seals": sorted(
                (_json_model(seal) for seal in substrate._seals.values()),
                key=canonical_json,
            ),
        }
        # Return a detached reconstruction for semantic checks below. Its
        # dataclasses remain valid after the temporary ledger disappears.
        return state, substrate


def _report_files(root: Path, plan: Host0Plan) -> list[Path]:
    expected = [root / "reports" / f"{leg.leg_id}.json" for leg in plan.ordered_legs]
    actual = sorted((root / "reports").glob("*.json")) if (root / "reports").is_dir() else []
    if {path.name for path in actual} != {path.name for path in expected}:
        raise ValueError("receipt reports do not exactly match the Host 0 plan")
    return expected


def _verify_payload_semantics(root: Path) -> tuple[CIRunIdentity, Host0Plan, dict]:
    identity = CIRunIdentity.from_dict(_load_object(root / "identity.json"))
    plan_bytes = (root / "plan.json").read_bytes()
    plan = Host0Plan.from_bytes(plan_bytes)
    plan_digest = Host0Plan.digest(plan_bytes)
    aggregation_payload = _load_object(root / "aggregation.json")
    if (
        aggregation_payload.get("_kind") != "host0_aggregation"
        or type(aggregation_payload.get("_v")) is not int
        or aggregation_payload["_v"] != 1
    ):
        raise ValueError("unsupported Host 0 aggregation record")
    if aggregation_payload.get("complete") is not True:
        raise ValueError("receipt aggregation is not complete")
    if aggregation_payload.get("capture_complete") is not True:
        raise ValueError("receipt capture is not complete")
    if aggregation_payload.get("identity") != identity.to_dict():
        raise ValueError("aggregation identity mismatch")
    if aggregation_payload.get("plan_digest") != plan_digest:
        raise ValueError("aggregation plan digest mismatch")
    if aggregation_payload.get("subject_ref") != identity.subject_ref:
        raise ValueError("aggregation subject mismatch")
    if aggregation_payload.get("epoch_ref") != identity.epoch_ref:
        raise ValueError("aggregation epoch mismatch")

    reports = [CILegReport.from_dict(_load_object(path)) for path in _report_files(root, plan)]
    adapter = CIAdapter.for_repository(identity.repository)
    aggregation = aggregate_reports(
        adapter,
        identity,
        plan,
        plan_digest,
        reports,
        aggregated_at=aggregation_payload["aggregated_at"],
    )
    if not aggregation.complete or aggregation.issues:
        raise ValueError("receipt reports do not reconstruct a complete aggregate")
    expected_aggregation = aggregation.to_dict()
    expected_aggregation.update(
        {
            "capture_complete": True,
            "epoch_ref": identity.epoch_ref,
            "subject_ref": identity.subject_ref,
        }
    )
    if aggregation_payload != expected_aggregation:
        raise ValueError("aggregation record differs from its reconstructed value")

    declarations = _observable_specs(root / "observable-specs.json", adapter.source_ref)
    if declarations != adapter.observable_specs():
        raise ValueError("observable declarations do not match the adapter source")
    forecast_records = _load_jsonl(root / "state" / "forecasts.jsonl")
    evidence_records = _load_jsonl(root / "state" / "evidence.jsonl")
    outcome_records = _load_jsonl(root / "state" / "outcomes.jsonl")
    if len(forecast_records) != 1 or forecast_records[0].get("_kind") != "forecast":
        raise ValueError("Host 0 receipt must contain exactly one forecast record")
    if len(outcome_records) != 1 or outcome_records[0].get("_kind") != "outcome":
        raise ValueError("Host 0 receipt must contain exactly one outcome record")
    if len(evidence_records) != plan.aggregate_slot + 1:
        raise ValueError("Host 0 evidence ledger has an unexpected record count")
    kinds = [record.get("_kind") for record in evidence_records]
    if kinds.count("observation") != plan.aggregate_slot or kinds.count("seal") != 1:
        raise ValueError("Host 0 evidence ledger has unexpected record kinds")

    state, substrate = _semantic_state(
        root / "state",
        declarations,
        aggregation.aggregated_at,
    )
    if len(substrate.forecasts) != 1:
        raise ValueError("Host 0 receipt must contain one operational forecast")
    forecast = next(iter(substrate.forecasts.values()))
    expected_forecast_id = "f_host0_" + sha256_bytes(
        canonical_json(
            {"identity": identity.to_dict(), "plan_digest": plan_digest}
        ).encode("utf-8")
    )[:12]
    horizon = (datetime.fromisoformat(forecast.created_at) + timedelta(hours=24)).isoformat()
    resolution = forecast.resolution
    if (
        forecast.forecast_id != expected_forecast_id
        or forecast.subject_ref != identity.subject_ref
        or forecast.claim != _FORECAST_CLAIM
        or type(forecast.probability) is not float
        or forecast.probability != 0.5
        or forecast.exposure is not Exposure.EXPOSED
        or forecast.exposure_basis is not None
        or forecast.authored_by != "github-actions-host0"
        or forecast.assumptions != _FORECAST_ASSUMPTIONS
        or resolution.observable != REQUIRED_CHECKS_PASS
        or resolution.comparator is not Comparator.EQ
        or resolution.threshold is not True
        or resolution.horizon != horizon
        or resolution.adjudication is not Adjudication.FIRST_VALID_TERMINAL
        or tuple(resolution.accept_validity) != (Validity.VALID,)
        or resolution.causal_mode is not CausalMode.OBSERVATIONAL
    ):
        raise ValueError("operational forecast differs from the exact Host 0 contract")
    commitment_bytes = (root / "world-commitment.json").read_bytes()
    commitment_payload = _strict_json(commitment_bytes, root / "world-commitment.json")
    if not isinstance(commitment_payload, dict):
        raise ValueError("world commitment must be an object")
    commitment = commitment_bytes.decode("utf-8")
    if canonical_world_commitment(commitment_payload) != commitment:
        raise ValueError("world commitment is not canonical")
    artifacts = commitment_payload.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or len(artifacts) != 3
        or any(
            not isinstance(item, dict)
            or set(item) != {"locator", "path", "sha256", "size"}
            for item in artifacts
        )
    ):
        raise ValueError("world commitment artifacts are malformed")
    expected_paths = [".github/host0-plan.json", plan.workflow_path, "pyproject.toml"]
    if [item["path"] for item in artifacts] != expected_paths:
        raise ValueError("world commitment artifact paths are not the Host 0 inputs")
    for item in artifacts:
        if (
            item["locator"] != f"git:{identity.tested_sha}:{item['path']}"
            or not isinstance(item["sha256"], str)
            or not _SHA256.fullmatch(item["sha256"])
            or type(item["size"]) is not int
            or item["size"] < 1
        ):
            raise ValueError("world commitment artifact binding is malformed")
    if artifacts[0]["sha256"] != plan_digest or artifacts[0]["size"] != len(plan_bytes):
        raise ValueError("world commitment does not bind the Host 0 plan bytes")
    expected_commitment = {
        "_kind": "world_commitment",
        "_v": 1,
        "adapter_schema": 1,
        "artifacts": artifacts,
        "capability": WorldRefCapability.IDENTIFIABLE.value,
        "created_at": forecast.created_at,
        "intended_environment": {
            "python_versions": [leg.python_version for leg in plan.ordered_legs],
            "runner": "ubuntu-latest",
        },
        "intended_legs": [
            {"commands": list(leg.commands), "id": leg.leg_id, "slot": leg.slot}
            for leg in plan.ordered_legs
        ],
        "ledger_schema": LEDGER_SCHEMA,
        "plan_id": plan.plan_id,
        "repository": identity.repository,
        "tested_sha": identity.tested_sha,
        "workflow_ref": identity.workflow_ref,
    }
    if commitment_payload != expected_commitment:
        raise ValueError("world commitment differs from the exact Host 0 contract")
    if forecast.world_ref != world_ref_for(commitment):
        raise ValueError("world commitment digest mismatch")
    if forecast.world_ref_capability is not WorldRefCapability.IDENTIFIABLE:
        raise ValueError("first Host 0 world must remain IDENTIFIABLE")
    if getattr(forecast, "world_commitment", None) != commitment:
        raise ValueError("forecast does not bind the world commitment")
    authoring = _load_object(root / "authoring.json")
    if authoring != {
        "_kind": "host0_authoring",
        "_v": 1,
        "created_at": forecast.created_at,
        "forecast_id": forecast.forecast_id,
        "plan_digest": plan_digest,
        "world_ref": forecast.world_ref,
    }:
        raise ValueError("authoring metadata does not bind the operational forecast")

    observations = substrate._observations
    if len(observations) != plan.aggregate_slot:
        raise ValueError("receipt does not contain exactly one event per plan slot")
    expected_events = aggregation.events
    if [_json_model(item) for item in observations] != [
        _json_model(item) for item in expected_events
    ]:
        raise ValueError("evidence ledger does not replay to the declared aggregate")
    if [event.source_seq for event in observations] != list(range(1, plan.aggregate_slot + 1)):
        raise ValueError("evidence sequence is not complete and ordered")
    if any(
        event.source_ref != identity.source_ref
        or event.subject_ref != identity.subject_ref
        or event.epoch_ref != identity.epoch_ref
        for event in observations
    ):
        raise ValueError("evidence coordinates differ from the run identity")
    if [event.observable for event in observations[:-1]] != [
        REQUIRED_LEG_PASS
    ] * len(plan.legs):
        raise ValueError("a leg emitted the commit-level observable")
    if observations[-1].observable != REQUIRED_CHECKS_PASS:
        raise ValueError("slot 6 is not the commit-level aggregate")

    seals = list(substrate._seals.values())
    if len(seals) != 1:
        raise ValueError("Host 0 receipt must contain exactly one seal")
    seal = seals[0]
    if not hasattr(seal, "subject_ref"):
        raise ValueError("core seal schema does not expose subject_ref")
    if (
        seal.source_ref != identity.source_ref
        or seal.subject_ref != identity.subject_ref
        or seal.epoch_ref != identity.epoch_ref
        or seal.final_source_seq != plan.aggregate_slot
        or seal.complete is not True
        or seal.sealed_at != aggregation.aggregated_at
    ):
        raise ValueError("completeness seal coordinates are invalid")

    if len(substrate.outcomes) != 1:
        raise ValueError("Host 0 receipt must contain one outcome")
    outcome = next(iter(substrate.outcomes.values()))
    aggregate_observation = observations[-1]
    expected_verdict = (
        Verdict.HIT if aggregation.required_checks_pass is True else Verdict.MISS
    )
    resolved_at = datetime.fromisoformat(outcome.resolved_at)
    created_at = datetime.fromisoformat(forecast.created_at)
    observed_at = datetime.fromisoformat(aggregate_observation.arrived_at)
    horizon_at = datetime.fromisoformat(forecast.resolution.horizon)
    if (
        outcome.forecast_id != forecast.forecast_id
        or outcome.terminal is not Terminal.RESOLVED
        or outcome.verdict is not expected_verdict
        or outcome.observation_key != aggregate_observation.dedup_key
        or type(outcome.predicted) is not float
        or outcome.predicted != 0.5
        or outcome.observed is not aggregation.required_checks_pass
        or type(outcome.brier) is not float
        or outcome.brier != 0.25
        or outcome.reason != "first valid terminal observation"
        or outcome.calibration_eligible is not False
        or not _OUTCOME_ID.fullmatch(outcome.outcome_id)
        or not created_at < observed_at <= horizon_at
        or resolved_at < observed_at
    ):
        raise ValueError("outcome differs from independent Host 0 reconstruction")

    inventory = _load_object(root / "execution-inventory.json")
    expected_inventory_legs = [
        {
            "conclusion": report.conclusion.value,
            "created_at": report.created_at,
            "dependency_capture_complete": report.dependency_capture_complete,
            "executed_commands": list(report.executed_commands),
            "id": report.leg_id,
            "interpreter": {
                "implementation": report.implementation,
                "version": report.interpreter,
            },
            "python_version": report.python_version,
            "resolved_distributions": [
                {"name": name, "version": version}
                for name, version in report.distributions
            ],
            "runner": {
                "image": report.runner_image,
                "image_version": report.runner_image_version,
                "os": report.runner_os,
            },
            "slot": report.slot,
        }
        for report in aggregation.reports
    ]
    expected_inventory = {
        "_kind": "host0_execution_inventory",
        "_v": 1,
        "action_commits": dict(sorted(ACTION_COMMITS.items())),
        "aggregated_at": aggregation.aggregated_at,
        "dependency_resolution": _DEPENDENCY_RESOLUTION,
        "identity": identity.to_dict(),
        "legs": expected_inventory_legs,
        "plan_digest": plan_digest,
    }
    if inventory != expected_inventory:
        raise ValueError("execution inventory differs from reconstructed execution")
    return identity, plan, state


def _copy_new(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def export_receipt(run_directory: Path, output_parent: Path) -> Path:
    identity, plan, state = _verify_payload_semantics(run_directory)
    output_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".host0-receipt-", dir=output_parent))
    final: Path | None = None
    promoted = False
    try:
        for name in (
            "aggregation.json",
            "authoring.json",
            "execution-inventory.json",
            "identity.json",
            "observable-specs.json",
            "plan.json",
            "world-commitment.json",
        ):
            _copy_new(run_directory / name, staging / name)
        for name in ("forecasts.jsonl", "evidence.jsonl", "outcomes.jsonl"):
            _copy_new(run_directory / "state" / name, staging / "state" / name)
        for path in _report_files(run_directory, plan):
            _copy_new(path, staging / "reports" / path.name)
        _write_new(staging / "exposure-amendments.jsonl", b"")
        semantic_digest = sha256_bytes(canonical_json_bytes(state))
        _write_new(
            staging / "semantic-state.json",
            canonical_json_bytes(
                {
                    "_kind": "host0_semantic_state",
                    "_v": 1,
                    "sha256": semantic_digest,
                    "state": state,
                }
            )
            + b"\n",
        )
        _write_new(
            staging / "reproduction.json",
            canonical_json_bytes(
                {
                    "_kind": "host0_reproduction",
                    "_v": 1,
                    "command": (
                        "python -m hanish.adapters.ci_cli verify-receipt --receipt ."
                    ),
                    "tested_sha": identity.tested_sha,
                }
            )
            + b"\n",
        )
        manifest = build_manifest(staging)
        final = output_parent / receipt_directory_name(
            str(identity.run_id),
            identity.run_attempt,
            manifest.manifest_root,
        )
        if os.path.lexists(final):
            verify_receipt(final)
            existing_manifest = verify_manifest(final)
            existing_identity = CIRunIdentity.from_dict(_load_object(final / "identity.json"))
            if existing_manifest != manifest or existing_identity != identity:
                raise FileExistsError(f"receipt identity collision: {final}")
            shutil.rmtree(staging)
            return final
        staging.rename(final)
        promoted = True
        verify_receipt(final)
        return final
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if promoted and final is not None and final.exists():
            shutil.rmtree(final)
        raise


def verify_receipt(receipt: Path) -> None:
    manifest = verify_manifest(receipt)
    identity = CIRunIdentity.from_dict(_load_object(receipt / "identity.json"))
    expected_name = receipt_directory_name(
        str(identity.run_id),
        identity.run_attempt,
        manifest.manifest_root,
    )
    if receipt.name != expected_name:
        raise ValueError("receipt directory name does not bind the run identity")
    plan = Host0Plan.from_bytes((receipt / "plan.json").read_bytes())
    expected = set(_BASE_PAYLOADS)
    expected.update(f"reports/{leg.leg_id}.json" for leg in plan.ordered_legs)
    actual = {entry.path for entry in manifest.entries}
    if actual != expected:
        raise ValueError("receipt payload membership differs from the Host 0 contract")
    if (receipt / "exposure-amendments.jsonl").read_bytes() != b"":
        raise ValueError("operational Host 0 receipt unexpectedly contains amendments")
    replay_identity, _, state = _verify_payload_semantics(receipt)
    if replay_identity != identity:
        raise ValueError("receipt identity changed during replay")
    semantic = _load_object(receipt / "semantic-state.json")
    digest = sha256_bytes(canonical_json_bytes(state))
    if semantic != {
        "_kind": "host0_semantic_state",
        "_v": 1,
        "sha256": digest,
        "state": state,
    }:
        raise ValueError("semantic replay digest mismatch")
    reproduction = _load_object(receipt / "reproduction.json")
    if reproduction != {
        "_kind": "host0_reproduction",
        "_v": 1,
        "command": _REPRODUCTION_COMMAND,
        "tested_sha": identity.tested_sha,
    }:
        raise ValueError("reproduction record does not bind the receipt identity")
