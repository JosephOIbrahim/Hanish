"""The adversarial conformance suite -- the guardian's verdict, pinned.

Every finding the guardian verified with a live probe against v0.1.0 gets a
test here that would have caught the release. Each test name is the law it
defends, so a regression reads as a broken law, not a broken test.

The three P0s, verbatim from the verdict:

  P0-1  semantically-broken but JSON-valid records raised out of
        Substrate.__init__, bricking every future reopen.
  P0-2  a garbage arrived_at / validity sat outside the per-observation
        guard, aborting the WHOLE process() pass -- and, since the poison
        persists, every later pass too. Permanent denial of resolution.
  P0-3  a seal from a source that never emits the forecast's observable
        still completed its stream, turning absence into MISS.

And the mediums/lows they rode in with: M-1 repair-truncate vs a concurrent
writer, M-2 forecasts/outcomes untagged and un-gated, M-3 UNRESOLVABLE
never reopened by late-but-in-time evidence, L-1 status(at), L-2 whitespace
torn tail, L-4 dead code.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hanish import Substrate
from hanish.adapters.ci import REQUIRED_CHECKS_PASS, CIAdapter
from hanish.future.claims import Comparator, Exposure, Forecast, ResolutionSpec
from hanish.past.events import (
    CompletenessSeal,
    ObservationEvent,
    Terminal,
    Verdict,
)
from hanish.past.ledger import Ledger

SHA = "abc123"
PAST = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
LATER = (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def build(tmp_path, horizon=LATER):
    ci = CIAdapter()
    sub = Substrate(tmp_path, observables=ci.observable_specs())
    f = Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="guardian target",
        probability=0.7,
        exposure=Exposure.BLIND,
        world_ref=ci.world_ref(SHA, "wf1", "lock1"),
        world_ref_capability=ci.world_ref_capability,
        resolution=ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=horizon,
        ),
    )
    return ci, sub, f


def in_time(ci, value=True, hours_before=2):
    """An observation that ARRIVED (processing-wise) late but is still
    within its horizon (arrived_at before it expired)."""
    return ObservationEvent(
        source_ref="github-actions",
        event_id="run-481:attempt-1:required_checks",
        subject_ref=ci.subject_ref(SHA),
        observable=REQUIRED_CHECKS_PASS,
        value=value,
        source_seq=1,
        epoch_ref=ci.subject_ref(SHA),
        arrived_at=(datetime.now(UTC) - timedelta(hours=hours_before)).isoformat(),
    )


# ---------------------------------------------------------------------------
# P0-1 -- a damaged record never bricks a rebuild
# ---------------------------------------------------------------------------

def test_semantically_broken_record_never_bricks_reopen(tmp_path):
    """A record that parses as JSON but is not a valid forecast / observation
    / outcome used to raise out of Substrate.__init__. One hostile record on
    any ledger bricked every future reopen. Now: skipped, counted, and the
    rest of the ledger keeps working."""
    ci, sub0, f = build(tmp_path)
    fid = sub0.author(f)

    # valid JSON, invalid record -- one per ledger
    with open(sub0.forecasts_l.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "subject_ref": "git:x", "claim": "broken", "probability": 0.5,
            "resolution": {"observable": REQUIRED_CHECKS_PASS,
                           "threshold": True,
                           "horizon": "2099-01-01T00:00:00+00:00"},
            "exposure": "BLIND",          # missing comparator + accept_validity
        }) + "\n")
    with open(sub0.evidence_l.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "_kind": "observation", "_v": 1,
            "source_ref": "x", "event_id": "e_bad", "subject_ref": "git:s",
            "observable": REQUIRED_CHECKS_PASS, "value": True,
            "validity": "GARBAGE",        # not a Validity
        }) + "\n")
    with open(sub0.outcomes_l.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "forecast_id": "f_bad", "terminal": "BOGUS",   # not a Terminal
        }) + "\n")

    reopened = Substrate(tmp_path, observables=ci.observable_specs())  # no raise
    assert fid in reopened.forecasts                    # the good record survived
    assert reopened.status()["capture"]["corrupted"] >= 3

    # and the reopen is fully functional, not just not-dead
    reopened.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=True))
    out = reopened.process()
    assert out[0].forecast_id == fid
    assert out[0].verdict is Verdict.HIT


# ---------------------------------------------------------------------------
# P0-2 -- a poison record cannot deny resolution
# ---------------------------------------------------------------------------

def test_poison_record_cannot_deny_resolution(tmp_path):
    """An unparseable arrived_at / validity used to abort the WHOLE process()
    pass -- and, since the poison persisted, every later pass died too. Now:
    counted once (invalid_compare), and a good observation still scores."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    sub.capture(ObservationEvent(
        source_ref="x", event_id="e_poison",
        subject_ref=ci.subject_ref(SHA),
        observable=REQUIRED_CHECKS_PASS, value=True,
        arrived_at="not-a-timestamp",
    ))
    sub.capture(ObservationEvent(
        source_ref="x", event_id="e_poison2",
        subject_ref=ci.subject_ref(SHA),
        observable=REQUIRED_CHECKS_PASS, value=True,
        validity="GARBAGE",
    ))

    assert sub.process() == []                  # neither poison scores
    assert sub.process_errors == 0              # not a denial of resolution
    assert sub.invalid_compare == 2             # both counted, fail-closed

    sub.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=True))
    out = sub.process()
    assert out[0].verdict is Verdict.HIT        # good evidence still resolves


