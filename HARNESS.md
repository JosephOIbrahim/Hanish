# HARNESS — the team that hardens Hanish

> Position statement: the harness is Hanish's **second host**. CI proved the loop
> on a real external stream. This harness runs the loop on the team itself — the
> ledger is the memory, the director authors forecasts, the gates resolve them.
> A substrate that cannot instrument its own hardening does not deserve to be called
> a substrate.

## 1. The honest read on autoresearch

`karpathy/autoresearch` is **not** a multi-agent framework. It is a minimal
single-agent training loop: a human edits `program.md` ("the research org code"),
one agent edits one file (`train.py`), runs fixed 5-minute experiments, checks a
single metric (`val_bpb`), keeps or discards the change, and a log is left for
review. No director, no team, no inter-agent messaging.

What transfers is the **spine**, not the machinery:

| autoresearch primitive | Hanish harness equivalent |
|---|---|
| `program.md` steers the org | `harness/program.md` — the only file a human edits |
| one agent owns one file | each worker owns exactly one subsystem |
| fixed time budget + one metric | each work item has a budget + a metric gate |
| keep / discard by metric | the director keeps a change only when the gate passes |
| log left for the morning | the journal + a human digest |
| context as markdown, not code | `program.md` + `orchestrator.md` carry the context |

The 20-agent team, the orchestration, and the agents talking to each other are
**additions** — autoresearch deliberately has none of them. That is the gap this
harness fills, and it fills it the way Hanish would: every opinion becomes an
observation, every decision a forecast, every verdict real.

## 2. The scout (evidence, not vibes)

Probed against the live code on 2026-08-19:

- **P1 — `process()` breaks the OUTWARD law.** `capture()` is wrapped; `process()`
  is not. A host emitting a `"garbage"` string on a bool observable with a `GT`
  contract: `TypeError: '>' not supported between instances of 'str' and 'bool'`
  propagates **into the host**. `value_type` on `ObservableSpec` is declared and
  never enforced.
- **P2 — naive horizon crashes the sweep.** `parse("2026-08-19T12:00:00")` vs
  aware `now()`: `can't compare offset-naive and offset-aware datetimes`.
- **P3 — a torn tail bricks the whole substrate.** One partial JSON line in a
  ledger makes `_rebuild()` raise; the constructor dies; `capture()` — the one
  path guaranteed not to raise — becomes unreachable.
- **P4 — no cross-process effect-once.** No file locking; `_seen` dedup is
  per-process; two hosts capturing the same event write it twice.
- **P5 — no schema version.** A V0.1 required field raises on V0.0 ledgers.
- **P6 — resolution re-walks all observations on every `process()`.** "Indexed not
  scanned" is true for forecast lookup, not for the observation side.
- **P7 — repo hygiene.** stale `temporal_substrate.egg-info/`, caches in-tree;
  `.gitignore` incomplete.
- **P8 — no host namespace.** Observable names are global; a second host collides
  with CI's flat `observables` dict.

Strengths (unchanged by this work): append-only fsync ledgers; index-not-scan for
forecast lookup; first-valid-terminal; fail-closed expiry; domain blindness
enforced by AST + vocabulary; zero runtime deps; deterministic replay.

## 3. The refactor — PAST > PRESENT > FUTURE

The codebase teaches its own grammar by structure. The dependency lattice is the
arrow — PAST is the only root:

```
hanish/
  shared.py          the time vocabulary: now(), parse(), Validity, Emission,
                     Comparator. Imports nothing.              [root]
  past/              what happened — append-only, replayable
    ledger.py        + tail recovery (P3), locking (P4), schema tags (P5)
    events.py        ObservationEvent, CompletenessSeal, Outcome
    version.py       schema versioning + migration policy
  future/            what is claimed — authored blind, scored later
    claims.py        Forecast, ResolutionSpec, Exposure, WorldRef, Adjudication
    scoring.py       compare, Brier, calibration honesty
  present/           the now — indexed state, health, sweep
    substrate.py     index, resolve, sweep, status
    health.py        telemetry, digest, advisories
  adapters/
    ci.py            host #1
    harness.py       host #2 — built by this harness's own first delivery
```

Import lattice (enforced, see law 5): `past` imports nothing above `shared`;
`future` imports `past`; `present` imports `past` + `future`. No adapter
imports into `past`/`future`/`present`. The domain-blindness test walks all
three dirs and the vocabulary grep still bites.

## 4. The harness — shape

**Shape:** hybrid — a code agent (it edits code) whose top is a workflow
orchestrator (phases, gates, KEEP/DISCARD). The human is the executive; the
director is the operator; the workers are the bench.

- **Entrypoint:** `claude -p` or this session with `harness/program.md` as the
  instruction.
- **Orchestrator:** the `director` agent. Not a worker. Decides the next work
  item, authors a forecast before it, spawns the owning worker(s), scores.
- **Capability registry:** `.claude/agents/*.md` — 19 roles + director, each
  with a tool whitelist and a denied list.
- **State:** the journal (append-only, in the ledger of the harness) + the
  substrate root under `harness/root/`. The journal IS memory; there is no
  other.
