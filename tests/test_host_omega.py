"""Host Omega and CIAdapter share the same hostile-host contract."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hanish import Substrate
from hanish.adapters.ci import REQUIRED_CHECKS_PASS, CIAdapter
from hanish.adapters.omega import OMEGA_RESULT, OmegaAdapter
from hanish.future.claims import Comparator, Exposure, Forecast, ResolutionSpec
from hanish.past.events import CompletenessSeal, ObservationEvent, Terminal, Verdict

CREATED = "2026-01-01T00:00:00+00:00"
ARRIVED = "2026-01-01T01:00:00+00:00"
HORIZON = "2026-01-01T02:00:00+00:00"
AFTER = "2026-01-01T03:00:00+00:00"
SUBJECT = "subject:omega-contract"
EPOCH = "epoch:omega-contract"


@dataclass(frozen=True)
class ContractHost:
    name: str
    source_ref: str
    observable: str
    specs: dict
    event: object
    seal: object


def _omega_host() -> ContractHost:
    adapter = OmegaAdapter("contract")

    def make_event(value=True, event_id="event-1", source_seq=1, arrived_at=ARRIVED):
        return adapter.event(
            subject_ref=SUBJECT,
            epoch_ref=EPOCH,
            event_id=event_id,
            value=value,
            source_seq=source_seq,
            arrived_at=arrived_at,
        )

    def make_seal(
        final_source_seq=1,
        complete=True,
        sealed_at="2026-01-01T02:01:00+00:00",
    ):
        return adapter.seal(
            subject_ref=SUBJECT,
            epoch_ref=EPOCH,
            final_source_seq=final_source_seq,
            complete=complete,
            sealed_at=sealed_at,
        )

    return ContractHost(
        name="omega",
        source_ref=adapter.source_ref,
        observable=OMEGA_RESULT,
        specs=adapter.observable_specs(),
        event=make_event,
        seal=make_seal,
    )


def _ci_host() -> ContractHost:
    adapter = CIAdapter(source_ref="github-actions:fixture/contract")

    def make_event(value=True, event_id="event-1", source_seq=1, arrived_at=ARRIVED):
        return ObservationEvent(
            source_ref=adapter.source_ref,
            event_id=event_id,
            subject_ref=SUBJECT,
            observable=REQUIRED_CHECKS_PASS,
            value=value,
            source_seq=source_seq,
            epoch_ref=EPOCH,
            arrived_at=arrived_at,
        )

    def make_seal(
        final_source_seq=1,
        complete=True,
        sealed_at="2026-01-01T02:01:00+00:00",
    ):
        return CompletenessSeal(
            source_ref=adapter.source_ref,
            epoch_ref=EPOCH,
            subject_ref=SUBJECT,
            final_source_seq=final_source_seq,
            complete=complete,
            sealed_at=sealed_at,
        )

    return ContractHost(
        name="ci",
        source_ref=adapter.source_ref,
        observable=REQUIRED_CHECKS_PASS,
        specs=adapter.observable_specs(),
        event=make_event,
        seal=make_seal,
    )


@pytest.fixture(params=[_omega_host, _ci_host], ids=["host-omega", "ci-adapter"])
def host(request) -> ContractHost:
    return request.param()


def _forecast(host: ContractHost) -> Forecast:
    return Forecast(
        subject_ref=SUBJECT,
        claim=f"{host.name} emits true",
        probability=0.5,
        exposure=Exposure.EXPOSED,
        created_at=CREATED,
        resolution=ResolutionSpec(
            observable=host.observable,
            comparator=Comparator.EQ,
            threshold=True,
            horizon=HORIZON,
        ),
    )


def test_contract_resolves_and_replays_identically(host, tmp_path):
    substrate = Substrate(tmp_path, observables=host.specs)
    forecast_id = substrate.author(_forecast(host))
    envelope = host.event()
    assert substrate.capture(envelope)
    assert substrate.capture(envelope)
    assert substrate.capture(host.seal())
    assert substrate.process(at=AFTER)[0].verdict is Verdict.HIT

    reopened = Substrate(tmp_path, observables=host.specs)
    assert reopened.outcomes[forecast_id].verdict is Verdict.HIT
    assert reopened.duplicates == 0  # operational retries are not epistemic history
    assert len(reopened._observations) == 1


def test_contract_rejects_bool_int_confusion_without_raising(host, tmp_path):
    substrate = Substrate(tmp_path, observables=host.specs)
    substrate.author(_forecast(host))
    assert not substrate.capture(host.event(1))
    assert substrate.process(at="2026-01-01T01:30:00+00:00") == []
    assert substrate.dropped == 1


def test_contract_io_fault_is_fail_open_outward(host, tmp_path, monkeypatch):
    substrate = Substrate(tmp_path, observables=host.specs)
    substrate.author(_forecast(host))

    def fail(*_args, **_kwargs):
        raise OSError("injected disk fault")

    monkeypatch.setattr(substrate.evidence_l, "sync_observation_once", fail)
    assert not substrate.capture(host.event())
    assert substrate.dropped == 1


def test_contract_malformed_time_is_dropped_and_good_retry_still_scores(host, tmp_path):
    substrate = Substrate(tmp_path, observables=host.specs)
    substrate.author(_forecast(host))
    malformed = host.event()
    object.__setattr__(malformed, "arrived_at", "not-a-time")
    assert not substrate.capture(malformed)
    assert substrate.capture(host.event())
    assert substrate.process(at=AFTER)[0].terminal is Terminal.RESOLVED


def test_contract_gap_defeats_a_complete_seal_across_restart(host, tmp_path):
    substrate = Substrate(tmp_path, observables=host.specs)
    forecast_id = substrate.author(_forecast(host))
    assert substrate.capture(
        host.event(
            event_id="event-2",
            source_seq=2,
            arrived_at="2026-01-01T02:30:00+00:00",
        )
    )
    assert substrate.capture(host.seal(final_source_seq=2))
    assert substrate.process(at=AFTER)[0].terminal is Terminal.UNRESOLVABLE

    reopened = Substrate(tmp_path, observables=host.specs)
    assert reopened.outcomes[forecast_id].terminal is Terminal.UNRESOLVABLE
    assert reopened.process(at=AFTER) == []


def test_contract_provisional_outcome_settles_once_after_late_complete_seal(host, tmp_path):
    substrate = Substrate(tmp_path, observables=host.specs)
    forecast_id = substrate.author(_forecast(host))
    assert substrate.process(at=AFTER)[0].terminal is Terminal.UNRESOLVABLE
    assert substrate.process(at=AFTER) == []

    assert substrate.capture(host.seal(final_source_seq=0))
    settled = substrate.process(at=AFTER)
    assert settled[0].verdict is Verdict.MISS
    assert substrate.process(at=AFTER) == []

    reopened = Substrate(tmp_path, observables=host.specs)
    assert reopened.outcomes[forecast_id].verdict is Verdict.MISS
    assert len(list(reopened.outcomes_l.raw())) == 2


def test_contract_conflicting_seal_cannot_upgrade_after_restart(host, tmp_path):
    substrate = Substrate(tmp_path, observables=host.specs)
    forecast_id = substrate.author(_forecast(host))
    assert substrate.capture(host.seal(final_source_seq=0, complete=False))
    assert not substrate.capture(
        host.seal(
            final_source_seq=0,
            complete=True,
            sealed_at="2026-01-01T02:02:00+00:00",
        )
    )

    reopened = Substrate(tmp_path, observables=host.specs)
    reopened.process(at=AFTER)
    assert reopened.outcomes[forecast_id].terminal is Terminal.UNRESOLVABLE
    assert len(reopened._seals) == 1


def test_two_host_namespaces_cannot_silently_overwrite_each_other(tmp_path):
    first = OmegaAdapter("one")
    second = OmegaAdapter("two")
    substrate = Substrate(tmp_path, observables=first.observable_specs())
    with pytest.raises(ValueError, match="namespace collision"):
        substrate.declare(second.observable_specs()[OMEGA_RESULT])
