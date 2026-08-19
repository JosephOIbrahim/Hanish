"""Standard-library command line for the GitHub Actions Host 0 slice."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path

from ..future.claims import (
    Comparator,
    Exposure,
    Forecast,
    ResolutionSpec,
    WorldRefCapability,
    canonical_world_commitment,
    world_ref_for,
)
from ..past.ledger import LEDGER_SCHEMA
from ..present.substrate import Substrate
from ..receipts import (
    PathChange,
    load_exclusions,
    validate_exclusion_prefix,
    validate_receipt_additions,
)
from ..time import now
from .ci import (
    ACTION_COMMITS,
    REQUIRED_CHECKS_PASS,
    CIAdapter,
    CIAggregation,
    CILegReport,
    CIRunIdentity,
    Host0Plan,
    aggregate_reports,
    canonical_json,
    classify_leg_outcome,
    sha256_bytes,
)


class IncompleteAggregation(RuntimeError):
    pass


_EXCLUSIONS_PATH = "experiments/calibration-exclusions.jsonl"
_RECEIPTS_ROOT = "experiments/receipts"
_FULL_GIT_OID = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_DEFAULT_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _write_json(path: Path, value: object) -> None:
    _write_new(path, canonical_json(value).encode("utf-8") + b"\n")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _identity(args: argparse.Namespace) -> CIRunIdentity:
    return CIRunIdentity(
        repository=args.repository,
        workflow_ref=args.workflow_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        tested_sha=args.tested_sha,
    )


def _timestamp(value: str | None) -> str:
    result = value or now()
    try:
        parsed = datetime.fromisoformat(result)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _artifact(path: Path, repository_path: str, tested_sha: str) -> dict:
    payload = path.read_bytes()
    return {
        "locator": f"git:{tested_sha}:{repository_path}",
        "path": repository_path,
        "sha256": sha256_bytes(payload),
        "size": len(payload),
    }


def _world_commitment(
    identity: CIRunIdentity,
    plan: Host0Plan,
    plan_path: Path,
    workflow_path: Path,
    project_path: Path,
    created_at: str,
) -> tuple[str, str]:
    value = {
        "_kind": "world_commitment",
        "_v": 1,
        "adapter_schema": 1,
        "artifacts": [
            _artifact(plan_path, ".github/host0-plan.json", identity.tested_sha),
            _artifact(workflow_path, plan.workflow_path, identity.tested_sha),
            _artifact(project_path, "pyproject.toml", identity.tested_sha),
        ],
        "capability": WorldRefCapability.IDENTIFIABLE.value,
        "created_at": created_at,
        "intended_environment": {
            "python_versions": [leg.python_version for leg in plan.ordered_legs],
            "runner": "ubuntu-latest",
        },
        "intended_legs": [
            {
                "commands": list(leg.commands),
                "id": leg.leg_id,
                "slot": leg.slot,
            }
            for leg in plan.ordered_legs
        ],
        "ledger_schema": LEDGER_SCHEMA,
        "plan_id": plan.plan_id,
        "repository": identity.repository,
        "tested_sha": identity.tested_sha,
        "workflow_ref": identity.workflow_ref,
    }
    commitment = canonical_world_commitment(value)
    return commitment, world_ref_for(commitment)


def _observable_declarations(adapter: CIAdapter) -> dict:
    specs = []
    for spec in sorted(adapter.observable_specs().values(), key=lambda item: item.name):
        specs.append(
            {
                "emission": spec.emission.value,
                "name": spec.name,
                "sources": list(spec.sources),
                "value_type": spec.value_type,
            }
        )
    return {
        "_kind": "observable_declarations",
        "_v": 1,
        "source_ref": adapter.source_ref,
        "specs": specs,
    }


def _cleanup_lock_files(root: Path) -> None:
    for path in root.glob("*.lock"):
        path.unlink(missing_ok=True)


def author_forecast(args: argparse.Namespace) -> int:
    identity = _identity(args)
    adapter = CIAdapter.for_repository(identity.repository)
    plan_path = Path(args.plan)
    workflow_path = Path(args.workflow)
    project_path = Path(args.project)
    plan_bytes = plan_path.read_bytes()
    plan = Host0Plan.from_bytes(plan_bytes)
    created_at = _timestamp(args.created_at)
    commitment, world_ref = _world_commitment(
        identity,
        plan,
        plan_path,
        workflow_path,
        project_path,
        created_at,
    )
    plan_digest = Host0Plan.digest(plan_bytes)
    forecast_seed = canonical_json(
        {"identity": identity.to_dict(), "plan_digest": plan_digest}
    ).encode("utf-8")
    forecast_id = f"f_host0_{sha256_bytes(forecast_seed)[:12]}"
    horizon = (datetime.fromisoformat(created_at) + timedelta(hours=24)).isoformat()
    forecast_kwargs = {
        "subject_ref": identity.subject_ref,
        "claim": "the required Host 0 checks for this tested checkout pass",
        "probability": 0.5,
        "resolution": ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=horizon,
        ),
        "exposure": Exposure.EXPOSED,
        "world_ref": world_ref,
        "world_ref_capability": WorldRefCapability.IDENTIFIABLE,
        "authored_by": "github-actions-host0",
        "assumptions": ("operational forecast; never calibration eligible",),
        "forecast_id": forecast_id,
        "created_at": created_at,
    }
    # Schema v2 binds the exact canonical commitment string into the forecast.
    # Feature detection keeps the ordered migration executable before that
    # field lands; the final v2 runtime always takes this branch.
    if "world_commitment" in {item.name for item in fields(Forecast)}:
        forecast_kwargs["world_commitment"] = commitment
    forecast = Forecast(**forecast_kwargs)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    state = output / "state"
    substrate = Substrate(state, observables=adapter.observable_specs())
    substrate.author(forecast)
    _cleanup_lock_files(state)

    _write_json(output / "identity.json", identity.to_dict())
    _write_new(output / "plan.json", plan_bytes)
    _write_new(output / "world-commitment.json", commitment.encode("utf-8"))
    _write_json(output / "observable-specs.json", _observable_declarations(adapter))
    _write_json(
        output / "authoring.json",
        {
            "_kind": "host0_authoring",
            "_v": 1,
            "created_at": created_at,
            "forecast_id": forecast_id,
            "plan_digest": plan_digest,
            "world_ref": world_ref,
        },
    )
    return 0


def write_leg_report(args: argparse.Namespace) -> int:
    identity = _identity(args)
    plan_bytes = Path(args.plan).read_bytes()
    plan = Host0Plan.from_bytes(plan_bytes)
    leg = plan.leg(args.leg_id)
    runtime_python = _runtime_python_version()
    runtime_interpreter = _runtime_interpreter()
    if args.python_version != runtime_python:
        raise ValueError("declared Python version does not match the running interpreter")
    conclusion = classify_leg_outcome(
        args.checkout_outcome,
        args.setup_outcome,
        args.install_outcome,
        args.gate_outcome,
    )
    distributions, dependency_capture_complete = _installed_distributions()
    report = CILegReport(
        identity=identity,
        plan_digest=Host0Plan.digest(plan_bytes),
        leg_id=leg.leg_id,
        slot=leg.slot,
        conclusion=conclusion,
        checkout_outcome=args.checkout_outcome,
        setup_outcome=args.setup_outcome,
        install_outcome=args.install_outcome,
        gate_outcome=args.gate_outcome,
        python_version=args.python_version,
        interpreter=runtime_interpreter,
        implementation=sys.implementation.name,
        executed_commands=leg.commands if conclusion.evidence_valid else (),
        distributions=distributions,
        dependency_capture_complete=dependency_capture_complete,
        runner_os=args.runner_os,
        runner_image=args.runner_image,
        runner_image_version=args.runner_image_version,
        created_at=_timestamp(args.created_at),
    )
    _write_json(Path(args.output), report.to_dict())
    return 0


def _installed_distributions() -> tuple[tuple[tuple[str, str], ...], bool]:
    """Capture the reporter environment without invoking a package manager."""

    captured: dict[str, tuple[str, str]] = {}
    complete = True
    try:
        for distribution in metadata.distributions():
            name = distribution.metadata.get("Name", "").strip()
            version = str(distribution.version).strip()
            if not name or not version:
                complete = False
                continue
            captured[name.lower()] = (name, version)
    except Exception:  # noqa: BLE001 - inventory degrades honestly to incomplete
        return (), False
    return tuple(sorted(captured.values(), key=lambda item: item[0].lower())), complete


def _runtime_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _runtime_interpreter() -> str:
    return sys.version


def _load_reports(directory: Path) -> tuple[list[CILegReport], list[str]]:
    reports: list[CILegReport] = []
    issues: list[str] = []
    if not directory.is_dir():
        return reports, ["reports_directory_missing"]
    for path in sorted(directory.rglob("leg-report.json")):
        try:
            reports.append(CILegReport.from_bytes(path.read_bytes()))
        except (OSError, ValueError):
            issues.append(f"malformed_report:{path.relative_to(directory).as_posix()}")
    return reports, issues


def _copy_state(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for name in ("forecasts.jsonl", "evidence.jsonl", "outcomes.jsonl"):
        source_path = source / name
        if source_path.exists():
            shutil.copyfile(source_path, target / name)


def _copy_authoring(source: Path, target: Path) -> None:
    for name in (
        "identity.json",
        "plan.json",
        "world-commitment.json",
        "observable-specs.json",
        "authoring.json",
    ):
        shutil.copyfile(source / name, target / name)
    _copy_state(source / "state", target / "state")


def _execution_inventory(aggregation: CIAggregation) -> dict:
    return {
        "_kind": "host0_execution_inventory",
        "_v": 1,
        "action_commits": dict(sorted(ACTION_COMMITS.items())),
        "aggregated_at": aggregation.aggregated_at,
        "dependency_resolution": (
            "reporter environments captured per leg; isolated build environment "
            "and mutable runner image keep the world IDENTIFIABLE"
        ),
        "identity": aggregation.identity.to_dict(),
        "legs": [
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
        ],
        "plan_digest": aggregation.plan_digest,
    }


def aggregate(args: argparse.Namespace) -> int:
    forecast_dir = Path(args.forecast_dir)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    identity = CIRunIdentity.from_dict(_read_json(forecast_dir / "identity.json"))
    plan_bytes = Path(args.plan).read_bytes()
    if (forecast_dir / "plan.json").read_bytes() != plan_bytes:
        raise ValueError("forecast artifact plan differs from repository plan")
    plan = Host0Plan.from_bytes(plan_bytes)
    plan_digest = Host0Plan.digest(plan_bytes)
    adapter = CIAdapter.for_repository(identity.repository)
    reports, report_issues = _load_reports(Path(args.reports_dir))
    aggregation = aggregate_reports(
        adapter,
        identity,
        plan,
        plan_digest,
        reports,
        aggregated_at=_timestamp(args.aggregated_at),
        external_issues=report_issues,
    )
    _copy_authoring(forecast_dir, output)
    reports_output = output / "reports"
    reports_output.mkdir()
    for report in aggregation.reports:
        _write_json(reports_output / f"{report.leg_id}.json", report.to_dict())

    substrate = Substrate(output / "state", observables=adapter.observable_specs())
    capture_ok = True
    for event in aggregation.events:
        capture_ok = substrate.capture(event) and capture_ok
    final_complete = aggregation.complete and capture_ok
    seal = adapter.finalize_run(
        identity,
        plan,
        complete=final_complete,
        sealed_at=aggregation.aggregated_at,
    )
    capture_ok = substrate.capture(seal) and capture_ok
    final_complete = final_complete and capture_ok
    substrate.process(at=aggregation.aggregated_at)
    _cleanup_lock_files(output / "state")

    aggregation_payload = aggregation.to_dict()
    aggregation_payload["capture_complete"] = final_complete
    aggregation_payload["subject_ref"] = identity.subject_ref
    aggregation_payload["epoch_ref"] = identity.epoch_ref
    _write_json(output / "aggregation.json", aggregation_payload)
    _write_json(output / "execution-inventory.json", _execution_inventory(aggregation))
    if not final_complete:
        raise IncompleteAggregation(
            "Host 0 evidence is incomplete: " + ", ".join(aggregation.issues or ("capture",))
        )
    return 0


def export_receipt(args: argparse.Namespace) -> int:
    from .ci_receipt import export_receipt as export

    path = export(Path(args.run_dir), Path(args.output_parent))
    print(path)
    return 0


def verify_receipt(args: argparse.Namespace) -> int:
    from .ci_receipt import verify_receipt as verify

    verify(Path(args.receipt))
    print(f"verified {args.receipt}")
    return 0


def _run_git(
    repository_root: Path,
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run one read-only Git plumbing command without invoking a shell."""

    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=False,
            capture_output=True,
            shell=False,
        )
    except OSError as exc:
        raise ValueError("git executable is unavailable") from exc
    if check and result.returncode != 0:
        operation = arguments[0] if arguments else "command"
        raise ValueError(f"git {operation} failed")
    return result


