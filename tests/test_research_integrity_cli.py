"""Fail-closed Git policy checks for promoted research history."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import hanish.adapters.ci_cli as ci_cli
from hanish.adapters.ci_cli import main
from hanish.receipts import ReceiptError, canonical_json_bytes, validate_receipt_additions

ROOT = Path(__file__).resolve().parents[1]
EXCLUSION = "experiments/calibration-exclusions.jsonl"
RECEIPTS = "experiments/receipts"


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _record(forecast_id: str) -> dict:
    value = json.loads((ROOT / EXCLUSION).read_text(encoding="utf-8"))
    value["forecast_id"] = forecast_id
    return value


def _registry(*forecast_ids: str) -> bytes:
    return b"".join(canonical_json_bytes(_record(value)) + b"\n" for value in forecast_ids)


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path, registry: bytes | None = None) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Hanish Tests")
    _git(repository, "config", "user.email", "hanish@example.invalid")
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    if registry is not None:
        path = repository / EXCLUSION
        path.parent.mkdir(parents=True)
        path.write_bytes(registry)
    return repository, _commit(repository, "base")


def _check(repository: Path, base: str, head: str, *, event: str = "push") -> int:
    base_option = (
        ["--pull-request-base-sha", base]
        if event == "pull_request"
        else ["--push-before-sha", base]
    )
    return main(
        [
            "check-research-integrity",
            "--repository-root",
            str(repository),
            "--event-name",
            event,
            *base_option,
            "--default-branch",
            "main",
            "--head",
            head,
        ]
    )


def test_name_status_parser_allows_add_but_rejects_every_mutating_status():
    root = f"{RECEIPTS}/481-1-deadbeef"
    payload = (
        f"A\0{root}/added\0"
        f"M\0{root}/modified\0"
        f"D\0{root}/deleted\0"
        f"R100\0{root}/old\0{root}/renamed\0"
        f"C100\0{root}/source\0{root}/copied\0"
        f"T\0{root}/type\0"
    ).encode()
    changes = ci_cli._parse_name_status(payload)
    validate_receipt_additions(changes[:1])
    assert [change.status for change in changes] == ["A", "M", "D", "R100", "C100", "T"]
    for change in changes[1:]:
        with pytest.raises(ReceiptError, match="add-only"):
            validate_receipt_additions([change])


def test_first_registry_and_new_receipt_paths_are_allowed_and_semantically_checked(
    tmp_path,
    monkeypatch,
):
    repository, base = _repository(tmp_path)
    exclusion = repository / EXCLUSION
    exclusion.parent.mkdir(parents=True)
    exclusion.write_bytes(_registry("f_first"))
    receipt = repository / RECEIPTS / "candidate"
    receipt.mkdir(parents=True)
    (receipt / "payload.json").write_text("{}\n", encoding="utf-8")
    head = _commit(repository, "add research artifacts")
    seen = []
    monkeypatch.setattr("hanish.adapters.ci_receipt.verify_receipt", seen.append)
    assert _check(repository, base, head) == 0
    assert seen == [receipt]


def test_full_push_range_detects_an_earlier_receipt_edit(tmp_path, monkeypatch):
    repository, _ = _repository(tmp_path, _registry("f_first"))
    receipt = repository / RECEIPTS / "old" / "payload.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("first\n", encoding="utf-8")
    base = _commit(repository, "add receipt fixture")
    receipt.write_text("changed\n", encoding="utf-8")
    _commit(repository, "mutate receipt")
    (repository / "README.md").write_text("later\n", encoding="utf-8")
    head = _commit(repository, "later unrelated commit")
    monkeypatch.setattr(ci_cli, "_verify_receipt_tree", lambda _root: None)
    assert _check(repository, base, head) == 1


def test_mutate_then_revert_cannot_hide_inside_a_multi_commit_push(tmp_path, monkeypatch):
    repository, _ = _repository(tmp_path, _registry("f_first"))
    receipt = repository / RECEIPTS / "old" / "payload.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("authoritative\n", encoding="utf-8")
    base = _commit(repository, "authoritative receipt")
    receipt.write_text("mutated\n", encoding="utf-8")
    _commit(repository, "mutate receipt")
    receipt.write_text("authoritative\n", encoding="utf-8")
    head = _commit(repository, "restore receipt bytes")
    monkeypatch.setattr(ci_cli, "_verify_receipt_tree", lambda _root: None)
    assert _check(repository, base, head) == 1


def test_side_branch_mutate_revert_is_visible_through_a_merge(tmp_path, monkeypatch):
    repository, _ = _repository(tmp_path, _registry("f_first"))
    receipt = repository / RECEIPTS / "old" / "payload.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("authoritative\n", encoding="utf-8")
    base = _commit(repository, "authoritative receipt")

    _git(repository, "switch", "--create", "side")
    receipt.write_text("mutated\n", encoding="utf-8")
    _commit(repository, "side mutation")
    receipt.write_text("authoritative\n", encoding="utf-8")
    _commit(repository, "side restore")

    _git(repository, "switch", "main")
    (repository / "README.md").write_text("main change\n", encoding="utf-8")
    _commit(repository, "main change")
    _git(repository, "merge", "--no-ff", "side", "-m", "merge side")
    head = _git(repository, "rev-parse", "HEAD")
    monkeypatch.setattr(ci_cli, "_verify_receipt_tree", lambda _root: None)
    assert _check(repository, base, head) == 1


def test_pr_and_push_select_their_distinct_authoritative_bases(tmp_path):
    repository, first = _repository(tmp_path, _registry("f_first"))
    (repository / "README.md").write_text("second\n", encoding="utf-8")
    second = _commit(repository, "second")
    (repository / "README.md").write_text("third\n", encoding="utf-8")
    head = _commit(repository, "third")
    assert ci_cli._select_base(
        repository,
        event_name="pull_request",
        explicit_base=None,
        pull_request_base=first,
        push_before=second,
        default_branch="main",
        head=head,
    ) == first
    assert ci_cli._select_base(
        repository,
        event_name="push",
        explicit_base=None,
        pull_request_base=first,
        push_before=second,
        default_branch="main",
        head=head,
    ) == second


@pytest.mark.parametrize("missing", [None, "", "0" * 40])
def test_branch_creation_base_falls_back_to_origin_default(tmp_path, missing):
    repository, base = _repository(tmp_path, _registry("f_first"))
    _git(repository, "update-ref", "refs/remotes/origin/main", base)
    (repository / "README.md").write_text("branch\n", encoding="utf-8")
    head = _commit(repository, "branch commit")
    assert ci_cli._select_base(
        repository,
        event_name="push",
        explicit_base=None,
        pull_request_base=None,
        push_before=missing,
        default_branch="main",
        head=head,
    ) == base


@pytest.mark.parametrize("mode", ["append", "truncate", "reorder", "invalid_suffix"])
def test_exclusion_history_is_an_exact_valid_byte_prefix(tmp_path, mode):
    first = canonical_json_bytes(_record("f_first")) + b"\n"
    second = canonical_json_bytes(_record("f_second")) + b"\n"
    base_bytes = first if mode in {"append", "invalid_suffix"} else first + second
    repository, base = _repository(tmp_path, base_bytes)
    current = {
        "append": first + second,
        "truncate": first,
        "reorder": second + first,
        "invalid_suffix": first + b'{"broken":true}\n',
    }[mode]
    (repository / EXCLUSION).write_bytes(current)
    head = _commit(repository, mode)
    expected = 0 if mode == "append" else 1
    assert _check(repository, base, head) == expected


def test_invalid_unchanged_registry_and_corrupt_receipt_fail_without_traceback(
    tmp_path,
    capsys,
):
    repository, base = _repository(tmp_path, b'{"broken":true}\n')
    corrupt = repository / RECEIPTS / "corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "manifest.json").write_text("not-json\n", encoding="utf-8")
    head = _commit(repository, "corrupt history")
    assert _check(repository, head, head) == 1
    error = capsys.readouterr().err
    assert error.startswith("hanish-ci: ")
    assert "Traceback" not in error
    assert _check(repository, base, head) == 1


def test_corrupt_receipt_and_loose_receipt_file_each_block_unchanged_history(tmp_path):
    repository, _ = _repository(tmp_path, _registry("f_first"))
    corrupt = repository / RECEIPTS / "corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "manifest.json").write_text("not-json\n", encoding="utf-8")
    head = _commit(repository, "corrupt receipt")
    assert _check(repository, head, head) == 1

    shutil.rmtree(corrupt)
    loose = repository / RECEIPTS / "loose.json"
    loose.write_text("{}\n", encoding="utf-8")
    head = _commit(repository, "loose receipt file")
    assert _check(repository, head, head) == 1


def test_git_is_invoked_with_an_argument_vector_and_never_a_shell(tmp_path, monkeypatch):
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, b"ok", b"")

    monkeypatch.setattr(ci_cli.subprocess, "run", fake_run)
    result = ci_cli._run_git(tmp_path, ["status", "--porcelain=v1"])
    assert result.stdout == b"ok"
    assert isinstance(calls[0][0], list)
    assert calls[0][1]["shell"] is False


def test_malformed_nul_diff_fails_closed():
    with pytest.raises(ValueError, match="torn tail"):
        ci_cli._parse_name_status(b"A\0path")
    with pytest.raises(ValueError, match="omitted a path"):
        ci_cli._parse_name_status(b"R100\0old\0")
