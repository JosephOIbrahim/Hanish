"""G2 -- a torn tail may not brick the substrate.

Flight PAST found P3: a single partial final line made reopen raise
JSONDecodeError. The ledger is the source of truth; a crash that cut a write
in half is a physical accident, not a reason the whole system stops.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hanish import Substrate
from hanish.adapters.ci import REQUIRED_CHECKS_PASS, CIAdapter
from hanish.future.claims import (
    Comparator,
    Exposure,
    Forecast,
    ResolutionSpec,
)
from hanish.past.events import Verdict

SHA = "abc123"
LATER = (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def build(tmp_path):
    ci = CIAdapter()
    sub = Substrate(tmp_path, observables=ci.observable_specs())
    f = Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="tail recovery keeps the substrate open",
        probability=0.7,
        exposure=Exposure.BLIND,
        world_ref=ci.world_ref(SHA, "wf1", "lock1"),
        world_ref_capability=ci.world_ref_capability,
        resolution=ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=LATER,
        ),
    )
    return ci, sub, f


def test_torn_tail_recovers_on_reopen(tmp_path):
    """P3 repro. A partial final line used to brick the substrate."""
    ci, sub, f = build(tmp_path)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", 1, passed=False))

    with open(sub.evidence_l.path, "a", encoding="utf-8") as fh:
        fh.write('{"torn_off')                     # crash mid-append: no newline got out

    reopened = Substrate(tmp_path, observables=ci.observable_specs())
    assert reopened.evidence_l.tail_loss == 1
    assert reopened.status()["capture"]["tail_loss"] == 1
    # The ledger is whole again and still works. FIRST_VALID_TERMINAL: the
    # surviving first observation (passed=False) resolves the forecast to
    # MISS before the retry arrives -- the torn byte neither bricked the
    # rebuild nor laundered the observation away.
    reopened.capture(ci.checks_result(SHA, "481", 2, passed=True))
    o = reopened.process()[0]
    assert o.verdict is Verdict.MISS
    assert reopened.status()["capture"]["tail_loss"] == 1   # counted once


def test_mid_history_corruption_is_counted_not_fatal(tmp_path):
    """A damaged line between valid records is skipped and counted, never
    fabricated and never allowed to brick reopen. A skipped observation
    shows up as a source_seq gap, so a damaged line can never become a MISS."""
    ci, sub, f = build(tmp_path)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", 1, passed=False))
    sub.capture(ci.checks_result(SHA, "481", 2, passed=True))
    good_lines = sub.evidence_l.path.read_text().splitlines()

    # splice a corrupt line between two valid records (truly mid-history)
    mid = good_lines[:1] + ['{"broken'] + good_lines[1:]
    sub.evidence_l.path.write_text("\n".join(mid) + "\n", encoding="utf-8")

    reopened = Substrate(tmp_path, observables=ci.observable_specs())
    assert reopened.evidence_l.corrupted == 1
    assert reopened.status()["capture"]["corrupted"] == 1
    assert reopened.status()["capture"]["tail_loss"] == 0   # nothing torn
    # the surviving valid records rebuilt the state
    assert len(reopened._observations) == 2
