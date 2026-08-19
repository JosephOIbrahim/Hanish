# Hanish — Temporal Cognitive Substrate — V0.0

**One forecast. One external observation. One durable outcome. No scheduler.**

This is the smallest thing that can be wrong. It exists to be falsified cheaply,
not to be impressive.

```
python3 -m pytest tests/ -q     # 20 tests
python3 demo.py                 # three scenarios end to end
```

---

## What it is

A type system for time plus a scoreboard.

The core knows there are named observables, claims about them with target
times, an append-only evidence history, and outcomes. It knows nothing else —
not what any name means, not what units anything is in, not what actions
exist, not whether anything is good.

```
hanish/
  core/            domain-blind. imports stdlib and itself. nothing more.
    types.py       four identities, enums, frozen dataclasses
    ledger.py      append-only JSONL, fsync per record
    substrate.py   index, resolution, expiry sweep, health
  adapters/
    ci.py          the ONLY file that knows what a commit is
tests/
  test_v00.py               one test per frozen guarantee
  test_domain_blindness.py  dependency direction + vocabulary grep
demo.py
```

---

## The two failure directions

They point opposite ways, and confusing them is how these systems rot.

**Outward — fail open.** `capture()` never raises into the host. Losing an
observation is acceptable; breaking a build is not. Drops are counted, never
silent.

**Inward — fail closed.** Incomplete evidence never becomes knowledge. A
missing observation is `UNRESOLVABLE`, never `MISS`. Operational failure may
not be laundered into evidence against the model.

The mechanism that makes the second one real is the **completeness seal**: the
host asserts at a natural boundary that a stream is finished and emitted
exactly N records. Combined with per-source sequence numbers, a gap is
detectable. Without a seal, absence proves nothing and the substrate says so.

A drop counter alone is insufficient — it only knows about failures it saw.

---

## The four identities

Collapsing any two of these corrupts calibration.

| | Answers | Example |
|---|---|---|
| `source_ref` | who emitted it | `github-actions` |
| `event_id` | which emission is this | `run-481:attempt-2:required_checks` |
| `subject_ref` | what it's about | `git:abc123` |
| `world_ref` | what was known when authored | `world:d39bb3bb9a76` |

Dedup key is `(source_ref, event_id)` — uniqueness only ever holds within an
emitter's own scope, which is the only guarantee a distributed capture layer
can actually make.

Two CI attempts on one commit are **two events about one subject**. Not
duplicates. Only the first *valid* one scores; a later green rerun is recorded
and does not rescore. Retrying until green is the largest
calibration-laundering vector there is.

---

## Exposure

`BLIND` or `EXPOSED`, declared at authoring.

Intervention is only knowable if someone reports it. **Exposure is knowable
when the forecast is written and requires no reporting.** A forecast visible to
any actor that could move its target is presumed to have helped cause the
outcome, whether or not an intervention was reported.

`EXPOSED` forecasts resolve normally. They are never
`calibration_eligible`.

This costs three lines now and is expensive to retrofit later, because
retrofitting means discarding the calibration data already collected.

---

## No scheduler on the epistemic path

Resolution happens when someone asks — `process()` at a query, a flush, a CI
finalizer. Nothing requires a running process. Idle for three weeks and
nothing rots; the next call drains it.

The expiry sweep is housekeeping. It may lag arbitrarily and can never change
an outcome already recorded.

Everything derives from the three ledgers, so restart is free: reopen and
rebuild.

---

## Health — three numbers

Separating these is what lets `closure 99% / scoreable 71% / drop 0.1%` be told
apart from `closure 72% / scoreable 71% / drop 27%`. Very different failures.

- **closure_rate** — did the bookkeeping terminalize?
- **scoreable_rate** — did we actually learn anything?
- **capture integrity** — accepted, duplicates, dropped, sealed epochs

---

## Deliberately absent

Not oversights. Each belongs to a later version and adding it now would mean
freezing a design nothing has run against.

decay · calibration buckets · Brier aggregation · tiers · promotion · drift ·
model calls · Moneta · Octavius · distributed transport · policy layer ·
`InterventionEvent` · USD

Three enum fields carry exactly one legal value: `causal_mode`
(`OBSERVATIONAL`), `adjudication` (`FIRST_VALID_TERMINAL`), and
`accept_validity` (`VALID`). The fields exist because identity and contract
shape are expensive to retrofit. The value sets stay at one until a version
actually exercises the alternatives — a field with one legal value costs a byte
and locks in nothing.

---

## Frozen here

Changing any of these later means discarding data.

- four-identity model
- ledger immutability and append-only history
- fail open outward / fail closed inward
- resolution contract declared before evidence arrives
- exposure declared at authoring
- indexed resolution — never a scan of history
- dependency direction: core imports no adapter

## Next

**V0.1** — source identity across processes, at-least-once transport,
restart under kill -9, bounded spool.
**V0.2** — Host Ω adversarial conformance harness, fault injection,
out-of-order, gaps, schema evolution. **Hard gate: V0.3 cannot begin until
every V0.2 gate is green.**
**V0.3** — model-authored forecasts, sealed BLIND by construction. First
publishable result.
