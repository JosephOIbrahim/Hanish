"""Canonical v2 authorship values shared by behavioral tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from hanish.future.claims import ExposureBasis


def created_before(horizon: str) -> str:
    """A deterministic relation, not a wall-clock race in the test process."""
    return (datetime.fromisoformat(horizon) - timedelta(hours=2)).isoformat()


def blind_authorship(horizon: str, *, authored_by: str = "human") -> dict:
    """Complete, disjoint host attestation for tests that require calibration."""
    created_at = created_before(horizon)
    return {
        "authored_by": authored_by,
        "created_at": created_at,
        "exposure_basis": ExposureBasis(
            author_ref=authored_by,
            seen_by=("test-auditor",),
            capable_actors=("test-target-actor",),
            seen_by_complete=True,
            capable_actors_complete=True,
            separation_control_ref="test-control:embargo",
            attested_by="test-host",
            attested_at=(
                datetime.fromisoformat(created_at) - timedelta(seconds=1)
            ).isoformat(),
        ),
    }