# ---------------------------------------------------------------------------
# P0-3 -- completeness is scoped to the observable's channel
# ---------------------------------------------------------------------------

def test_seal_from_an_unrelated_source_cannot_close_my_stream(tmp_path):
    """A seal used to complete ANY epoch-matching stream. A foreign source
    closing its own channel for this epoch turned our missing checks into a
    MISS. Now: the seal must come from a source that actually emits the
    forecast's observable, and otherwise absence stays UNRESOLVABLE."""
    ci = CIAdapter()
    sub = Substrate(tmp_path, observables=ci.observable_specs())

    attacked = Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="checks will pass",
        probability=0.6,
        exposure=Exposure.BLIND,
        resolution=ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS, comparator=Comparator.EQ,
            threshold=True, horizon=PAST,
        ),
    )
    aid = sub.author(attacked)

    # a foreign source genuinely seals and emits its OWN stream, no gaps
    sub.capture(CompletenessSeal(
        source_ref="other-source", epoch_ref=ci.subject_ref(SHA),
        final_source_seq=1,
    ))
    sub.capture(ObservationEvent(
        source_ref="other-source", event_id="d1",
        subject_ref=ci.subject_ref(SHA), epoch_ref=ci.subject_ref(SHA),
        observable="ci.duration_s", value=41.0, source_seq=1,
    ))

    sub.process()
    assert sub.outcomes[aid].terminal is Terminal.UNRESOLVABLE   # fail closed
    assert sub.outcomes[aid].verdict is None                     # never MISS

    # the honest control: the channel's OWN source seals it empty -> MISS
    control = Forecast(
        subject_ref=ci.subject_ref("f00d"),
        claim="own channel passes",
        probability=0.5,
        exposure=Exposure.BLIND,
        resolution=ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS, comparator=Comparator.EQ,
            threshold=True, horizon=PAST,
        ),
    )
    cid = sub.author(control)
    sub.capture(CompletenessSeal(
        source_ref="github-actions", epoch_ref=ci.subject_ref("f00d"),
        final_source_seq=0,
    ))
    sub.process()
    assert sub.outcomes[cid].verdict is Verdict.MISS


# ---------------------------------------------------------------------------
# M-2 -- the schema gate covers every ledger
# ---------------------------------------------------------------------------

