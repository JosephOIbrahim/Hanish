"""G1 -- the OUTWARD law. The substrate never raises into its host.

Flight PAST found P1 (an incomparable observation crashed process()) and P2
(a naive horizon crashed the sweep). Both were probed live against V0.0.
These tests pin the fixes: malformed evidence is dropped, counted, and
surfaced -- never thrown at the host, never laundered into a verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hanish import Substrate
from hanish.adapters.ci import REQUIRED_CHECKS_PASS, CIAdapter
from hanish.future.claims import (
    Comparator,
    Exposure,
    Forecast,
    ResolutionSpec,
)
from hanish.past.events import (
    ObservationEvent,
    Verdict,
)
from tests._support import created_before

SHA = "abc123"
LATER = (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def build(tmp_path, comparator=Comparator.EQ, threshold=True):
    ci = CIAdapter()
    sub = Substrate(tmp_path, observables=ci.observable_specs())
    f = Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="next valid required-check result for this code state passes",
        probability=0.8,
        exposure=Exposure.EXPOSED,
        created_at=created_before(LATER),
        world_ref=ci.world_ref(SHA, "wf1", "lock1"),
        world_ref_capability=ci.world_ref_capability,
        resolution=ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS,
            comparator=comparator,
            threshold=threshold,
            horizon=LATER,
        ),
    )
    return ci, sub, f


def test_incomparable_observation_never_crashes_process(tmp_path):
    """P1 repro: a wrong-typed value is dropped before persistence and can
    neither crash process() nor become a score."""
    ci, sub, f = build(tmp_path, comparator=Comparator.GT, threshold=True)
    fid = sub.author(f)

    assert sub.capture(ObservationEvent(
        source_ref=ci.source_ref, event_id="e1", subject_ref=ci.subject_ref(SHA),
        observable=REQUIRED_CHECKS_PASS, value="garbage",     # str vs bool
    )) is False

    out = sub.process()                     # must not raise
    assert out == []                        # malformed evidence scores nothing
    assert sub.invalid_compare == 0
    assert sub.status()["capture"]["invalid_compare"] == 0
    assert sub.dropped == 1
    assert fid not in sub.outcomes          # still open


def test_after_a_bad_observation_a_good_one_still_scores(tmp_path):
    """Fail closed, then recover: the malformed event is skipped, not sticky."""
    ci, sub, f = build(tmp_path, comparator=Comparator.GT, threshold=False)
    sub.author(f)

    assert sub.capture(ObservationEvent(
        source_ref=ci.source_ref, event_id="e1", subject_ref=ci.subject_ref(SHA),
        observable=REQUIRED_CHECKS_PASS, value="garbage")) is False
    sub.process()
    assert sub.invalid_compare == 0

    sub.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=True))
    out = sub.process()
    assert out[0].verdict is Verdict.HIT
    assert sub.invalid_compare == 0
    assert sub.dropped == 1


def test_process_never_raises_on_a_defective_internals(tmp_path, monkeypatch):
    """The backstop, not the path: even if something inside the resolution
    machinery blows up, the host gets an empty list and a counter."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    def boom(self, at):
        raise RuntimeError("internal defect")

    monkeypatch.setattr(Substrate, "_sweep_expired", boom)
    assert sub.process() == []
    assert sub.process_errors == 1
    assert sub.status()["capture"]["process_errors"] == 1


def test_naive_horizon_rejected_at_authoring(tmp_path):
    """P2 repro: a naive (no-tz) horizon used to crash the expiry sweep.
    Now the well-formedness gate refuses it before it can ever reach a
    comparison."""
    ci, sub, _ = build(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        Forecast(
            subject_ref=ci.subject_ref(SHA),
            claim="naive horizon must never reach process",
            probability=0.5,
            exposure=Exposure.EXPOSED,
            resolution=ResolutionSpec(
                observable=REQUIRED_CHECKS_PASS,
                comparator=Comparator.EQ,
                threshold=True,
                horizon=datetime.now().isoformat(),   # deliberately naive
            ),
        )
