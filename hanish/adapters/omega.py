"""Host Omega: a deliberately small hostile-host conformance adapter.

Omega carries no domain policy. It gives the conformance suite an explicit,
restart-stable identity surface on which to inject malformed values, gaps,
duplicates, conflicts, and I/O failures without teaching the core what a CI
run is.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..future.claims import EmissionSemantics, ObservableSpec, WorldRefCapability
from ..past.events import CompletenessSeal, ObservationEvent, Validity
from ..time import now

OMEGA_RESULT = "omega.required_result"


@dataclass(frozen=True)
class OmegaAdapter:
    namespace: str = "default"

    world_ref_capability = WorldRefCapability.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValueError("Omega namespace must be a non-empty string")

    @property
    def source_ref(self) -> str:
        return f"host-omega:{self.namespace}"

    def observable_specs(self) -> dict[str, ObservableSpec]:
        return {
            OMEGA_RESULT: ObservableSpec(
                name=OMEGA_RESULT,
                value_type="bool",
                emission=EmissionSemantics.TERMINAL,
                sources=(self.source_ref,),
            )
        }

    def event(
        self,
        *,
        subject_ref: str,
        epoch_ref: str,
        event_id: str,
        value: object,
        source_seq: object,
        arrived_at: str | None = None,
        validity: Validity = Validity.VALID,
    ) -> ObservationEvent:
        return ObservationEvent(
            source_ref=self.source_ref,
            event_id=event_id,
            subject_ref=subject_ref,
            observable=OMEGA_RESULT,
            value=value,
            source_seq=source_seq,
            epoch_ref=epoch_ref,
            validity=validity,
            arrived_at=arrived_at or now(),
        )

    def seal(
        self,
        *,
        subject_ref: str,
        epoch_ref: str,
        final_source_seq: object,
        complete: object = True,
        sealed_at: str | None = None,
    ) -> CompletenessSeal:
        return CompletenessSeal(
            source_ref=self.source_ref,
            epoch_ref=epoch_ref,
            subject_ref=subject_ref,
            final_source_seq=final_source_seq,
            complete=complete,
            sealed_at=sealed_at or now(),
        )
