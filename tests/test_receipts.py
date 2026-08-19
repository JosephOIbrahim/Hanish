"""Generic immutable-receipt and calibration-policy guarantees."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hanish.receipts import (
    CalibrationBatch,
    CalibrationExposureAmendment,
    CalibrationSample,
    PathChange,
    ReceiptError,
    build_manifest,
    canonical_json_bytes,
    iter_eligible_calibration_samples,
    load_exclusions,
    receipt_directory_name,
    validate_exclusion_prefix,
    validate_receipt_additions,
    verify_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
EXCLUSIONS = ROOT / "experiments" / "calibration-exclusions.jsonl"
HISTORICAL_KEY = ("github:JosephOIbrahim/Hanish", "f_c57a1c3bc61f")


class StubProvider:
    def __init__(self, batch=None, *, error=None, calls=None, name="provider"):
        self.batch = batch
        self.error = error
        self.calls = calls if calls is not None else []
        self.name = name

    def load_validated_calibration(self):
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error
        return self.batch


def _exclusion_value() -> dict:
    return json.loads(EXCLUSIONS.read_text(encoding="utf-8"))


def _exclusion_line(value: dict | None = None) -> bytes:
    return canonical_json_bytes(value or _exclusion_value()) + b"\n"


def _receipt(tmp_path: Path):
    staging = tmp_path / "staging"
    (staging / "ledgers").mkdir(parents=True)
    (staging / "receipt.json").write_bytes(canonical_json_bytes({"run": "481"}) + b"\n")
    (staging / "ledgers" / "evidence.jsonl").write_bytes(b'{"event":1}\n')
    manifest = build_manifest(staging)
    promoted = tmp_path / receipt_directory_name("481", 2, manifest.manifest_root)
    staging.rename(promoted)
    return promoted, manifest


def test_canonical_json_is_stable_and_rejects_non_finite_numbers():
    assert canonical_json_bytes({"z": 1, "a": "é"}) == b'{"a":"\xc3\xa9","z":1}'
    with pytest.raises(ReceiptError, match="canonical JSON"):
        canonical_json_bytes({"bad": float("nan")})


def test_historical_exclusion_is_strict_and_effective_without_raw_digest():
    registry = load_exclusions(EXCLUSIONS)
    assert registry.excludes(*HISTORICAL_KEY)
    record = registry.records_for(*HISTORICAL_KEY)[0]
    assert record.calibration_eligible is False
    assert record.reason_code == "AUTHOR_CAPABLE_OF_MOVING_TARGET"
    assert record.raw_ledger_available is False
    assert record.target_record_sha256 is None
    assert len(record.citations) == 5


def test_calibration_corpus_validates_every_input_before_returning_an_iterator():
    calls = []
    excluded = CalibrationSample(*HISTORICAL_KEY, exposure="BLIND", payload="old")
    eligible = CalibrationSample(
        "github:JosephOIbrahim/Hanish",
        "f_independent",
        exposure="BLIND",
        payload="new",
    )
    providers = [
        StubProvider(CalibrationBatch((excluded,)), calls=calls, name="first"),
        StubProvider(CalibrationBatch((eligible,)), calls=calls, name="second"),
    ]

    iterator = iter_eligible_calibration_samples(EXCLUSIONS, providers)

    assert calls == ["first", "second"]
    assert list(iterator) == [eligible]


def test_one_corrupt_calibration_input_blocks_the_whole_corpus():
    sample = CalibrationSample(
        "github:JosephOIbrahim/Hanish",
        "f_would_have_been_eligible",
        exposure="BLIND",
        payload=True,
    )
    providers = [
        StubProvider(CalibrationBatch((sample,))),
        StubProvider(error=ReceiptError("corrupt receipt")),
    ]

    with pytest.raises(ReceiptError, match="corrupt receipt"):
        iter_eligible_calibration_samples(EXCLUSIONS, providers)


def test_exposed_and_monotonically_amended_samples_never_enter_calibration():
    repository = "github:JosephOIbrahim/Hanish"
    structurally_exposed = CalibrationSample(
        repository,
        "f_exposed",
        exposure="EXPOSED",
        payload=1,
    )
    amended = CalibrationSample(
        repository,
        "f_amended",
        exposure="BLIND",
        payload=2,
    )
    batch = CalibrationBatch(
        (structurally_exposed, amended),
        (CalibrationExposureAmendment(repository, amended.forecast_id),),
    )

    assert list(
        iter_eligible_calibration_samples(EXCLUSIONS, [StubProvider(batch)])
    ) == []
    with pytest.raises(ReceiptError, match="only calibration amendment"):
        CalibrationExposureAmendment(
            repository,
            amended.forecast_id,
            from_exposure="EXPOSED",
            to_exposure="BLIND",
        )


def test_digestless_historical_exclusion_matches_repository_and_forecast():
    sample = CalibrationSample(*HISTORICAL_KEY, exposure="BLIND", payload={"score": 1})
    batch = CalibrationBatch((sample,))

    assert list(
        iter_eligible_calibration_samples(EXCLUSIONS, [StubProvider(batch)])
    ) == []


def test_malformed_registry_blocks_corpus_before_any_provider_is_loaded(tmp_path):
    malformed = tmp_path / "calibration-exclusions.jsonl"
    malformed.write_bytes(b'{"_kind":"calibration_exclusion"}')
    calls = []
    provider = StubProvider(CalibrationBatch(()), calls=calls)

    with pytest.raises(ReceiptError, match="newline terminated"):
        iter_eligible_calibration_samples(malformed, [provider])
    assert calls == []


def test_exclusion_fold_is_monotone_for_later_records():
    first = _exclusion_line()
    later = _exclusion_value()
    later["recorded_at"] = "2026-08-19T20:41:00Z"
    later["reason"] = "A later policy record adds context without restoring eligibility."
    registry = validate_exclusion_prefix(first, first + _exclusion_line(later))
    assert registry.excludes(*HISTORICAL_KEY)
    assert len(registry.records_for(*HISTORICAL_KEY)) == 2


@pytest.mark.parametrize(
    "current",
    [
        b"{}",
        b"{}\n",
        b'{"_kind":"x","_kind":"y"}\n',
    ],
)
def test_exclusion_registry_rejects_incomplete_or_ambiguous_records(current):
    with pytest.raises(ReceiptError):
        validate_exclusion_prefix(b"", current)


def test_exclusion_registry_rejects_rewrite_truncation_and_reinclusion():
    base = _exclusion_line()
    edited = base.replace(b"AUTHOR_CAPABLE", b"AUTHOR_POSSIBLY", 1)
    with pytest.raises(ReceiptError, match="exact byte prefix"):
        validate_exclusion_prefix(base, edited)
    with pytest.raises(ReceiptError):
        validate_exclusion_prefix(base, base[:-1])

    reincluded = _exclusion_value()
    reincluded["calibration_eligible"] = True
    with pytest.raises(ReceiptError, match="only remove"):
        validate_exclusion_prefix(base, base + _exclusion_line(reincluded))


def test_exclusion_registry_rejects_bool_version_and_unknown_fields():
    value = _exclusion_value()
    value["_v"] = True
    with pytest.raises(ReceiptError, match="version"):
        validate_exclusion_prefix(b"", _exclusion_line(value))

    value = _exclusion_value()
    value["surprise"] = "ignored fields are not allowed"
    with pytest.raises(ReceiptError, match="fields differ"):
        validate_exclusion_prefix(b"", _exclusion_line(value))


def test_manifest_is_non_circular_deterministic_and_non_mutating(tmp_path):
    receipt, built = _receipt(tmp_path)
    manifest_before = (receipt / "manifest.json").read_bytes()
    payload_before = (receipt / "receipt.json").read_bytes()
    names_before = sorted(path.relative_to(receipt) for path in receipt.rglob("*"))

    verified = verify_manifest(receipt)

    assert verified == built
    assert all(entry.path != "manifest.json" for entry in verified.entries)
    assert (receipt / "manifest.json").read_bytes() == manifest_before
    assert (receipt / "receipt.json").read_bytes() == payload_before
    assert sorted(path.relative_to(receipt) for path in receipt.rglob("*")) == names_before


def test_manifest_root_does_not_depend_on_file_creation_order(tmp_path):
    roots = []
    for name, order in (("one", ("a.txt", "b.txt")), ("two", ("b.txt", "a.txt"))):
        directory = tmp_path / name
        directory.mkdir()
        for filename in order:
            (directory / filename).write_text(filename, encoding="utf-8")
        roots.append(build_manifest(directory).manifest_root)
    assert roots[0] == roots[1]


def test_manifest_detects_tampering_and_extra_payload(tmp_path):
    receipt, _ = _receipt(tmp_path)
    (receipt / "receipt.json").write_bytes(b'{"run":"changed"}\n')
    with pytest.raises(ReceiptError, match="payload membership"):
        verify_manifest(receipt)

    receipt, _ = _receipt(tmp_path / "second")
    (receipt / "unlisted.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ReceiptError, match="payload membership"):
        verify_manifest(receipt)


def test_manifest_requires_canonical_bytes_and_matching_directory(tmp_path):
    receipt, _ = _receipt(tmp_path)
    manifest_path = receipt / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    with pytest.raises(ReceiptError, match="canonical"):
        verify_manifest(receipt)

    receipt, _ = _receipt(tmp_path / "second")
    other = tmp_path / "wrong-name"
    receipt.rename(other)
    with pytest.raises(ReceiptError, match="directory name"):
        verify_manifest(other)
    verify_manifest(other, require_directory_suffix=False)


def test_manifest_builder_refuses_to_overwrite(tmp_path):
    receipt, _ = _receipt(tmp_path)
    with pytest.raises(ReceiptError, match="refusing to overwrite"):
        build_manifest(receipt)


def test_receipt_directory_name_rejects_ambiguous_coordinates():
    root = "a" * 64
    assert receipt_directory_name("481", 2, root) == f"481-2-{root[:16]}"
    with pytest.raises(ReceiptError):
        receipt_directory_name("../481", 2, root)
    with pytest.raises(ReceiptError):
        receipt_directory_name("..", 2, root)
    with pytest.raises(ReceiptError):
        receipt_directory_name("481", True, root)


def test_receipt_history_allows_only_new_paths():
    validate_receipt_additions(
        [PathChange("A", ("experiments/receipts/481-1-deadbeef/file.json",))]
    )
    validate_receipt_additions([PathChange("M", ("README.md",))])

    rejected = [
        PathChange("M", ("experiments/receipts/481-1-deadbeef/file.json",)),
        PathChange("D", ("experiments/receipts/481-1-deadbeef/file.json",)),
        PathChange(
            "R100",
            (
                "experiments/receipts/481-1-deadbeef/file.json",
                "experiments/receipts/481-1-deadbeef/moved.json",
            ),
        ),
    ]
    for change in rejected:
        with pytest.raises(ReceiptError, match="add-only"):
            validate_receipt_additions([change])
