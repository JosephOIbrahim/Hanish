"""GitHub Actions host adapter and Host 0 aggregation contracts.

This is the only module in Hanish that knows what a repository, workflow,
commit, or CI leg is. The core receives opaque identities and declared
observables; all CI interpretation stays on this side of the adapter seam.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from ..future.claims import EmissionSemantics, ObservableSpec, WorldRefCapability
from ..past.events import CompletenessSeal, ObservationEvent, Validity
from ..time import now

# Kept for v1 callers. Autonomous Host 0 always configures a repository and
# therefore uses ``github-actions:<owner>/<repository>`` instead.
SOURCE = "github-actions"

REQUIRED_CHECKS_PASS = "ci.required_checks_pass"
REQUIRED_LEG_PASS = "ci.required_leg_pass"
DURATION_S = "ci.duration_s"

PLAN_KIND = "host0_plan"
PLAN_VERSION = 1
LEG_REPORT_KIND = "host0_leg_report"
LEG_REPORT_VERSION = 1

ACTION_COMMITS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",  # v8.0.1
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",  # v7.0.0
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # v7.0.1
}

_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REPOSITORY = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
_STEP_OUTCOMES = frozenset({"success", "failure", "cancelled", "skipped"})


def canonical_json(value: object) -> str:
    """Return the single canonical JSON representation used by Host 0."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_json(payload: bytes, name: str) -> object:
    """Decode signed Host 0 JSON without lossy parser extensions."""

    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name} is not valid UTF-8 JSON") from exc

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{name} contains duplicate key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{name} contains non-finite number {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid UTF-8 JSON") from exc
    except ValueError:
        raise


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _safe_id(value: object, name: str) -> str:
    value = _nonempty(value, name)
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} is not a canonical identifier")
    return value


def _aware_timestamp(value: object, name: str) -> str:
    value = _nonempty(value, name)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _exact_keys(payload: dict, expected: set[str], name: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{name} fields differ; missing={missing}, extra={extra}")


@dataclass(frozen=True)
class CIRunIdentity:
    """Immutable coordinates shared by every artifact in one workflow attempt."""

    repository: str
    workflow_ref: str
    run_id: int
    run_attempt: int
    tested_sha: str

    def __post_init__(self) -> None:
        repository = _nonempty(self.repository, "repository").lower()
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be owner/name")
        workflow_ref = _nonempty(self.workflow_ref, "workflow_ref")
        tested_sha = _nonempty(self.tested_sha, "tested_sha").lower()
        if not _GIT_SHA.fullmatch(tested_sha):
            raise ValueError("tested_sha must be a full 40- or 64-hex Git object ID")
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "workflow_ref", workflow_ref)
        object.__setattr__(self, "run_id", _positive_int(self.run_id, "run_id"))
        object.__setattr__(self, "run_attempt", _positive_int(self.run_attempt, "run_attempt"))
        object.__setattr__(self, "tested_sha", tested_sha)

    @property
    def source_ref(self) -> str:
        return f"github-actions:{self.repository}"

    @property
    def subject_ref(self) -> str:
        return f"git:{self.tested_sha}"

    @property
    def epoch_ref(self) -> str:
        # Hashing avoids delimiter ambiguity while the complete coordinates
        # remain present in every report/event metadata and receipt.
        payload = canonical_json(
            {
                "repository": self.repository,
                "run_attempt": self.run_attempt,
                "run_id": self.run_id,
                "workflow_ref": self.workflow_ref,
            }
        ).encode("utf-8")
        return f"github-actions-run:sha256:{sha256_bytes(payload)}"

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "run_attempt": self.run_attempt,
            "run_id": self.run_id,
            "tested_sha": self.tested_sha,
            "workflow_ref": self.workflow_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> CIRunIdentity:
        if not isinstance(payload, dict):
            raise ValueError("run identity must be an object")
        expected = {"repository", "workflow_ref", "run_id", "run_attempt", "tested_sha"}
        _exact_keys(payload, expected, "run identity")
        return cls(**payload)


