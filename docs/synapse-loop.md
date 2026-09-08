# Hanish in the SYNAPSE memory LOOP

[SYNAPSE v5.67.2](https://github.com/JosephOIbrahim/Synapse/releases/tag/v5.67.2)
uses Hanish's existing API to record a forecast before an artist-requested
operation and settle it against the observed handler outcome. The adapter lives
in SYNAPSE. Hanish's core remains domain-blind and has no Houdini, Moneta,
Octavius or model dependency.

## Qualified sources

| Component | Source used for qualification |
|---|---|
| SYNAPSE | [v5.67.2, `7d121ea1`](https://github.com/JosephOIbrahim/Synapse/commit/7d121ea1d3abbd4847f277399514c80a639b25b1) |
| Hanish | [v0.2.0, `b82e334`](https://github.com/JosephOIbrahim/Hanish/commit/b82e33492282911d22b5eb4091596c1eb88160da) |
| Moneta | [v1.2.0-rc3, `c4a6218`](https://github.com/JosephOIbrahim/Moneta/commit/c4a6218aabde668a7499cc7efd32e0e48e78bf83) |
| Octavius | [`1fbc090`](https://github.com/JosephOIbrahim/Octavius/commit/1fbc0907cb3dcec345d40c601ef9b2221480365d) |

The composed qualification used Houdini 22.0.400's Python 3.13 and USD 0.26.5.
Hanish itself remains a zero-runtime-dependency Python library. These source pins
record a tested combination, not a claim about all later revisions.

## Forecast and evidence contract

The host adapter records the narrow Boolean observable
`synapse.handler_terminal_ok.v1` with a fixed probability of `0.5`. Forecasts are
`EXPOSED`, are not calibration-eligible, and use an `IDENTIFIABLE` world
commitment. A HIT describes the handler returning without an explicit failure;
it does not measure render quality, artistic merit or predictive skill.

Before dispatch, SYNAPSE verifies the persisted forecast and its digest. A
terminal observation must match that attempt and digest. The adapter supplies
one immutable terminal event per attempt and rejects conflicting retries.
Timeouts, uncertain transport and missing observations stay unknown; expiry does
not turn missing evidence into a MISS. The host's observation horizon is separate
from the scene action's execution time.

Delivery recovery may reconcile a forecast already written before dispatch. It
never creates one after the action or replays the scene operation. Repeated
settlement preserves durable outcome identity; operational duplicate/comparison
counters can change and are not immutable outcome evidence.

Hanish remains the evidence ledger. SYNAPSE deposits a derived feedback capsule
into Moneta, allowing the next requested context lookup to recall it. Octavius
composes only the host's private context view. The host uses bounded isolated
subprocesses, keeping substrate waits outside Houdini's interface thread.

## Scope and verification

This optional observational host integration does not start the separate
Moneta/Octavius/Hanish temporal ablation, promote a Host 0 research receipt,
establish calibration, or implement recursive improvement. Existing Hanish
research gates and historical exclusions remain in force. Artist consent stays
with SYNAPSE's existing action path; memory observation grants no new authority.

The host-owned [adapter](https://github.com/JosephOIbrahim/Synapse/blob/v5.67.2/python/synapse/loop/hanish_worker.py)
and [configuration guide](https://github.com/JosephOIbrahim/Synapse/blob/v5.67.2/docs/MEMORY_LOOP_REPAIR.md)
define the concrete integration. From the pinned SYNAPSE checkout, configure
`MONETA_SRC`, `SYNAPSE_TEST_LOOP_PYTHON`, `SYNAPSE_TEST_HANISH_ROOT` and
`SYNAPSE_TEST_OCTAVIUS_ROOT`, then run:

```text
python -m pytest tests/test_loop_hanish_worker.py tests/test_loop_integration.py -q
```

The tests cover forecast-before-dispatch, digest binding, HIT/MISS/unknown,
conflicting evidence, lost acknowledgements, reopen and reuse. Explicit skips
for unavailable substrates are not a real-substrate pass. Keep project ledgers
and personal scene records outside this repository; the linked release states
the observed qualification and remaining production limits.