- **Context:** `program.md` + `orchestrator.md` + the journal tail are loaded
  at boot; the director assembles per-item context for each worker.
- **Permission:** all mutating writes go through the director's keep verdict;
  workers never commit; the `guardian` never edits.
- **Evaluation:** `harness/metrics.md` (below) is the metric gate; every work
  item is kept or discarded against it; `verifier` runs it.

## 4. The team

`director` orchestrates. 19 workers, three flights + cross-cutting, each one
file it owns, each a law it guards:

| role | owns | guards |
|---|---|---|
| `ledger-doctor`  | past/ledger tail recovery, fsync | P3 |
| `crash-scribe`   | multi-process capture, locking | P4 |
| `replay-smith`   | startup perf, checkpoints | scaling |
| `schema-keeper`  | record versioning, migration | P5 |
| `host-shield`    | process() never-raise, value_type | P1/P2 — the law |
| `index-smith`    | high-water marks, O(new) | P6 |
| `health-watch`   | status(), telemetry, digest | honesty of rates |
| `clock-court`    | UTC/naive/skew | clock law |
| `claim-mint`     | Forecast/contracts/world_ref | claims are blind |
| `scoring-scan`   | Brier, calibration | scoreable honesty |
| `adjudicator`    | first-valid-terminal, laundering | the anti-retry law |
| `namespace-planner` | host namespaces | P8 |
| `frontier-architect`| the PAST/PRESENT/FUTURE split | the lattice |
| `guardian`       | attacks every claim | the adversary |
| `verifier`       | runs gates, tests, ruff | metrics are true |
| `critic`        | law review of every diff | the constitution |
| `docs-writer`    | ADRs, README, operator card | teach-down |
| `release-ship`   | CI, PyPI, versioning | P7 hygiene |
| `harness-self`   | adapters/harness.py (host #2) | the recursion |

Waves, not all-at-once: the director activates 2–5 per work item. The roster is
the ceiling, not the concurrency.

## 5. The communication — agents that talk

- The **journal** (append-only JSONL under `harness/root/` via the substrate):
  every observation a worker posts, every forecast the director authors, every
  verdict the gates emit.
- **Direct handoff** for dependent work (SendMessage): the director to a worker,
  the guardian attacking a live diff, a worker asking the verifier for a gate.
- **Single source of truth is the journal.** No worker may trust what it says in
  a channel; it must read the journal and the substrate it was given.

## 6. The metrics (the only budget that keeps a change)

| gate | the metric | closes a work item when |
|---|---|---|
| G1  never-raise | hostile input suite | process() accepts garbage, never raises (P1/P2) |
| G2  tail-toler | torn-tail suite | substrate reopens, status admits a lost tail (P3) |
| G3  once-only | cross-process race | one event = one record (P4) |
| G4  schema | forward-open | old ledgers open, records versioned (P5) |
| G5  lattice | domain test | past←present←future + vocab grep (the refactor) |
| G6  scoreable | honesty suite | rates still separate closure/scoreable/capture |
| G7  replay | the 20 V0.0 tests | all green, only import paths changed |
| G8  host #2 | self-forecast | adapters/harness.py authors, resolves, calibrates |
| G9  ship | release check | ruff, pytest 4×python, demo, package builds |

The `verifier` runs these; the `guardian` attacks the claim; KEEP is entered by
the `verifier` alone, DISCARD needs a reason in the journal, UNRESOLVABLE is
honest: "budget exhausted, gate not met".

## 7. The frontier

Hanish is the epistemic spine for agent systems:

1. **Self-measurement** — the harness IS host #2; its own claims get scored by
   its own evidence; the team's calibration is a real number.
2. **Forensics by replay** — REPLAYABLE world_refs make "who knew what when"
   checkable; the substrate stops being a scoreboard and becomes the epistemic
   layer under any agent team — a cognitive audit trail.
3. **Namespaced multi-host** — CI, harness, build, render, arbitrary agents all
   emit into one fabric with sealed epochs and per-host world_refs.
4. **Calibration analytics** — Brier drift, per-actor trust scores, honesty
   reporting as a first-class artifact (the seam between "interesting" and
   "calibration data" already drawn by EXPOSED).

The frontier is not a new system. It is the same loop, hosted by more
worlds, with the harness itself as the second world.

## 8. Phases

1. **Flight PAST** — frontier-architect executes the split under guardian attack;
   ledger-doctor/schema-lock/crash-scribe/replay-smith land G1–G5.
2. **Flight PRESENT** — host-shield, index-smith, health-watch, clock-court land
   G1/G6 + perf.
3. **Flight FUTURE** — claim-mint, scoring-scan, adjudicator land the scoring
   universe + G6.
4. **Cross-cutting** — critic clears every diff; docs-writer writes the
   teach-down; release-ship lands G9 and P7 hygiene.
5. **Frontier (host #2)** — namespace-planner + harness-self land G8: the
   journal becomes a real substrate; the director authors its first forecast.

**Definition of done:** G1–G9 green, digest written, the harness's own first
forecast resolved by its own evidence, and the teach-down delivered at sea level.
