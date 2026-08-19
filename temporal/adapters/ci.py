"""CI host adapter.

This is the ONLY file in the system that knows what a commit is.

An adapter may translate names, package values, assign opaque subject
identities, and declare host capabilities. It may not infer conclusions,
invent probabilities, reinterpret failures, calibrate, or choose actions.
If it starts reasoning, the seam has leaked.

Direction of dependency is one-way and load-bearing:
    adapter imports core.   core imports nothing from adapters.
"""

from __future__ import annotations

import hashlib

from ..core.types import (
    CompletenessSeal,
    EmissionSemantics,
    ObservableSpec,
    ObservationEvent,
    Validity,
    WorldRefCapability,
)

SOURCE = "github-actions"

REQUIRED_CHECKS_PASS = "ci.required_checks_pass"
DURATION_S = "ci.duration_s"

SPECS = {
    REQUIRED_CHECKS_PASS: ObservableSpec(
        name=REQUIRED_CHECKS_PASS,
        value_type="bool",
        # One final value closes the subject. That is what lets a sealed
        # stream with no matching observation legitimately mean MISS.
        emission=EmissionSemantics.TERMINAL,
    ),
    DURATION_S: ObservableSpec(
        name=DURATION_S,
        value_type="float",
        emission=EmissionSemantics.PER_SUBJECT,
    ),
}


class CIAdapter:
    """Declares what CI emits and translates CI events into opaque records."""

    world_ref_capability = WorldRefCapability.REPLAYABLE

    def __init__(self, source_ref: str = SOURCE):
        self.source_ref = source_ref
        self._seq: dict[str, int] = {}

    # -- declaration -----------------------------------------------------

    def observable_specs(self) -> dict[str, ObservableSpec]:
        return dict(SPECS)

    # -- identity --------------------------------------------------------

    @staticmethod
    def subject_ref(commit_sha: str) -> str:
        """What the forecast is about. Opaque to the core."""
        return f"git:{commit_sha}"

    @staticmethod
    def world_ref(commit_sha: str, workflow_sha: str, lockfile_sha: str) -> str:
        """What information state existed when the forecast was authored.

        Git gives us a genuinely replayable coordinate for free -- the whole
        world can be reconstructed from it. That is a stronger guarantee than
        most hosts can offer."""
        digest = hashlib.sha256(
            f"{commit_sha}|{workflow_sha}|{lockfile_sha}".encode()
        ).hexdigest()[:16]
        return f"world:{digest}"

    # -- emission --------------------------------------------------------

    def _next_seq(self, epoch_ref: str) -> int:
        self._seq[epoch_ref] = self._seq.get(epoch_ref, 0) + 1
        return self._seq[epoch_ref]

    def checks_result(
        self,
        commit_sha: str,
        run_id: str,
        attempt: int,
        passed: bool,
        infrastructure_failure: bool = False,
    ) -> ObservationEvent:
        subject = self.subject_ref(commit_sha)
        return ObservationEvent(
            source_ref=self.source_ref,
            # Uniqueness only needs to hold within this source's scope.
            event_id=f"run-{run_id}:attempt-{attempt}:required_checks",
            subject_ref=subject,
            observable=REQUIRED_CHECKS_PASS,
            value=passed,
            source_seq=self._next_seq(subject),
            epoch_ref=subject,
            # Exogenous does not mean correct. A runner can malfunction, and
            # a malfunctioning runner must not score a forecast.
            validity=Validity.INVALID if infrastructure_failure else Validity.VALID,
            metadata={"run_id": run_id, "attempt": attempt},
        )

    def duration(
        self, commit_sha: str, run_id: str, attempt: int, seconds: float
    ) -> ObservationEvent:
        subject = self.subject_ref(commit_sha)
        return ObservationEvent(
            source_ref=self.source_ref,
            event_id=f"run-{run_id}:attempt-{attempt}:duration",
            subject_ref=subject,
            observable=DURATION_S,
            value=seconds,
            source_seq=self._next_seq(subject),
            epoch_ref=subject,
            metadata={"run_id": run_id, "attempt": attempt},
        )

    def finalize(self, commit_sha: str, complete: bool = True) -> CompletenessSeal:
        """Called from the CI job finalizer. Asserts: this stream is over and
        emitted exactly this many records.

        Without this, the substrate can never distinguish 'it did not happen'
        from 'the channel died', and must fail closed on every expiry."""
        subject = self.subject_ref(commit_sha)
        return CompletenessSeal(
            source_ref=self.source_ref,
            epoch_ref=subject,
            final_source_seq=self._seq.get(subject, 0),
            complete=complete,
        )
