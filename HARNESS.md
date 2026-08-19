# HARNESS — the team that hardens Hanish

> Provenance correction: this file began as the blueprint for a second host.
> The original session was operator-driven, and its synthetic `flight-past`
> evidence was not an autonomous CI capture. Its unavailable runtime root is
> not reconstructed. V0.2 makes GitHub Actions Host 0 and uses Host Ω as the
> hostile local conformance host; neither creates calibration data by default.

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
harness aimed to fill. The workflow below is a blueprint; only records retained
in an available ledger or promoted receipt are evidence that a run occurred.

## 2. The historical scout (evidence, not vibes)

Probed against the pre-v0.1.1 code on 2026-08-19:

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

P1–P5 and P7 were closed in v0.1.1. P6 and the namespace/Host 0 work were
carried into the v0.2 design rather than being misreported as finished.

Strengths retained by the work: append-only fsync ledgers; first-valid-terminal;
fail-closed expiry; domain blindness enforced by AST + vocabulary; and zero
runtime dependencies. Seed-variance replay is now a separate gate, not an
assertion inferred from an ordinary green test run.

## 3. The refactor — PAST > FUTURE > PRESENT

The codebase teaches its own grammar by structure. The dependency lattice is the
arrow — PAST is the only root:

```
hanish/
  time.py            the time vocabulary: now(), parse          [root]
  past/              what happened — append-only, replayable
    ledger.py        tail recovery, locking, schema tags
    events.py        ObservationEvent, CompletenessSeal, Outcome
  future/            what is claimed and scored later
    claims.py        Forecast, ResolutionSpec, Exposure, WorldRef, Adjudication
    scoring.py       compare, Brier, calibration honesty
  present/           the now — indexed state, health, sweep
    substrate.py     index, resolve, sweep, status
  adapters/
    ci.py            CI translation seam; Host 0 integration lands in v0.2
```

Import lattice (enforced, see law 4): `past` imports nothing above `time`;
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
- **State:** the original `harness/root/` was a gitignored runtime directory
  and is unavailable. Committed waves and this digest are interpretations, not
  replacement ledger bytes. New authoritative runs require promoted, hashed
  receipts under `experiments/receipts/`.
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
| `harness-self`   | Host Ω fixture + adapter contract | hostile conformance |

Waves, not all-at-once: the director activates 2–5 per work item. The roster is
the ceiling, not the concurrency.

## 5. The communication — agents that talk

- A fresh run may use an append-only runtime journal under `harness/root/`, but
  that ignored directory is operational state, not published evidence. Every
  authoritative run must be exported, verified, and promoted as a receipt.
- **Direct handoff** for dependent work (SendMessage): the director to a worker,
  the guardian attacking a live diff, a worker asking the verifier for a gate.
- **Single source of truth during a live run is that run's journal.** Across
  clones or releases, only committed waves, policy records, and verified
  receipts survive. No worker may fill a missing journal from prose.

## 6. The metrics (the only budget that keeps a change)

| gate | the metric | closes a work item when |
|---|---|---|
| G1  never-raise | hostile input suite | process() accepts garbage, never raises (P1/P2) |
| G2  tail-toler | torn-tail suite | substrate reopens, status admits a lost tail (P3) |
| G3  once-only | cross-process race | one event = one record (P4) |
| G4  schema | forward-open | old ledgers open, records versioned (P5) |
| G5  lattice | domain test | past←future←present + vocab grep (the refactor) |
| G6  scoreable | honesty suite | rates still separate closure/scoreable/capture |
| G7  replay | seed-variance replay gate | distinct hash seeds match the pinned semantic digest |
| G8  Host Ω | hostile adapter contract | local hostile host and CI adapter pass the same conformance suite |
| G9  ship | release check | ruff, supported Python tests, demo, package build, receipt verification |

The `verifier` runs these; the `guardian` attacks the claim; KEEP is entered by
the `verifier` alone, DISCARD needs a reason in the journal, UNRESOLVABLE is
honest: "budget exhausted, gate not met".

## 7. The frontier

Hanish is intended to become the epistemic spine for agent systems. These are
frontier goals, not claims about retained evidence:

1. **Self-measurement** — future hosts may score their own claims only when
   exposure and separation are structurally proved. The historical self-score
   is excluded; the published calibration corpus currently has zero eligible
   samples.
2. **Forensics by replay** — a full world commitment can make "who knew what
   when" checkable. Legacy truncated CI references are only `IDENTIFIABLE`, not
   `REPLAYABLE`.
3. **Namespaced multi-host** — CI, harness, build, render, arbitrary agents all
   emit into one fabric with sealed epochs and per-host world_refs.
4. **Calibration analytics** — Brier drift, per-actor trust scores, honesty
   reporting as a first-class artifact (the seam between "interesting" and
   "calibration data" already drawn by EXPOSED).

The frontier is not a new system. It is the same loop, hosted by more
worlds. The historical harness session is not promoted as the second world.

## 8. Phases

1. **Trust guard** — seed-variance replay is pinned before schema migration.
2. **Core trust** — structural exposure, monotone amendment, bounded capture,
   incremental resolution, terminal concurrency, and honest world references.
3. **Host 0** — independent matrix plan, stable identities, commit aggregation,
   completeness, G9 package build, and a candidate receipt.
4. **Promotion and hostility** — verify and promote the receipt add-only, then
   run the same contract against Host Ω.

**Definition of done:** G1–G9 are green, one live Actions candidate verifies
and is promoted without rewriting history, and no exposed forecast is reported
as calibration. Moneta + Octavius + Hanish ablation remains out of scope.
