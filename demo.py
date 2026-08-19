#!/usr/bin/env python3
"""V0.0 walkthrough.

Three scenarios, in order of how much they teach:

  1. The loop closes.               A CI result scores a pre-authored forecast.
  2. Evidence goes missing.         The same setup, minus the seal.
  3. The process dies mid-flight.   Author, die, restart, resolve.

Run:  python3 demo.py
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hanish import Substrate
from hanish.adapters.ci import REQUIRED_CHECKS_PASS, CIAdapter
from hanish.core.types import (
    Comparator,
    Exposure,
    Forecast,
    ResolutionSpec,
)

SHA = "abc123def456"


def rule(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def make_forecast(ci, probability, horizon):
    return Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="next valid required-check result for this code state passes",
        probability=probability,
        exposure=Exposure.BLIND,          # authored after the final commit
        world_ref=ci.world_ref(SHA, "workflow-v8", "lock-9f2a"),
        world_ref_capability=ci.world_ref_capability,
        authored_by="human",
        resolution=ResolutionSpec(
            observable=REQUIRED_CHECKS_PASS,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=horizon,
        ),
    )


def scenario_1(root):
    rule("1 — THE LOOP CLOSES")
    ci = CIAdapter()
    sub = Substrate(root / "s1", observables=ci.observable_specs())

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    f = make_forecast(ci, 0.82, future)
    fid = sub.author(f)
    print(f"  authored   {fid}")
    print(f"    subject  {f.subject_ref}")
    print(f"    world    {f.world_ref}  ({f.world_ref_capability.value})")
    print(f"    claim    P = {f.probability}")
    print(f"    exposure {f.exposure.value}")

    print("\n  ... CI runs. Nobody touches the substrate. ...\n")
    sub.capture(ci.duration(SHA, run_id="481", attempt=1, seconds=487.3))
    sub.capture(ci.checks_result(SHA, run_id="481", attempt=1, passed=False))
    sub.capture(ci.finalize(SHA))

    for o in sub.process():
        print(f"  resolved   {o.terminal.value} / {o.verdict.value}")
        print(f"    predicted {o.predicted}   observed {o.observed}")
        print(f"    brier     {o.brier:.4f}")
        print(f"    eligible  {o.calibration_eligible}")

    print("\n  ... developer pushes a fix, CI goes green on a rerun ...\n")
    sub.capture(ci.checks_result(SHA, run_id="481", attempt=2, passed=True))
    n = len(sub.process())
    print(f"  rescored?  {n} new outcomes  <- retrying until green cannot launder calibration")
    return sub


def scenario_2(root):
    rule("2 — EVIDENCE GOES MISSING")
    ci = CIAdapter()
    sub = Substrate(root / "s2", observables=ci.observable_specs())

    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    sub.author(make_forecast(ci, 0.82, past))
    print("  horizon has already passed and no result arrived.")
    print("  the runner may have died. or the checks may genuinely have not run.")
    print("  the substrate cannot tell, so it refuses to guess.\n")

    for o in sub.process():
        print(f"  resolved   {o.terminal.value}")
        print(f"    reason    {o.reason}")
        print(f"    brier     {o.brier}")
        print(f"    eligible  {o.calibration_eligible}")

    s = sub.status()
    print(f"\n  closure   {s['closure_rate']}   <- bookkeeping did close")
    print(f"  scoreable {s['scoreable_rate']}   <- but we learned nothing, and we say so")
    return sub


def scenario_3(root):
    rule("3 — THE PROCESS DIES MID-FLIGHT")
    ci = CIAdapter()
    sub = Substrate(root / "s3", observables=ci.observable_specs())
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    fid = sub.author(make_forecast(ci, 0.91, future))
    print(f"  authored   {fid}")

    del sub
    print("  ... process dies. no scheduler was running. nothing was pending. ...")

    reopened = Substrate(root / "s3", observables=ci.observable_specs())
    print(f"  reopened   {len(reopened.forecasts)} forecast(s) rebuilt from ledger")

    reopened.capture(ci.checks_result(SHA, run_id="512", attempt=1, passed=True))
    for o in reopened.process():
        print(f"  resolved   {o.terminal.value} / {o.verdict.value}   brier {o.brier:.4f}")
    return reopened


def main():
    root = Path(tempfile.mkdtemp(prefix="tcs-demo-"))
    try:
        s1 = scenario_1(root)
        scenario_2(root)
        scenario_3(root)

        rule("HEALTH — scenario 1")
        s = s1.status()
        print(f"  closure_rate    {s['closure_rate']}")
        print(f"  scoreable_rate  {s['scoreable_rate']}")
        print(f"  capture         {s['capture']}")
        print(f"  forecasts       {s['forecasts']}")
        print(f"  outcomes        {s['outcomes']}")
        print(f"  index           {s['index']}")

        rule("LEDGERS — scenario 1")
        for name in ("forecasts", "evidence", "outcomes"):
            p = s1.root / f"{name}.jsonl"
            print(f"  {name:10} {len(p.read_text().splitlines())} records  {p}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
