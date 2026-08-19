"""The append path synchronizes only the durable tail after cold open."""

from __future__ import annotations

import json
import os

from hanish.past.ledger import Ledger


def _observation(event_id: str, value: bool = True) -> dict:
    return {
        "_kind": "observation",
        "_v": 1,
        "source_ref": "source",
        "event_id": event_id,
        "value": value,
    }


def test_race_loser_receives_the_durable_winner_without_reopen(tmp_path):
    path = tmp_path / "evidence.jsonl"
    first = Ledger(path)
    second = Ledger(path)

    winner = _observation("event-1")
    assert first.sync_observation_once(winner, ("source", "event-1")).appended

    result = second.sync_observation_once(winner, ("source", "event-1"))
    assert not result.appended
    assert not result.conflict
    assert result.winner == winner
    assert result.records == (winner,)


def test_reused_identity_with_different_payload_is_a_conflict(tmp_path):
    ledger = Ledger(tmp_path / "evidence.jsonl")
    ledger.sync_observation_once(_observation("event-1"), ("source", "event-1"))

    result = ledger.sync_observation_once(
        _observation("event-1", value=False), ("source", "event-1")
    )
    assert not result.appended
    assert result.conflict
    assert result.winner == _observation("event-1")
    assert len(list(ledger.raw())) == 1


def test_tail_sync_repairs_a_post_open_torn_write(tmp_path):
    path = tmp_path / "evidence.jsonl"
    ledger = Ledger(path)
    good = _observation("event-1")
    with open(path, "ab") as fh:
        fh.write(json.dumps(good, sort_keys=True).encode() + b"\n{\"torn\"")

    synchronized = ledger.synchronize()
    assert synchronized.records == (good,)
    assert not synchronized.reset
    assert ledger.tail_loss == 1
    assert path.read_bytes().endswith(b"\n")


def test_tail_sync_rejects_complete_non_objects_and_counts_blank_records(tmp_path):
    path = tmp_path / "evidence.jsonl"
    ledger = Ledger(path)
    good = _observation("event-1")
    with open(path, "ab") as fh:
        fh.write(b"[]\n   \n")
        fh.write(json.dumps(good, sort_keys=True).encode() + b"\n")

    synchronized = ledger.synchronize()
    assert synchronized.records == (good,)
    assert not synchronized.reset
    assert ledger.corrupted == 2
    assert ledger.tail_loss == 0
    assert list(ledger.raw()) == [good]

    next_record = _observation("event-2")
    result = ledger.sync_observation_once(next_record, ("source", "event-2"))
    assert result.appended
    assert list(ledger.raw()) == [good, next_record]


def test_same_length_in_place_rewrite_forces_a_visible_generation_reset(tmp_path):
    path = tmp_path / "evidence.jsonl"
    ledger = Ledger(path)
    first = _observation("event-1")
    second = _observation("event-2")
    ledger.append_dict(first)
    original = path.stat()
    replacement = (json.dumps(second, allow_nan=False, sort_keys=True) + "\n").encode()
    assert len(replacement) == original.st_size

    path.write_bytes(replacement)
    os.utime(
        path,
        ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000),
    )
    synchronized = ledger.synchronize()

    assert synchronized.reset
    assert synchronized.records == (second,)
    assert ledger.generation_resets == 1
    assert ledger.snapshot() == (second,)


def test_tail_sync_does_not_repeat_the_cold_rescan(tmp_path, monkeypatch):
    path = tmp_path / "evidence.jsonl"
    seed = Ledger(path)
    for number in range(250):
        payload = _observation(f"seed-{number}")
        seed.sync_observation_once(payload, ("source", f"seed-{number}"))

    ledger = Ledger(path)  # the one permitted O(N) cold snapshot

    def no_full_rescan():
        raise AssertionError("hot-path operation attempted a full rescan")

    monkeypatch.setattr(ledger, "_full_rescan_locked", no_full_rescan)
    for number in range(25):
        payload = _observation(f"new-{number}")
        result = ledger.sync_observation_once(payload, ("source", f"new-{number}"))
        assert result.appended


def test_compare_and_append_enforces_a_monotone_transition(tmp_path):
    ledger = Ledger(tmp_path / "outcomes.jsonl")
    provisional = {"forecast_id": "f1", "terminal": "UNRESOLVABLE"}
    settled = {"forecast_id": "f1", "terminal": "RESOLVED", "verdict": "HIT"}
    competing = {"forecast_id": "f1", "terminal": "RESOLVED", "verdict": "MISS"}

    def allowed(existing: tuple[dict, ...], candidate: dict) -> bool:
        settled_exists = any(item.get("terminal") != "UNRESOLVABLE" for item in existing)
        return not settled_exists and (
            not existing or candidate.get("terminal") != "UNRESOLVABLE"
        )

    assert ledger.compare_and_append(provisional, ("forecast_id",), allowed).appended
    assert ledger.compare_and_append(settled, ("forecast_id",), allowed).appended
    rejected = ledger.compare_and_append(competing, ("forecast_id",), allowed)
    assert not rejected.appended
    assert rejected.conflict
    assert len(list(ledger.raw())) == 2
