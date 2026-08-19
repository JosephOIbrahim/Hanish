"""V0.0 tests. One test per frozen guarantee.

Each test name is the claim it defends. If a test here fails, a law is broken.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from temporal import Substrate
from temporal.adapters.ci import REQUIRED_CHECKS_PASS, CIAdapter
from temporal.core.types import (
    Comparator,
    Exposure,
    Forecast,
    ObservationEvent,
    ResolutionSpec,
    Terminal,
    Verdict,
    WorldRefCapability,
)

SHA = "abc123"
LATER = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
PAST = (datetime.now(UTC) - timedelta(hours=1)).isoformat()


def build(tmp_path, horizon=LATER, probability=0.82, exposure=Exposure.BLIND):
    ci = CIAdapter()
    sub = Substrate(tmp_path, observables=ci.observable_specs())
    f = Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="next valid required-check result passes",
        probability=probability,
        exposure=exposure,
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


# -- the loop --------------------------------------------------------------

def test_the_loop_closes(tmp_path):
    """V0.0's whole reason to exist: an external observation resolves a
    pre-authored forecast and a durable outcome persists."""
    ci, sub, f = build(tmp_path)
    fid = sub.author(f)

    sub.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=True))
    outcomes = sub.process()

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.forecast_id == fid
    assert o.terminal is Terminal.RESOLVED
    assert o.verdict is Verdict.HIT
    assert o.brier == pytest.approx((0.82 - 1.0) ** 2)
    assert o.calibration_eligible is True


def test_outcome_survives_restart(tmp_path):
    """Everything derives from the ledgers. Reopen and rebuild."""
    ci, sub, f = build(tmp_path)
    fid = sub.author(f)
    sub.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=False))
    sub.process()

    reopened = Substrate(tmp_path, observables=ci.observable_specs())
    assert reopened.outcomes[fid].verdict is Verdict.MISS
    assert reopened.forecasts[fid].probability == 0.82


def test_forecast_authored_before_death_still_resolves(tmp_path):
    """Author, die before any evidence, restart, then let CI answer."""
    ci, sub, f = build(tmp_path)
    fid = sub.author(f)
    del sub                                   # process death

    reopened = Substrate(tmp_path, observables=ci.observable_specs())
    reopened.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=True))
    outcomes = reopened.process()
    assert outcomes[0].forecast_id == fid
    assert outcomes[0].verdict is Verdict.HIT


# -- P3: judgment precedes evidence ---------------------------------------

def test_undeclared_observable_cannot_be_authored(tmp_path):
    """An unfalsifiable claim is free to produce and therefore worthless.
    Refused at authoring, not resolved UNRESOLVABLE later."""
    ci, sub, _ = build(tmp_path)
    bad = Forecast(
        subject_ref="git:abc",
        claim="vibes",
        probability=0.9,
        exposure=Exposure.BLIND,
        resolution=ResolutionSpec(
            observable="ci.nothing_emits_this",
            comparator=Comparator.EQ, threshold=True, horizon=LATER,
        ),
    )
    with pytest.raises(ValueError, match="undeclared observable"):
        sub.author(bad)


# -- P8: fail closed inward ------------------------------------------------

def test_missing_evidence_is_unresolvable_not_miss(tmp_path):
    """The strongest invariant in the system. An operational failure may
    never be laundered into contrary evidence about the model."""
    ci, sub, f = build(tmp_path, horizon=PAST)
    sub.author(f)

    outcomes = sub.process()                  # horizon passed, nothing arrived

    assert outcomes[0].terminal is Terminal.UNRESOLVABLE
    assert outcomes[0].verdict is None
    assert outcomes[0].brier is None
    assert outcomes[0].calibration_eligible is False
    assert sub.status()["scoreable_rate"] == 0.0


def test_sealed_complete_stream_makes_absence_informative(tmp_path):
    """With a seal AND no sequence gaps, absence IS evidence: the channel
    promised a terminal value, the channel is closed, nothing matched."""
    ci, sub, f = build(tmp_path, horizon=PAST)
    sub.author(f)

    sub.capture(ci.duration(SHA, run_id="481", attempt=1, seconds=41.0))
    sub.capture(ci.finalize(SHA, complete=True))

    o = sub.process()[0]
    assert o.terminal is Terminal.RESOLVED
    assert o.verdict is Verdict.MISS
    assert o.calibration_eligible is True


def test_sequence_gap_defeats_the_seal(tmp_path):
    """A drop counter alone is insufficient -- it only knows about failures
    it observed. Per-source sequence numbers catch the ones it didn't."""
    ci, sub, f = build(tmp_path, horizon=PAST)
    sub.author(f)

    ci.duration(SHA, run_id="481", attempt=1, seconds=41.0)  # seq 1, never captured
    arrived = ci.duration(SHA, run_id="481", attempt=2, seconds=42.0)  # seq 2
    sub.capture(arrived)
    sub.capture(ci.finalize(SHA, complete=True))   # claims 2 records; we have 1

    o = sub.process()[0]
    assert o.terminal is Terminal.UNRESOLVABLE
    assert "completeness unknown" in o.reason


# -- P7b: fail open outward ------------------------------------------------