def _resolved_commit(repository_root: Path, revision: str, name: str) -> str:
    if revision != "HEAD" and not _FULL_GIT_OID.fullmatch(revision):
        raise ValueError(f"{name} must be HEAD or a full Git object ID")
    result = _run_git(repository_root, ["rev-parse", "--verify", f"{revision}^{{commit}}"])
    value = result.stdout.decode("ascii", "strict").strip().lower()
    if not _FULL_GIT_OID.fullmatch(value):
        raise ValueError(f"git returned an invalid {name}")
    return value


def _fallback_base(repository_root: Path, head: str, default_branch: str) -> str:
    if (
        not _DEFAULT_BRANCH.fullmatch(default_branch)
        or default_branch.startswith(('-', '/'))
        or ".." in default_branch
        or "//" in default_branch
    ):
        raise ValueError("default branch is not a safe Git ref component")
    remote_ref = f"refs/remotes/origin/{default_branch}"
    remote = _run_git(
        repository_root,
        ["rev-parse", "--verify", f"{remote_ref}^{{commit}}"],
    ).stdout.decode("ascii", "strict").strip().lower()
    if not _FULL_GIT_OID.fullmatch(remote):
        raise ValueError("origin/default branch did not resolve to a full Git object ID")
    merge_base = _run_git(repository_root, ["merge-base", head, remote]).stdout
    value = merge_base.decode("ascii", "strict").strip().lower()
    if not _FULL_GIT_OID.fullmatch(value):
        raise ValueError("merge-base did not return a full Git object ID")
    return value