def test_future_version_on_forecast_or_outcome_fails_loud(tmp_path):
    """M-2: forecasts/outcomes carried no _v tag and read as v1 forever, so a
    future writer on those ledgers could be silently misread. Now every ledger
    is tagged and gated -- and a newer version still fails loud."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    with open(sub.forecasts_l.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "_v": 99, "claim": "future", "probability": 0.5,
            "subject_ref": "git:s", "exposure": "BLIND",
        }) + "\n")
    with pytest.raises(ValueError, match="schema"):
        Substrate(tmp_path, observables=ci.observable_specs())


def test_forecasts_and_outcomes_are_schema_tagged(tmp_path):
    ci, sub, f = build(tmp_path)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", 1, passed=True))
    sub.process()

    for name in ("forecasts", "outcomes"):
        first = (sub.root / f"{name}.jsonl").read_text().splitlines()[0]
        assert json.loads(first)["_v"] == 1


# ---------------------------------------------------------------------------
# M-3 -- UNRESOLVABLE is a housekeeping closure, not a verdict
# ---------------------------------------------------------------------------

def test_unresolvable_is_reopened_by_in_time_evidence(tmp_path):
    ci, sub, f = build(tmp_path, horizon=PAST)
    fid = sub.author(f)

    sub.process()                                  # nothing arrived -> UNRESOLVABLE
    assert sub.outcomes[fid].terminal is Terminal.UNRESOLVABLE

    # evidence arrives late processing-wise but IN TIME (before the horizon)
    sub.capture(in_time(ci))
    out = sub.process()
    assert sub.outcomes[fid].verdict is Verdict.HIT
    assert out[-1].forecast_id == fid


def test_settled_miss_is_never_reopened(tmp_path):
    ci, sub, f = build(tmp_path, horizon=PAST)
    fid = sub.author(f)

    sub.capture(CompletenessSeal(
        source_ref="github-actions", epoch_ref=ci.subject_ref(SHA),
        final_source_seq=0,
    ))
    sub.process()
    assert sub.outcomes[fid].verdict is Verdict.MISS

    sub.capture(in_time(ci))
    sub.process()
    assert sub.outcomes[fid].verdict is Verdict.MISS     # the first verdict stands


# ---------------------------------------------------------------------------
# M-1 / L-2 -- the ledger under contention and a whitespace torn tail
# ---------------------------------------------------------------------------

def test_concurrent_append_after_torn_tail_loses_nothing(tmp_path):
    """Repair used to read and then truncate without the lock: a writer that
    appended between the read and the truncate had its record eaten. The
    whole pass now runs under the append lock, so a torn tail cannot kill a
    concurrent append."""
    p = tmp_path / "concurrent.jsonl"
    p.write_text('{"i":0}\n{"torn', encoding="utf-8")

    def work(base):
        ledger = Ledger(p)                  # construction repairs under lock
        for i in range(50):
            ledger.append_dict({"n": base + i})

    a = threading.Thread(target=work, args=(0,))
    b = threading.Thread(target=work, args=(100,))
    a.start()
    b.start()
    a.join()
    b.join()

    recs = list(Ledger(p).raw())
    assert len(recs) == 1 + 100              # the survivor + both writers' appends
    values = {r["n"] for r in recs if "n" in r}
    assert values == set(range(0, 50)) | set(range(100, 150)) | {0}


def test_whitespace_tail_is_a_torn_tail(tmp_path):
    """A write that died while emitting whitespace leaves a partial final
    line -- a torn tail in every sense that matters. Truncated and counted,
    never left to sit at the end of the file."""
    p = tmp_path / "torn.jsonl"
    p.write_text('{"a":1}\n   ', encoding="utf-8")

    ledger = Ledger(p)
    assert ledger.tail_loss == 1
    assert len(ledger) == 1
    assert p.read_text(encoding="utf-8").strip() == '{"a":1}'


# ---------------------------------------------------------------------------
# L-1 -- status() swallows a broken clock
# ---------------------------------------------------------------------------

def test_status_does_not_raise_on_a_bad_at(tmp_path):
    ci, sub, f = build(tmp_path)
    sub.author(f)
    s = sub.status(at="not-a-timestamp")
    assert "closure_rate" in s