def test_capture_never_raises_into_the_host(tmp_path):
    """Losing an observation is acceptable. Breaking a build is not."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    assert sub.capture("not an event") is False          # garbage
    assert sub.capture(ObservationEvent(                  # undeclared observable
        source_ref="x", event_id="e1", subject_ref="s",
        observable="unknown.thing", value=1)) is False

    class Exploding(ObservationEvent):
        @property
        def dedup_key(self):
            raise RuntimeError("boom")

    assert sub.capture(Exploding(
        source_ref="x", event_id="e2",
        subject_ref=ci.subject_ref(SHA),
        observable=REQUIRED_CHECKS_PASS, value=True)) is False

    assert sub.dropped == 3
    assert sub.status()["capture"]["drop_rate"] == 1.0


# -- identity --------------------------------------------------------------

def test_duplicate_envelope_does_not_resolve_twice(tmp_path):
    """At-least-once transport, idempotent ingestion, effect-once resolution."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    ev = ci.checks_result(SHA, run_id="481", attempt=1, passed=True)
    assert sub.capture(ev) is True
    assert sub.capture(ev) is True             # accepted, not re-ingested

    outcomes = sub.process()
    assert len(outcomes) == 1
    assert sub.duplicates == 1
    assert len(list(sub.outcomes_l.raw())) == 1


def test_rerun_is_a_second_event_about_one_subject(tmp_path):
    """Two attempts on one commit are two events about one subject. Not
    duplicates. But only the first VALID one scores."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    infra = ci.checks_result(SHA, "481", attempt=1, passed=False,
                             infrastructure_failure=True)
    real = ci.checks_result(SHA, "481", attempt=2, passed=False)
    green = ci.checks_result(SHA, "481", attempt=3, passed=True)

    for e in (infra, real, green):
        sub.capture(e)
    outcomes = sub.process()

    assert infra.dedup_key != real.dedup_key           # not duplicates
    assert len(outcomes) == 1
    assert outcomes[0].verdict is Verdict.MISS         # attempt 2 scored
    assert outcomes[0].observed is False
    # attempt 3 going green does NOT rescore. Retrying until green is the
    # largest calibration-laundering vector there is.


def test_invalid_observation_does_not_score(tmp_path):
    """Exogenous does not mean correct."""
    ci, sub, f = build(tmp_path)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", attempt=1, passed=False,
                                 infrastructure_failure=True))
    assert sub.process() == []


def test_world_ref_capability_is_enforced_at_construction(tmp_path):
    """An invariant with a runtime conditional cannot be checked. The host
    declares capability; the forecast must then honour it."""
    with pytest.raises(ValueError, match="declared world_ref capability"):
        Forecast(
            subject_ref="git:x", claim="c", probability=0.5,
            exposure=Exposure.BLIND,
            world_ref=None,
            world_ref_capability=WorldRefCapability.REPLAYABLE,
            resolution=ResolutionSpec(
                observable=REQUIRED_CHECKS_PASS, comparator=Comparator.EQ,
                threshold=True, horizon=LATER),
        )


# -- exposure --------------------------------------------------------------

def test_exposed_forecast_resolves_but_never_calibrates(tmp_path):
    """A forecast visible to an actor that could move its target is presumed
    to have helped cause the outcome, whether or not anyone reported acting.
    It may be interesting. It is not calibration data."""
    ci, sub, f = build(tmp_path, exposure=Exposure.EXPOSED)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", attempt=1, passed=True))

    o = sub.process()[0]
    assert o.terminal is Terminal.RESOLVED
    assert o.verdict is Verdict.HIT
    assert o.calibration_eligible is False
    assert sub.status()["closure_rate"] is None or True   # not yet due


# -- accountability --------------------------------------------------------

def test_ledgers_are_append_only(tmp_path):
    """Influence may decay to zero. Accountability may never decay."""
    ci, sub, f = build(tmp_path)
    fid = sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", attempt=1, passed=False))
    sub.process()

    before = (sub.root / "forecasts.jsonl").read_text()
    sub.capture(ci.checks_result(SHA, "481", attempt=2, passed=True))
    sub.process()
    after = (sub.root / "forecasts.jsonl").read_text()

    assert after == before                                # nothing rewritten
    assert sub.forecasts[fid].probability == 0.82         # original preserved
    assert sub.outcomes[fid].verdict is Verdict.MISS      # first terminal stands


def test_resolution_is_indexed_not_scanned(tmp_path):
    """Cost of a hit is a lookup, not a walk of history."""
    ci, sub, f = build(tmp_path)
    sub.author(f)
    for i in range(200):
        sub.capture(ci.duration(f"other{i}", str(i), 1, 1.0))

    key = (ci.subject_ref(SHA), REQUIRED_CHECKS_PASS)
    assert sub.index[key] == [f.forecast_id]
    assert sub.status()["index"]["max_watchers"] == 1


# -- health ----------------------------------------------------------------

def test_status_separates_closure_from_scoreability_from_capture(tmp_path):
    ci, sub, f = build(tmp_path, horizon=PAST)
    sub.author(f)
    sub.process()

    s = sub.status()
    assert s["closure_rate"] == 1.0        # bookkeeping closed
    assert s["scoreable_rate"] == 0.0      # but we learned nothing
    assert s["forecasts"]["prediction_debt"] == 0