def _missing_base(value: str | None) -> bool:
    return not value or not value.strip() or set(value.strip()) == {"0"}


def _select_base(
    repository_root: Path,
    *,
    event_name: str,
    explicit_base: str | None,
    pull_request_base: str | None,
    push_before: str | None,
    default_branch: str,
    head: str,
) -> str:
    if not _missing_base(explicit_base):
        selected = explicit_base
    elif event_name == "pull_request":
        if _missing_base(pull_request_base):
            raise ValueError("pull_request requires its authoritative base SHA")
        selected = pull_request_base
    elif event_name == "push":
        selected = push_before
    else:
        selected = None

    if _missing_base(selected):
        return _fallback_base(repository_root, head, default_branch)
    assert selected is not None
    return _resolved_commit(repository_root, selected.strip(), "base")


def _decode_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Git reported a non-UTF-8 path") from exc
    if not path:
        raise ValueError("Git reported an empty path")
    return path


def _parse_name_status(payload: bytes) -> tuple[PathChange, ...]:
    """Parse the unambiguous NUL form of ``git diff --name-status``."""

    if not payload:
        return ()
    if not payload.endswith(b"\0"):
        raise ValueError("Git name-status output has a torn tail")
    tokens = payload[:-1].split(b"\0")
    changes: list[PathChange] = []
    index = 0
    while index < len(tokens):
        try:
            status = tokens[index].decode("ascii", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Git reported a malformed path status") from exc
        index += 1
        if not status or status[0] not in "ACDMRTUXB":
            raise ValueError("Git reported an unsupported path status")
        if status[0] in "RC":
            if len(status) == 1 or not status[1:].isdigit():
                raise ValueError("Git reported a malformed rename/copy score")
            path_count = 2
        else:
            if len(status) != 1:
                raise ValueError("Git reported a malformed path status")
            path_count = 1
        if index + path_count > len(tokens):
            raise ValueError("Git name-status output omitted a path")
        paths = tuple(_decode_path(value) for value in tokens[index : index + path_count])
        changes.append(PathChange(status=status, paths=paths))
        index += path_count
    return tuple(changes)


def _changes_between(repository_root: Path, base: str, head: str) -> tuple[PathChange, ...]:
    result = _run_git(
        repository_root,
        ["diff", "--name-status", "-z", "--find-renames", f"{base}..{head}", "--"],
    )
    return _parse_name_status(result.stdout)


def _commit_transitions(
    repository_root: Path,
    base: str,
    head: str,
) -> tuple[tuple[str, str], ...]:
    if base == head:
        return ()
    ancestry = _run_git(
        repository_root,
        ["merge-base", "--is-ancestor", base, head],
        check=False,
    )
    if ancestry.returncode != 0:
        raise ValueError("authoritative base is not an ancestor of HEAD")
    payload = _run_git(
        repository_root,
        ["rev-list", "--reverse", "--topo-order", "--parents", f"{base}..{head}"],
    ).stdout
    try:
        lines = payload.decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Git returned malformed commit ancestry") from exc
    transitions: list[tuple[str, str]] = []
    for line in lines:
        values = line.split()
        if not values or any(not _FULL_GIT_OID.fullmatch(value) for value in values):
            raise ValueError("Git returned malformed commit ancestry")
        commit, *parents = (value.lower() for value in values)
        if not parents:
            raise ValueError("pushed range unexpectedly contains a root commit")
        transitions.extend((parent, commit) for parent in parents)
    if not transitions:
        raise ValueError("Git returned an empty nontrivial pushed range")
    return tuple(transitions)


def _blob_at_revision(repository_root: Path, revision: str, path: str) -> bytes | None:
    listing = _run_git(
        repository_root,
        ["ls-tree", "-z", "--name-only", revision, "--", path],
    ).stdout
    names = [item for item in listing.split(b"\0") if item]
    if not names:
        return None
    if names != [path.encode("utf-8")]:
        raise ValueError(f"Git tree lookup for {path} was ambiguous")
    return _run_git(repository_root, ["show", f"{revision}:{path}"]).stdout


def _validate_exclusion_transition(
    repository_root: Path,
    prior_revision: str,
    current_revision: str,
    changes: tuple[PathChange, ...],
) -> None:
    prior = _blob_at_revision(repository_root, prior_revision, _EXCLUSIONS_PATH)
    current = _blob_at_revision(repository_root, current_revision, _EXCLUSIONS_PATH)
    if current is None:
        if prior is None:
            return
        raise ValueError("calibration exclusion registry was deleted")
    if prior is None:
        introduced = any(
            change.status == "A" and change.paths == (_EXCLUSIONS_PATH,)
            for change in changes
        )
        if not introduced:
            raise ValueError("calibration exclusion registry was not added as a new path")
        prior = b""
    validate_exclusion_prefix(prior, current)


def _validate_current_exclusions(repository_root: Path, head: str) -> None:
    current_path = repository_root / _EXCLUSIONS_PATH
    if current_path.is_symlink() or not current_path.is_file():
        raise ValueError("calibration exclusion registry must be a regular file")
    # This independent load is intentional: malformed current policy fails
    # even when every transition claims the file was unchanged.
    load_exclusions(current_path)
    committed = _blob_at_revision(repository_root, head, _EXCLUSIONS_PATH)
    if committed is None or current_path.read_bytes() != committed:
        raise ValueError("working exclusion registry differs from committed HEAD")


def _verify_receipt_tree(repository_root: Path) -> None:
    from .ci_receipt import verify_receipt as verify

    root = repository_root / _RECEIPTS_ROOT
    if not os.path.lexists(root):
        return
    if root.is_symlink() or not root.is_dir():
        raise ValueError("receipt root must be a regular directory")
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if child.is_symlink() or not child.is_dir():
            raise ValueError("receipt root may contain only receipt directories")
        verify(child)


def check_research_integrity(args: argparse.Namespace) -> int:
    repository_root = Path(args.repository_root).resolve()
    if repository_root.is_symlink() or not repository_root.is_dir():
        raise ValueError("repository root must be a regular directory")
    event_name = (
        args.event_name
        or os.environ.get("HANISH_EVENT_NAME")
        or os.environ.get("GITHUB_EVENT_NAME", "")
    ).strip()
    head_value = (
        args.head
        or os.environ.get("HANISH_HEAD_SHA")
        or os.environ.get("GITHUB_SHA", "HEAD")
    ).strip()
    head = _resolved_commit(repository_root, head_value, "head")
    explicit_base = args.base_sha or os.environ.get("HANISH_BASE_SHA")
    pull_request_base = args.pull_request_base_sha or os.environ.get(
        "HANISH_PULL_REQUEST_BASE_SHA"
    )
    push_before = args.push_before_sha or os.environ.get("HANISH_PUSH_BEFORE_SHA")
    default_branch = (
        args.default_branch
        or os.environ.get("HANISH_DEFAULT_BRANCH")
        or "main"
    ).strip()
    base = _select_base(
        repository_root,
        event_name=event_name,
        explicit_base=explicit_base,
        pull_request_base=pull_request_base,
        push_before=push_before,
        default_branch=default_branch,
        head=head,
    )
    for prior, current in _commit_transitions(repository_root, base, head):
        changes = _changes_between(repository_root, prior, current)
        validate_receipt_additions(changes)
        _validate_exclusion_transition(repository_root, prior, current, changes)
    _validate_current_exclusions(repository_root, head)
    _verify_receipt_tree(repository_root)
    print(f"research integrity verified against {base}")
    return 0


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--tested-sha", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hanish-ci")
    commands = parser.add_subparsers(dest="command", required=True)

    author = commands.add_parser("author-forecast")
    _identity_arguments(author)
    author.add_argument("--plan", required=True)
    author.add_argument("--workflow", required=True)
    author.add_argument("--project", required=True)
    author.add_argument("--output", required=True)
    author.add_argument("--created-at")
    author.set_defaults(handler=author_forecast)

    report = commands.add_parser("write-leg-report")
    _identity_arguments(report)
    report.add_argument("--plan", required=True)
    report.add_argument("--leg-id", required=True)
    report.add_argument("--checkout-outcome", required=True)
    report.add_argument("--setup-outcome", required=True)
    report.add_argument("--install-outcome", required=True)
    report.add_argument("--gate-outcome", required=True)
    report.add_argument("--python-version", required=True)
    report.add_argument("--runner-os", default=os.environ.get("RUNNER_OS", ""))
    report.add_argument("--runner-image", default=os.environ.get("ImageOS", ""))
    report.add_argument(
        "--runner-image-version",
        default=os.environ.get("ImageVersion", ""),
    )
    report.add_argument("--created-at")
    report.add_argument("--output", required=True)
    report.set_defaults(handler=write_leg_report)

    aggregate_parser = commands.add_parser("aggregate")
    aggregate_parser.add_argument("--forecast-dir", required=True)
    aggregate_parser.add_argument("--reports-dir", required=True)
    aggregate_parser.add_argument("--plan", required=True)
    aggregate_parser.add_argument("--output", required=True)
    aggregate_parser.add_argument("--aggregated-at")
    aggregate_parser.set_defaults(handler=aggregate)

    export_parser = commands.add_parser("export-receipt")
    export_parser.add_argument("--run-dir", required=True)
    export_parser.add_argument("--output-parent", required=True)
    export_parser.set_defaults(handler=export_receipt)

    verify_parser = commands.add_parser("verify-receipt")
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.set_defaults(handler=verify_receipt)

    integrity = commands.add_parser("check-research-integrity")
    integrity.add_argument("--repository-root", default=".")
    integrity.add_argument("--event-name")
    integrity.add_argument("--base-sha")
    integrity.add_argument("--pull-request-base-sha")
    integrity.add_argument("--push-before-sha")
    integrity.add_argument("--default-branch")
    integrity.add_argument("--head")
    integrity.set_defaults(handler=check_research_integrity)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except IncompleteAggregation as exc:
        print(f"hanish-ci: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI owns stable error presentation
        print(f"hanish-ci: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
