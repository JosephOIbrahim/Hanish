"""G4 -- schema versioning.

Records carry LEDGER_SCHEMA on write. Old records without a tag read as v1.
A record with a HIGHER version fails loud: older code must never misread
newer data -- silently reading a future format is how corruption enters a
calibration feed.
"""

from __future__ import annotations

import json
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
from hanish.past.ledger import LEDGER_SCHEMA

SHA = "abc123"
LATER = (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def build(tmp_path):
    ci = CIAdapter()
    sub = Substrate(tmp_path, observables=ci.observable_specs())
    f = Forecast(
        subject_ref=ci.subject_ref(SHA),
        claim="schema stays honest",
        probability=0.5,
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


def test_records_carry_the_schema_version(tmp_path):
    ci, sub, f = build(tmp_path)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", 1, passed=False))

    first = sub.evidence_l.path.read_text().splitlines()[0]
    payload = json.loads(first)
    assert payload["_v"] == LEDGER_SCHEMA == 1


def test_legacy_untagged_records_open_fine(tmp_path):
    """V0.0 records carried a _kind tag but no version tag. They read as v1."""
    ci, sub, f = build(tmp_path)
    sub.author(f)
    sub.capture(ci.checks_result(SHA, "481", 1, passed=False))

    # strip the _v tag the way V0.0 would have written the record
    lines = sub.evidence_l.path.read_text().splitlines()
    stripped = [ln.replace('"_v": 1,', "").replace(',"_v": 1', "") for ln in lines]
    sub.evidence_l.path.write_text("\n".join(stripped) + "\n", encoding="utf-8")

    reopened = Substrate(tmp_path, observables=ci.observable_specs())
    assert len(reopened._observations) == 1


def test_future_schema_version_fails_loud(tmp_path):
    """A record written by a NEWER writer is dangerous to misread. Refused."""
    ci, sub, f = build(tmp_path)
    sub.author(f)

    with open(sub.evidence_l.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "_kind": "observation", "_v": 99,
            "source_ref": "x", "event_id": "new", "subject_ref": "s",
            "observable": REQUIRED_CHECKS_PASS, "value": True,
        }) + "\n")

    with pytest.raises(ValueError, match="schema"):
        Substrate(tmp_path, observables=ci.observable_specs())
