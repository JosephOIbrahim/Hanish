"""Immutable experiment-receipt primitives.

This module is deliberately host-neutral and standard-library only.  It knows
how to canonicalize JSON, bind a directory's payload bytes into a non-circular
manifest, and apply the append-only calibration-exclusion policy.  Host
adapters remain responsible for deciding which payloads a receipt requires,
replaying their semantics, and returning a fully validated calibration batch.
The corpus iterator materializes every such batch before it exposes one sample.

``manifest.json`` is not one of its own entries.  Instead ``manifest_root`` is
the SHA-256 of the canonical, sorted entry list.  Git is the long-term
immutability authority; these checks make byte changes and incomplete exports
detectable before an artifact is promoted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

MANIFEST_FILENAME = "manifest.json"
MANIFEST_KIND = "receipt_manifest"
MANIFEST_VERSION = 1
EXCLUSION_KIND = "calibration_exclusion"
EXCLUSION_VERSION = 1
DEFAULT_RECEIPT_ROOT = "experiments/receipts"

_HEX_40_OR_64 = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY_REF = re.compile(r"[a-z][a-z0-9+.-]*:[^\s\\]+\Z")
_FORECAST_ID = re.compile(r"f_[A-Za-z0-9._-]+\Z")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]+\Z")
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9._-]+\Z")


class ReceiptError(ValueError):
    """A receipt or corpus-policy artifact failed closed."""


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ReceiptManifest:
    entries: tuple[ManifestEntry, ...]
    manifest_root: str

    def as_dict(self) -> dict[str, object]:
        return {
            "_kind": MANIFEST_KIND,
            "_v": MANIFEST_VERSION,
            "entries": [entry.as_dict() for entry in self.entries],
            "manifest_root": self.manifest_root,
        }


@dataclass(frozen=True)
class ExclusionCitation:
    commit_sha: str
    git_blob_oid: str
    line_start: int
    line_end: int
    path: str


@dataclass(frozen=True)
class CalibrationExclusion:
    repository_ref: str
    forecast_id: str
    reason_code: str
    reason: str
    raw_ledger_available: bool
    target_record_sha256: str | None
    recorded_at: str
    recorded_by: str
    citations: tuple[ExclusionCitation, ...]
    calibration_eligible: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.repository_ref, self.forecast_id)


@dataclass(frozen=True)
class ExclusionRegistry:
    """A monotone fold: a key can be excluded, never re-included."""

    records: tuple[CalibrationExclusion, ...]

    def excludes(self, repository_ref: str, forecast_id: str) -> bool:
        key = (repository_ref, forecast_id)
        return any(record.key == key for record in self.records)

    def records_for(
        self, repository_ref: str, forecast_id: str
    ) -> tuple[CalibrationExclusion, ...]:
        key = (repository_ref, forecast_id)
        return tuple(record for record in self.records if record.key == key)


@dataclass(frozen=True)
class CalibrationSample:
    """Candidate whose exposure is the adapter-validated structural value."""

    repository_ref: str
    forecast_id: str
    exposure: str
    payload: object

    def __post_init__(self) -> None:
        _matching_string(
            self.repository_ref,
            _REPOSITORY_REF,
            "calibration sample repository_ref",
        )
        _matching_string(
            self.forecast_id,
            _FORECAST_ID,
            "calibration sample forecast_id",
        )
        if self.exposure not in {"BLIND", "EXPOSED"}:
            raise ReceiptError("calibration sample exposure must be BLIND or EXPOSED")

    @property
    def key(self) -> tuple[str, str]:
        return (self.repository_ref, self.forecast_id)


@dataclass(frozen=True)
class CalibrationExposureAmendment:
    """A monotone runtime correction; unexposing is not representable."""

    repository_ref: str
    forecast_id: str
    from_exposure: str = "BLIND"
    to_exposure: str = "EXPOSED"

    def __post_init__(self) -> None:
        _matching_string(
            self.repository_ref,
            _REPOSITORY_REF,
            "calibration amendment repository_ref",
        )
        _matching_string(
            self.forecast_id,
            _FORECAST_ID,
            "calibration amendment forecast_id",
        )
        if self.from_exposure != "BLIND" or self.to_exposure != "EXPOSED":
            raise ReceiptError("the only calibration amendment is BLIND -> EXPOSED")

    @property
    def key(self) -> tuple[str, str]:
        return (self.repository_ref, self.forecast_id)


@dataclass(frozen=True)
class CalibrationBatch:
    """Fully adapter-validated samples and their runtime amendments."""

    samples: tuple[CalibrationSample, ...]
    amendments: tuple[CalibrationExposureAmendment, ...] = ()

    def __post_init__(self) -> None:
        samples = tuple(self.samples)
        amendments = tuple(self.amendments)
        if any(not isinstance(sample, CalibrationSample) for sample in samples):
            raise ReceiptError("calibration batch contains an invalid sample")
        if any(
            not isinstance(amendment, CalibrationExposureAmendment)
            for amendment in amendments
        ):
            raise ReceiptError("calibration batch contains an invalid amendment")
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "amendments", amendments)


class CalibrationProvider(Protocol):
    """Adapter boundary for fail-closed receipt validation and sample loading.

    Providers translate host schemas into structural exposure plus any valid
    runtime amendments; raw caller-provided exposure labels are not sufficient.
    """

    def load_validated_calibration(self) -> CalibrationBatch:
        """Validate the complete input, then return its materialized batch."""


@dataclass(frozen=True)
class PathChange:
    """A version-control path change, independent of any Git subprocess."""

    status: str
    paths: tuple[str, ...]


def canonical_json_bytes(value: object) -> bytes:
    """Return the one accepted JSON representation for signed artifacts."""

    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReceiptError(f"value is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def build_manifest(directory: str | Path) -> ReceiptManifest:
    """Write a new non-circular manifest without overwriting history."""

    root = _directory(directory)
    manifest_path = root / MANIFEST_FILENAME
    if os.path.lexists(manifest_path):
        raise ReceiptError(f"refusing to overwrite {manifest_path}")

    entries = _payload_entries(root)
    if not entries:
        raise ReceiptError("a receipt must contain at least one payload file")
    manifest = _make_manifest(entries)
    payload = canonical_json_bytes(manifest.as_dict()) + b"\n"

    try:
        with manifest_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ReceiptError(f"could not create {manifest_path}: {exc}") from exc
    return manifest


def verify_manifest(
    directory: str | Path, *, require_directory_suffix: bool = True
) -> ReceiptManifest:
    """Verify exact receipt membership and every bound payload byte."""

    root = _directory(directory)
    manifest_path = root / MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReceiptError(f"missing regular {MANIFEST_FILENAME}")
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise ReceiptError(f"could not read {manifest_path}: {exc}") from exc

    value = _strict_json(raw, str(manifest_path))
    if not isinstance(value, dict):
        raise ReceiptError("manifest must be a JSON object")
    _exact_fields(value, {"_kind", "_v", "entries", "manifest_root"}, "manifest")
    if value["_kind"] != MANIFEST_KIND:
        raise ReceiptError("unsupported manifest kind")
    if type(value["_v"]) is not int or value["_v"] != MANIFEST_VERSION:
        raise ReceiptError("unsupported manifest version")
    if raw != canonical_json_bytes(value) + b"\n":
        raise ReceiptError("manifest is not canonical newline-terminated JSON")

    entries_value = value["entries"]
    if not isinstance(entries_value, list) or not entries_value:
        raise ReceiptError("manifest entries must be a non-empty list")
    entries = tuple(
        _manifest_entry_from_dict(entry, index)
        for index, entry in enumerate(entries_value)
    )
    _validate_entry_order(entries)

    manifest_root = value["manifest_root"]
    if not isinstance(manifest_root, str) or not _SHA256.fullmatch(manifest_root):
        raise ReceiptError("manifest_root must be a lowercase SHA-256")
    expected_root = _manifest_root(entries)
    if manifest_root != expected_root:
        raise ReceiptError("manifest_root does not match the canonical entry list")

    actual = _payload_entries(root)
    if actual != entries:
        raise ReceiptError("receipt payload membership, size, or digest differs")
    if require_directory_suffix and not root.name.endswith(f"-{manifest_root[:16]}"):
        raise ReceiptError("receipt directory name does not match manifest_root")
    return ReceiptManifest(entries=entries, manifest_root=manifest_root)


def receipt_directory_name(run_id: str, attempt: int, manifest_root: str) -> str:
    """Return the canonical promoted-directory name for a run attempt."""

    if (
        not isinstance(run_id, str)
        or run_id in {".", ".."}
        or not _SAFE_COMPONENT.fullmatch(run_id)
    ):
        raise ReceiptError("run_id is not a safe directory component")
    if type(attempt) is not int or attempt < 1:
        raise ReceiptError("attempt must be a positive integer")
    if not isinstance(manifest_root, str) or not _SHA256.fullmatch(manifest_root):
        raise ReceiptError("manifest_root must be a lowercase SHA-256")
    return f"{run_id}-{attempt}-{manifest_root[:16]}"


def load_exclusions(path: str | Path) -> ExclusionRegistry:
    """Load the complete policy registry before exposing any sample."""

    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ReceiptError(f"could not read exclusion registry {source}: {exc}") from exc
    return _load_exclusion_bytes(raw, str(source))


def iter_eligible_calibration_samples(
    exclusions: str | Path,
    providers: Iterable[CalibrationProvider],
) -> Iterator[CalibrationSample]:
    """Return eligible samples only after every policy/input validates.

    Receipt semantics stay adapter-owned: a provider must verify its whole
    input before returning a ``CalibrationBatch``.  This function eagerly
    obtains every batch, validates corpus-wide uniqueness, folds all exposure
    amendments, and only then constructs the returned iterator.  Therefore a
    corrupt registry or one corrupt provider prevents every sample from being
    observed by the caller.
    """

    registry = load_exclusions(exclusions)
    try:
        materialized_providers = tuple(providers)
    except Exception as exc:  # noqa: BLE001 - untrusted provider collection
        raise ReceiptError("could not materialize calibration providers") from exc

    batches: list[CalibrationBatch] = []
    for index, provider in enumerate(materialized_providers):
        loader = getattr(provider, "load_validated_calibration", None)
        if not callable(loader):
            raise ReceiptError(f"calibration provider {index} has no validation loader")
        try:
            batch = loader()
        except ReceiptError:
            raise
        except Exception as exc:  # noqa: BLE001 - adapter failures fail closed
            raise ReceiptError(f"calibration provider {index} failed validation") from exc
        if not isinstance(batch, CalibrationBatch):
            raise ReceiptError(f"calibration provider {index} returned an invalid batch")
        batches.append(batch)

    samples = tuple(sample for batch in batches for sample in batch.samples)
    keys = [sample.key for sample in samples]
    if len(keys) != len(set(keys)):
        raise ReceiptError("calibration corpus contains duplicate forecast coordinates")
    exposed = {
        amendment.key
        for batch in batches
        for amendment in batch.amendments
    }
    eligible = tuple(
        sample
        for sample in samples
        if sample.exposure == "BLIND"
        and sample.key not in exposed
        and not registry.excludes(*sample.key)
    )
    return iter(eligible)


def validate_exclusion_prefix(base: bytes, current: bytes) -> ExclusionRegistry:
    """Prove that an exclusion registry changed only by valid appends."""

    _load_exclusion_bytes(base, "base exclusion registry")
    if not current.startswith(base):
        raise ReceiptError("the prior exclusion registry is not an exact byte prefix")
    return _load_exclusion_bytes(current, "current exclusion registry")


def validate_receipt_additions(
    changes: Iterable[PathChange], *, receipt_root: str = DEFAULT_RECEIPT_ROOT
) -> None:
    """Reject edits, moves, or deletions anywhere below the receipt root.

    This pure check is intentionally paired with ``verify_manifest`` for every
    receipt after a change.  An added file in an old directory then fails exact
    manifest membership unless the old manifest is modified, which is itself
    rejected here.
    """

    normalized_root = _relative_path(receipt_root, "receipt_root")
    prefix = normalized_root + "/"
    for change in changes:
        if not isinstance(change, PathChange):
            raise ReceiptError("changes must contain PathChange values")
        if not change.status or not change.paths:
            raise ReceiptError("a path change needs a status and at least one path")
        paths = tuple(_relative_path(path, "changed path") for path in change.paths)
        touches_receipts = any(path == normalized_root or path.startswith(prefix) for path in paths)
        if touches_receipts and (change.status != "A" or len(paths) != 1):
            raise ReceiptError("promoted receipts are add-only")


def _strict_json(raw: bytes, where: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptError(f"{where} is not UTF-8") from exc

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ReceiptError(f"{where} contains duplicate key {key!r}")
            value[key] = item
        return value

    def reject_constant(value):
        raise ReceiptError(f"{where} contains non-finite number {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except ReceiptError:
        raise
    except json.JSONDecodeError as exc:
        raise ReceiptError(f"{where} is not valid JSON: {exc.msg}") from exc


def _load_exclusion_bytes(raw: bytes, where: str) -> ExclusionRegistry:
    if raw and not raw.endswith(b"\n"):
        raise ReceiptError(f"{where} is not newline terminated")
    records = []
    for line_number, line in enumerate(raw.splitlines(keepends=True), start=1):
        if line == b"\n":
            raise ReceiptError(f"{where}:{line_number} is blank")
        body = line[:-1]
        value = _strict_json(body, f"{where}:{line_number}")
        if not isinstance(value, dict):
            raise ReceiptError(f"{where}:{line_number} must be a JSON object")
        if body != canonical_json_bytes(value):
            raise ReceiptError(f"{where}:{line_number} is not canonical JSON")
        records.append(_exclusion_from_dict(value, f"{where}:{line_number}"))
    return ExclusionRegistry(records=tuple(records))


def _exclusion_from_dict(value: dict[str, Any], where: str) -> CalibrationExclusion:
    expected = {
        "_kind",
        "_v",
        "calibration_eligible",
        "citations",
        "forecast_id",
        "raw_ledger_available",
        "reason",
        "reason_code",
        "recorded_at",
        "recorded_by",
        "repository_ref",
        "target_record_sha256",
    }
    _exact_fields(value, expected, where)
    if value["_kind"] != EXCLUSION_KIND:
        raise ReceiptError(f"{where} has an unsupported kind")
    if type(value["_v"]) is not int or value["_v"] != EXCLUSION_VERSION:
        raise ReceiptError(f"{where} has an unsupported version")
    if value["calibration_eligible"] is not False:
        raise ReceiptError(f"{where} may only remove calibration eligibility")

    repository_ref = _matching_string(
        value["repository_ref"], _REPOSITORY_REF, f"{where}.repository_ref"
    )
    forecast_id = _matching_string(
        value["forecast_id"], _FORECAST_ID, f"{where}.forecast_id"
    )
    reason_code = _matching_string(
        value["reason_code"], _REASON_CODE, f"{where}.reason_code"
    )
    reason = _nonempty_string(value["reason"], f"{where}.reason")
    recorded_by = _nonempty_string(value["recorded_by"], f"{where}.recorded_by")
    recorded_at = _aware_timestamp(value["recorded_at"], f"{where}.recorded_at")

    raw_available = value["raw_ledger_available"]
    if type(raw_available) is not bool:
        raise ReceiptError(f"{where}.raw_ledger_available must be a boolean")
    target_digest = value["target_record_sha256"]
    if raw_available:
        if not isinstance(target_digest, str) or not _SHA256.fullmatch(target_digest):
            raise ReceiptError(f"{where} needs the available target record SHA-256")
    elif target_digest is not None:
        raise ReceiptError(f"{where} cannot claim a digest for unavailable raw bytes")

    citations_value = value["citations"]
    if not isinstance(citations_value, list) or not citations_value:
        raise ReceiptError(f"{where}.citations must be a non-empty list")
    citations = tuple(
        _citation_from_dict(citation, f"{where}.citations[{index}]")
        for index, citation in enumerate(citations_value)
    )
    return CalibrationExclusion(
        repository_ref=repository_ref,
        forecast_id=forecast_id,
        calibration_eligible=False,
        reason_code=reason_code,
        reason=reason,
        raw_ledger_available=raw_available,
        target_record_sha256=target_digest,
        recorded_at=recorded_at,
        recorded_by=recorded_by,
        citations=citations,
    )


def _citation_from_dict(value: Any, where: str) -> ExclusionCitation:
    if not isinstance(value, dict):
        raise ReceiptError(f"{where} must be an object")
    _exact_fields(
        value,
        {"commit_sha", "git_blob_oid", "line_end", "line_start", "path"},
        where,
    )
    commit_sha = _matching_string(value["commit_sha"], _HEX_40_OR_64, f"{where}.commit_sha")
    blob_oid = _matching_string(
        value["git_blob_oid"], _HEX_40_OR_64, f"{where}.git_blob_oid"
    )
    line_start = value["line_start"]
    line_end = value["line_end"]
    if type(line_start) is not int or line_start < 1:
        raise ReceiptError(f"{where}.line_start must be a positive integer")
    if type(line_end) is not int or line_end < line_start:
        raise ReceiptError(f"{where}.line_end must not precede line_start")
    return ExclusionCitation(
        commit_sha=commit_sha,
        git_blob_oid=blob_oid,
        line_start=line_start,
        line_end=line_end,
        path=_relative_path(value["path"], f"{where}.path"),
    )


def _make_manifest(entries: tuple[ManifestEntry, ...]) -> ReceiptManifest:
    return ReceiptManifest(entries=entries, manifest_root=_manifest_root(entries))


def _manifest_root(entries: tuple[ManifestEntry, ...]) -> str:
    entry_list = [entry.as_dict() for entry in entries]
    return hashlib.sha256(canonical_json_bytes(entry_list)).hexdigest()


def _manifest_entry_from_dict(value: Any, index: int) -> ManifestEntry:
    where = f"manifest.entries[{index}]"
    if not isinstance(value, dict):
        raise ReceiptError(f"{where} must be an object")
    _exact_fields(value, {"path", "sha256", "size"}, where)
    path = _relative_path(value["path"], f"{where}.path")
    if path == MANIFEST_FILENAME:
        raise ReceiptError("manifest.json cannot include itself")
    size = value["size"]
    if type(size) is not int or size < 0:
        raise ReceiptError(f"{where}.size must be a non-negative integer")
    digest = value["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ReceiptError(f"{where}.sha256 must be a lowercase SHA-256")
    return ManifestEntry(path=path, size=size, sha256=digest)


def _payload_entries(root: Path) -> tuple[ManifestEntry, ...]:
    paths: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReceiptError(f"receipt contains symlink {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ReceiptError(f"receipt contains non-regular payload {path}")
        relative = _relative_path(path.relative_to(root).as_posix(), "payload path")
        if relative == MANIFEST_FILENAME:
            continue
        paths.append((relative, path))

    paths.sort(key=lambda item: item[0])
    lowered: set[str] = set()
    entries = []
    for relative, path in paths:
        folded = relative.casefold()
        if folded in lowered:
            raise ReceiptError(f"case-ambiguous payload path {relative}")
        lowered.add(folded)
        size, digest = _stable_file_digest(path)
        entries.append(ManifestEntry(path=relative, size=size, sha256=digest))
    return tuple(entries)


def _stable_file_digest(path: Path) -> tuple[int, str]:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise ReceiptError(f"could not hash {path}: {exc}") from exc
    identity_before = (before.st_size, before.st_mtime_ns)
    identity_after = (after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or size != after.st_size:
        raise ReceiptError(f"payload changed while hashing: {path}")
    return size, digest.hexdigest()


def _validate_entry_order(entries: tuple[ManifestEntry, ...]) -> None:
    paths = [entry.path for entry in entries]
    if paths != sorted(paths):
        raise ReceiptError("manifest entries are not sorted by path")
    if len(paths) != len(set(paths)) or len(paths) != len({path.casefold() for path in paths}):
        raise ReceiptError("manifest contains duplicate or case-ambiguous paths")


def _directory(value: str | Path) -> Path:
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise ReceiptError(f"receipt root is not a regular directory: {path}")
    return path


def _relative_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ReceiptError(f"{where} must be a normalized POSIX relative path")
    path = PurePosixPath(value)
    invalid_part = any(part in {"", ".", ".."} for part in path.parts)
    if path.is_absolute() or path.as_posix() != value or invalid_part:
        raise ReceiptError(f"{where} must be a normalized POSIX relative path")
    return value


def _exact_fields(value: dict[str, Any], expected: set[str], where: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReceiptError(f"{where} fields differ; missing={missing}, extra={extra}")


def _matching_string(value: Any, pattern: re.Pattern[str], where: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ReceiptError(f"{where} has an invalid value")
    return value


def _nonempty_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptError(f"{where} must be a non-empty string")
    return value


def _aware_timestamp(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ReceiptError(f"{where} must be an ISO 8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReceiptError(f"{where} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReceiptError(f"{where} must be timezone-aware")
    return value
