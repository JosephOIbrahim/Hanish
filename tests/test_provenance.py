"""Published prose must not outrun the evidence it cites."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_readme_labels_the_fixed_baseline_without_a_mutable_live_count():
    readme = _read("README.md")
    prose = " ".join(readme.split())
    assert "v0.1.2 baseline: 41" in readme
    assert "31 tests, all green" not in readme
    assert "published calibration corpus therefore contains zero eligible samples" in prose


def test_harness_and_digest_retract_autonomous_ci_and_blindness_claims():
    harness = _read("HARNESS.md")
    digest = _read("harness/digest.md")
    digest_prose = " ".join(digest.split())
    assert "CI proved the loop on a real external stream" not in harness
    assert "synthetic `flight-past`" in harness
    assert "**6. Provenance amendment.**" in digest
    assert "not an autonomous CI capture" in digest_prose
    assert "no forecast, evidence, outcome, timestamp, digest, or receipt is" in digest_prose
    assert "zero eligible samples" in digest_prose


def test_program_distinguishes_runtime_ledgers_from_promoted_receipts():
    program = _read("harness/program.md")
    assert "Never edit a runtime ledger by hand" in program
    assert "sealed, hashed receipt export may be promoted once" in program
    assert "append-only" in program
    assert "calibration-exclusions.jsonl" in program


def test_prospect_rename_is_explained_without_rewriting_historical_input():
    readme = _read("README.md")
    historical_prompt = _read("harness/waves/adjudication-wave.js")
    assert "formerly called **Prospect** is now **Hanish**" in readme
    assert "Historical wave prompts retain the former name" in readme
    assert "Moneta + Octa + Prospect" in historical_prompt