@dataclass(frozen=True)
class CIPlanLeg:
    leg_id: str
    slot: int
    kind: str
    python_version: str
    commands: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "leg_id", _safe_id(self.leg_id, "leg_id"))
        object.__setattr__(self, "slot", _positive_int(self.slot, "slot"))
        if self.kind not in {"test", "build"}:
            raise ValueError("leg kind must be 'test' or 'build'")
        object.__setattr__(
            self,
            "python_version",
            _nonempty(self.python_version, "python_version"),
        )
        commands = tuple(_nonempty(command, "command") for command in self.commands)
        if not commands:
            raise ValueError("a plan leg must declare at least one command")
        object.__setattr__(self, "commands", commands)

    def to_dict(self) -> dict:
        return {
            "commands": list(self.commands),
            "id": self.leg_id,
            "kind": self.kind,
            "python": self.python_version,
            "slot": self.slot,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> CIPlanLeg:
        if not isinstance(payload, dict):
            raise ValueError("plan leg must be an object")
        _exact_keys(payload, {"id", "slot", "kind", "python", "commands"}, "plan leg")
        commands = payload["commands"]
        if not isinstance(commands, list) or any(not isinstance(item, str) for item in commands):
            raise ValueError("plan leg commands must be an array of strings")
        return cls(
            leg_id=payload["id"],
            slot=payload["slot"],
            kind=payload["kind"],
            python_version=payload["python"],
            commands=tuple(commands),
        )


@dataclass(frozen=True)
class Host0Plan:
    plan_id: str
    workflow_path: str
    legs: tuple[CIPlanLeg, ...]
    aggregate_id: str
    aggregate_slot: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _safe_id(self.plan_id, "plan_id"))
        workflow_path = _nonempty(self.workflow_path, "workflow_path")
        if workflow_path.startswith(("/", "\\")) or ".." in Path(workflow_path).parts:
            raise ValueError("workflow_path must be repository-relative")
        object.__setattr__(self, "workflow_path", workflow_path.replace("\\", "/"))
        legs = tuple(self.legs)
        if not legs:
            raise ValueError("Host 0 plan must contain at least one leg")
        object.__setattr__(self, "legs", legs)
        object.__setattr__(self, "aggregate_id", _safe_id(self.aggregate_id, "aggregate_id"))
        object.__setattr__(
            self,
            "aggregate_slot",
            _positive_int(self.aggregate_slot, "aggregate_slot"),
        )
        ids = [leg.leg_id for leg in legs]
        slots = [leg.slot for leg in legs]
        if len(set(ids)) != len(ids) or self.aggregate_id in ids:
            raise ValueError("plan leg identifiers must be unique")
        if len(set(slots)) != len(slots):
            raise ValueError("plan leg slots must be unique")
        expected = list(range(1, len(legs) + 1))
        if sorted(slots) != expected or self.aggregate_slot != len(legs) + 1:
            raise ValueError("plan slots must be contiguous with aggregate last")

    @property
    def ordered_legs(self) -> tuple[CIPlanLeg, ...]:
        return tuple(sorted(self.legs, key=lambda leg: leg.slot))

    def leg(self, leg_id: str) -> CIPlanLeg:
        matches = [leg for leg in self.legs if leg.leg_id == leg_id]
        if len(matches) != 1:
            raise ValueError(f"unknown plan leg {leg_id!r}")
        return matches[0]

    def to_dict(self) -> dict:
        return {
            "_kind": PLAN_KIND,
            "_v": PLAN_VERSION,
            "aggregate": {"id": self.aggregate_id, "slot": self.aggregate_slot},
            "legs": [leg.to_dict() for leg in self.ordered_legs],
            "plan_id": self.plan_id,
            "workflow_path": self.workflow_path,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> Host0Plan:
        if not isinstance(payload, dict):
            raise ValueError("Host 0 plan must be an object")
        expected = {"_kind", "_v", "plan_id", "workflow_path", "legs", "aggregate"}
        _exact_keys(payload, expected, "Host 0 plan")
        version = payload["_v"]
        if (
            payload["_kind"] != PLAN_KIND
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version != PLAN_VERSION
        ):
            raise ValueError("unsupported Host 0 plan kind or version")
        legs = payload["legs"]
        aggregate = payload["aggregate"]
        if not isinstance(legs, list) or not isinstance(aggregate, dict):
            raise ValueError("Host 0 plan legs/aggregate are malformed")
        _exact_keys(aggregate, {"id", "slot"}, "plan aggregate")
        return cls(
            plan_id=payload["plan_id"],
            workflow_path=payload["workflow_path"],
            legs=tuple(CIPlanLeg.from_dict(leg) for leg in legs),
            aggregate_id=aggregate["id"],
            aggregate_slot=aggregate["slot"],
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> Host0Plan:
        decoded = _strict_json(payload, "Host 0 plan")
        return cls.from_dict(decoded)

    @classmethod
    def load(cls, path: str | Path) -> Host0Plan:
        return cls.from_bytes(Path(path).read_bytes())

    @staticmethod
    def digest(payload: bytes) -> str:
        return sha256_bytes(payload)


class LegConclusion(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    CANCELLED = "CANCELLED"

    @property
    def evidence_valid(self) -> bool:
        return self in {LegConclusion.PASSED, LegConclusion.FAILED}

    @property
    def passed(self) -> bool | None:
        if self is LegConclusion.PASSED:
            return True
        if self is LegConclusion.FAILED:
            return False
        return None


def classify_leg_outcome(
    checkout_outcome: str,
    setup_outcome: str,
    install_outcome: str,
    gate_outcome: str,
) -> LegConclusion:
    """Separate genuine product failure from missing/invalid infrastructure."""

    outcomes = (checkout_outcome, setup_outcome, install_outcome, gate_outcome)
    if any(value not in _STEP_OUTCOMES for value in outcomes):
        raise ValueError("step outcome is not a GitHub Actions outcome")
    if "cancelled" in outcomes:
        return LegConclusion.CANCELLED
    if outcomes[:3] != ("success", "success", "success"):
        return LegConclusion.INFRASTRUCTURE_FAILURE
    if gate_outcome == "success":
        return LegConclusion.PASSED
    if gate_outcome == "failure":
        return LegConclusion.FAILED
    return LegConclusion.INFRASTRUCTURE_FAILURE


@dataclass(frozen=True)
class CILegReport:
    identity: CIRunIdentity
    plan_digest: str
    leg_id: str
    slot: int
    conclusion: LegConclusion
    checkout_outcome: str
    setup_outcome: str
    install_outcome: str
    gate_outcome: str
    python_version: str
    interpreter: str
    implementation: str
    executed_commands: tuple[str, ...]
    distributions: tuple[tuple[str, str], ...]
    dependency_capture_complete: bool
    runner_os: str
    runner_image: str
    runner_image_version: str
    created_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CIRunIdentity):
            raise ValueError("identity must be CIRunIdentity")
        digest = _nonempty(self.plan_digest, "plan_digest").lower()
        if not _HEX_DIGEST.fullmatch(digest):
            raise ValueError("plan_digest must be 64 lowercase hex characters")
        object.__setattr__(self, "plan_digest", digest)
        object.__setattr__(self, "leg_id", _safe_id(self.leg_id, "leg_id"))
        object.__setattr__(self, "slot", _positive_int(self.slot, "slot"))
        try:
            conclusion = LegConclusion(self.conclusion)
        except ValueError as exc:
            raise ValueError("unknown leg conclusion") from exc
        object.__setattr__(self, "conclusion", conclusion)
        expected = classify_leg_outcome(
            self.checkout_outcome,
            self.setup_outcome,
            self.install_outcome,
            self.gate_outcome,
        )
        if conclusion is not expected:
            raise ValueError("leg conclusion does not match its step outcomes")
        commands = tuple(
            _nonempty(command, "executed command")
            for command in self.executed_commands
        )
        object.__setattr__(self, "executed_commands", commands)
        distributions = tuple(
            sorted(
                (
                    _nonempty(name, "distribution name"),
                    _nonempty(version, "distribution version"),
                )
                for name, version in self.distributions
            )
        )
        if len({name.lower() for name, _ in distributions}) != len(distributions):
            raise ValueError("distribution names must be unique")
        object.__setattr__(self, "distributions", distributions)
        if not isinstance(self.dependency_capture_complete, bool):
            raise ValueError("dependency_capture_complete must be a strict bool")
        for field_name in (
            "python_version",
            "interpreter",
            "implementation",
            "runner_os",
            "runner_image",
            "runner_image_version",
            "created_at",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"{field_name} must be a string")
        interpreter_version = re.match(r"^(\d+)\.(\d+)(?:\.|\s|$)", self.interpreter)
        if interpreter_version is None:
            raise ValueError("interpreter must begin with its Python version")
        actual_python = ".".join(interpreter_version.groups())
        if self.python_version != actual_python:
            raise ValueError("declared Python version does not match the interpreter")
        object.__setattr__(self, "created_at", _aware_timestamp(self.created_at, "created_at"))

    @property
    def evidence_valid(self) -> bool:
        return self.conclusion.evidence_valid

    @property
    def passed(self) -> bool | None:
        return self.conclusion.passed

    def to_dict(self) -> dict:
        return {
            "_kind": LEG_REPORT_KIND,
            "_v": LEG_REPORT_VERSION,
            "conclusion": self.conclusion.value,
            "created_at": self.created_at,
            "dependencies": {
                "complete": self.dependency_capture_complete,
                "distributions": [
                    {"name": name, "version": version}
                    for name, version in self.distributions
                ],
            },
            "executed_commands": list(self.executed_commands),
            "identity": self.identity.to_dict(),
            "interpreter": {
                "implementation": self.implementation,
                "version": self.interpreter,
            },
            "leg_id": self.leg_id,
            "plan_digest": self.plan_digest,
            "python_version": self.python_version,
            "runner": {
                "image": self.runner_image,
                "image_version": self.runner_image_version,
                "os": self.runner_os,
            },
            "slot": self.slot,
            "steps": {
                "checkout": self.checkout_outcome,
                "gate": self.gate_outcome,
                "install": self.install_outcome,
                "setup": self.setup_outcome,
            },
        }

    @classmethod
    def from_dict(cls, payload: dict) -> CILegReport:
        if not isinstance(payload, dict):
            raise ValueError("leg report must be an object")
        expected = {
            "_kind",
            "_v",
            "identity",
            "plan_digest",
            "leg_id",
            "slot",
            "conclusion",
            "dependencies",
            "executed_commands",
            "steps",
            "python_version",
            "interpreter",
            "runner",
            "created_at",
        }
        _exact_keys(payload, expected, "leg report")
        version = payload["_v"]
        if (
            payload["_kind"] != LEG_REPORT_KIND
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version != LEG_REPORT_VERSION
        ):
            raise ValueError("unsupported leg report kind or version")
        steps = payload["steps"]
        dependencies = payload["dependencies"]
        commands = payload["executed_commands"]
        interpreter = payload["interpreter"]
        runner = payload["runner"]
        if (
            not isinstance(steps, dict)
            or not isinstance(dependencies, dict)
            or not isinstance(commands, list)
            or not isinstance(interpreter, dict)
            or not isinstance(runner, dict)
        ):
            raise ValueError("leg report collections are malformed")
        _exact_keys(steps, {"checkout", "setup", "install", "gate"}, "leg steps")
        _exact_keys(dependencies, {"complete", "distributions"}, "dependencies")
        _exact_keys(interpreter, {"implementation", "version"}, "interpreter")
        _exact_keys(runner, {"os", "image", "image_version"}, "runner")
        raw_distributions = dependencies["distributions"]
        if (
            any(not isinstance(command, str) for command in commands)
            or not isinstance(raw_distributions, list)
            or any(
                not isinstance(item, dict) or set(item) != {"name", "version"}
                for item in raw_distributions
            )
        ):
            raise ValueError("leg commands/dependencies are malformed")
        return cls(
            identity=CIRunIdentity.from_dict(payload["identity"]),
            plan_digest=payload["plan_digest"],
            leg_id=payload["leg_id"],
            slot=payload["slot"],
            conclusion=LegConclusion(payload["conclusion"]),
            checkout_outcome=steps["checkout"],
            setup_outcome=steps["setup"],
            install_outcome=steps["install"],
            gate_outcome=steps["gate"],
            python_version=payload["python_version"],
            interpreter=interpreter["version"],
            implementation=interpreter["implementation"],
            executed_commands=tuple(commands),
            distributions=tuple(
                (item["name"], item["version"]) for item in raw_distributions
            ),
            dependency_capture_complete=dependencies["complete"],
            runner_os=runner["os"],
            runner_image=runner["image"],
            runner_image_version=runner["image_version"],
            created_at=payload["created_at"],
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> CILegReport:
        decoded = _strict_json(payload, "leg report")
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class CIAggregation:
    identity: CIRunIdentity
    plan_digest: str
    reports: tuple[CILegReport, ...]
    events: tuple[ObservationEvent, ...]
    issues: tuple[str, ...]
    complete: bool
    required_checks_pass: bool | None
    aggregated_at: str

    def to_dict(self) -> dict:
        return {
            "_kind": "host0_aggregation",
            "_v": 1,
            "aggregated_at": self.aggregated_at,
            "complete": self.complete,
            "identity": self.identity.to_dict(),
            "issues": list(self.issues),
            "plan_digest": self.plan_digest,
            "reports": [report.leg_id for report in self.reports],
            "required_checks_pass": self.required_checks_pass,
        }


class CIAdapter:
    """Declare CI observables and translate validated Host 0 envelopes."""

    # The legacy digest named an information state but never contained enough
    # bytes or immutable locators to reconstruct it.
    world_ref_capability = WorldRefCapability.IDENTIFIABLE

    def __init__(self, source_ref: str = SOURCE, *, repository: str | None = None):
        if repository is not None:
            normalized = CIRunIdentity(
                repository=repository,
                workflow_ref="identity-only",
                run_id=1,
                run_attempt=1,
                tested_sha="0" * 40,
            ).repository
            source_ref = f"github-actions:{normalized}"
        self.source_ref = _nonempty(source_ref, "source_ref")
        # Compatibility only for the public v1 convenience helpers below.
        # Autonomous Host 0 never consults this process-local counter.
        self._legacy_seq: dict[str, int] = {}

    @classmethod
    def for_repository(cls, repository: str) -> CIAdapter:
        return cls(repository=repository)

    def observable_specs(self) -> dict[str, ObservableSpec]:
        return {
            REQUIRED_CHECKS_PASS: ObservableSpec(
                name=REQUIRED_CHECKS_PASS,
                value_type="bool",
                emission=EmissionSemantics.TERMINAL,
                sources=(self.source_ref,),
            ),
            REQUIRED_LEG_PASS: ObservableSpec(
                name=REQUIRED_LEG_PASS,
                value_type="bool",
                emission=EmissionSemantics.PERIODIC,
                sources=(self.source_ref,),
            ),
            DURATION_S: ObservableSpec(
                name=DURATION_S,
                value_type="float",
                emission=EmissionSemantics.PER_SUBJECT,
                sources=(self.source_ref,),
            ),
        }

    @staticmethod
    def subject_ref(commit_sha: str) -> str:
        return f"git:{commit_sha}"

    @staticmethod
    def world_ref(commit_sha: str, workflow_sha: str, lockfile_sha: str) -> str:
        """Return an honest full digest for a merely identifiable legacy world."""

        digest = sha256_bytes(f"{commit_sha}|{workflow_sha}|{lockfile_sha}".encode())
        return f"world:sha256:{digest}"

    def _assert_identity(self, identity: CIRunIdentity) -> None:
        if identity.source_ref != self.source_ref:
            raise ValueError("run identity does not belong to this adapter source")

    @staticmethod
    def _event_metadata(
        identity: CIRunIdentity,
        plan_digest: str,
        leg_id: str,
        slot: int,
    ) -> dict:
        return {
            "leg_id": leg_id,
            "plan_digest": plan_digest,
            "repository": identity.repository,
            "run_attempt": identity.run_attempt,
            "run_id": identity.run_id,
            "slot": slot,
            "tested_sha": identity.tested_sha,
            "workflow_ref": identity.workflow_ref,
        }

    def leg_event(
        self,
        identity: CIRunIdentity,
        plan_digest: str,
        report: CILegReport,
        *,
        arrived_at: str,
    ) -> ObservationEvent:
        self._assert_identity(identity)
        if report.passed is None:
            raise ValueError("infrastructure report cannot become evidence")
        return ObservationEvent(
            source_ref=self.source_ref,
            event_id=(
                f"run-{identity.run_id}:attempt-{identity.run_attempt}:"
                f"leg-{report.leg_id}"
            ),
            subject_ref=identity.subject_ref,
            observable=REQUIRED_LEG_PASS,
            value=report.passed,
            source_seq=report.slot,
            epoch_ref=identity.epoch_ref,
            emitted_at=report.created_at,
            validity=Validity.VALID,
            metadata=self._event_metadata(
                identity,
                plan_digest,
                report.leg_id,
                report.slot,
            ),
            arrived_at=arrived_at,
        )

    def aggregate_event(
        self,
        identity: CIRunIdentity,
        plan: Host0Plan,
        plan_digest: str,
        passed: bool,
        *,
        arrived_at: str,
    ) -> ObservationEvent:
        self._assert_identity(identity)
        if not isinstance(passed, bool):
            raise ValueError("aggregate result must be a strict bool")
        return ObservationEvent(
            source_ref=self.source_ref,
            event_id=(
                f"run-{identity.run_id}:attempt-{identity.run_attempt}:"
                "aggregate-required-checks"
            ),
            subject_ref=identity.subject_ref,
            observable=REQUIRED_CHECKS_PASS,
            value=passed,
            source_seq=plan.aggregate_slot,
            epoch_ref=identity.epoch_ref,
            emitted_at=arrived_at,
            validity=Validity.VALID,
            metadata=self._event_metadata(
                identity,
                plan_digest,
                plan.aggregate_id,
                plan.aggregate_slot,
            ),
            arrived_at=arrived_at,
        )

    def finalize_run(
        self,
        identity: CIRunIdentity,
        plan: Host0Plan,
        *,
        complete: bool,
        sealed_at: str | None = None,
    ) -> CompletenessSeal:
        self._assert_identity(identity)
        return _make_seal(
            source_ref=self.source_ref,
            subject_ref=identity.subject_ref,
            epoch_ref=identity.epoch_ref,
            final_source_seq=plan.aggregate_slot,
            complete=complete,
            sealed_at=sealed_at,
        )

    # -- v1 convenience API -------------------------------------------------

    def _next_legacy_seq(self, epoch_ref: str) -> int:
        self._legacy_seq[epoch_ref] = self._legacy_seq.get(epoch_ref, 0) + 1
        return self._legacy_seq[epoch_ref]

    def checks_result(
        self,
        commit_sha: str,
        run_id: str,
        attempt: int,
        passed: bool,
        infrastructure_failure: bool = False,
    ) -> ObservationEvent:
        subject = self.subject_ref(commit_sha)
        return ObservationEvent(
            source_ref=self.source_ref,
            event_id=f"run-{run_id}:attempt-{attempt}:required_checks",
            subject_ref=subject,
            observable=REQUIRED_CHECKS_PASS,
            value=passed,
            source_seq=self._next_legacy_seq(subject),
            epoch_ref=subject,
            validity=Validity.INVALID if infrastructure_failure else Validity.VALID,
            metadata={"run_id": run_id, "attempt": attempt},
        )

    def duration(
        self,
        commit_sha: str,
        run_id: str,
        attempt: int,
        seconds: float,
    ) -> ObservationEvent:
        subject = self.subject_ref(commit_sha)
        return ObservationEvent(
            source_ref=self.source_ref,
            event_id=f"run-{run_id}:attempt-{attempt}:duration",
            subject_ref=subject,
            observable=DURATION_S,
            value=seconds,
            source_seq=self._next_legacy_seq(subject),
            epoch_ref=subject,
            metadata={"run_id": run_id, "attempt": attempt},
        )

    def finalize(self, commit_sha: str, complete: bool = True) -> CompletenessSeal:
        subject = self.subject_ref(commit_sha)
        return _make_seal(
            source_ref=self.source_ref,
            subject_ref=subject,
            epoch_ref=subject,
            final_source_seq=self._legacy_seq.get(subject, 0),
            complete=complete,
        )


def aggregate_reports(
    adapter: CIAdapter,
    identity: CIRunIdentity,
    plan: Host0Plan,
    plan_digest: str,
    reports: Iterable[CILegReport],
    *,
    aggregated_at: str | None = None,
    external_issues: Iterable[str] = (),
) -> CIAggregation:
    """Validate exact plan membership and produce deterministic evidence."""

    if not _HEX_DIGEST.fullmatch(plan_digest):
        raise ValueError("plan_digest must be 64 lowercase hex characters")
    adapter._assert_identity(identity)
    at = _aware_timestamp(aggregated_at or now(), "aggregated_at")
    aggregation_time = datetime.fromisoformat(at)
    grouped: dict[str, list[CILegReport]] = {}
    unknown: list[CILegReport] = []
    expected_ids = {leg.leg_id for leg in plan.legs}
    for report in reports:
        if report.leg_id not in expected_ids:
            unknown.append(report)
        else:
            grouped.setdefault(report.leg_id, []).append(report)

    issues = [str(issue) for issue in external_issues]
    issues.extend(f"unknown_leg:{report.leg_id}" for report in unknown)
    accepted: list[CILegReport] = []
    for leg in plan.ordered_legs:
        candidates = grouped.get(leg.leg_id, [])
        if not candidates:
            issues.append(f"missing_leg:{leg.leg_id}")
            continue
        if len(candidates) != 1:
            issues.append(f"duplicate_leg:{leg.leg_id}")
            continue
        report = candidates[0]
        if report.identity != identity:
            issues.append(f"identity_mismatch:{leg.leg_id}")
        elif report.plan_digest != plan_digest:
            issues.append(f"plan_mismatch:{leg.leg_id}")
        elif report.slot != leg.slot:
            issues.append(f"slot_mismatch:{leg.leg_id}")
        elif report.python_version != leg.python_version:
            issues.append(f"python_mismatch:{leg.leg_id}")
        elif datetime.fromisoformat(report.created_at) > aggregation_time:
            issues.append(f"future_report:{leg.leg_id}")
        elif not report.dependency_capture_complete:
            issues.append(f"dependency_capture_incomplete:{leg.leg_id}")
        elif not report.evidence_valid:
            issues.append(f"invalid_leg:{leg.leg_id}:{report.conclusion.value}")
        elif report.executed_commands != leg.commands:
            issues.append(f"commands_mismatch:{leg.leg_id}")
        else:
            accepted.append(report)

    issues = sorted(set(issues))
    accepted.sort(key=lambda report: report.slot)
    complete = not issues and len(accepted) == len(plan.legs)
    events = [
        adapter.leg_event(identity, plan_digest, report, arrived_at=at)
        for report in accepted
    ]
    required_checks_pass: bool | None = None
    if complete:
        required_checks_pass = all(report.passed is True for report in accepted)
        events.append(
            adapter.aggregate_event(
                identity,
                plan,
                plan_digest,
                required_checks_pass,
                arrived_at=at,
            )
        )
    return CIAggregation(
        identity=identity,
        plan_digest=plan_digest,
        reports=tuple(accepted),
        events=tuple(events),
        issues=tuple(issues),
        complete=complete,
        required_checks_pass=required_checks_pass,
        aggregated_at=at,
    )


def _make_seal(
    *,
    source_ref: str,
    subject_ref: str,
    epoch_ref: str,
    final_source_seq: int,
    complete: bool,
    sealed_at: str | None = None,
) -> CompletenessSeal:
    """Bridge the v1 seal while the schema-v2 subject field lands.

    The final schema consumes ``subject_ref``. The field check keeps the v1
    compatibility helpers usable during the ordered migration without ever
    substituting the epoch for Host 0's subject.
    """

    kwargs = {
        "source_ref": source_ref,
        "epoch_ref": epoch_ref,
        "final_source_seq": final_source_seq,
        "complete": complete,
    }
    if sealed_at is not None:
        kwargs["sealed_at"] = sealed_at
    if "subject_ref" in CompletenessSeal.__dataclass_fields__:
        kwargs["subject_ref"] = subject_ref
    return CompletenessSeal(**kwargs)
