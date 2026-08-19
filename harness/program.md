# program.md — the research org code

> The only file a human edits. Everything an agent does is steered by this.
> Editing this file IS programming the harness. Keep it short enough to hold
> in one screen and sharp enough that a cold agent can act on it.

## Mission

Harden the Hanish substrate into a trustworthy Host 0 instrument. Preserve the
PAST > FUTURE > PRESENT lattice, correct historical provenance, and require a
verified promoted receipt before claiming an authoritative run. The temporal
ablation is deliberately not part of this program.

## The world

- Hanish = a type system for time plus a scoreboard. Domain-blind core.
  Adapters import core; core imports nothing from adapters. Zero runtime deps.
- Ledgers are append-only JSONL, fsync per append. Everything derives from them.
- Two laws are non-negotiable, and the guardian attacks anything that touches them:
  - **OUTWARD** — the substrate may never raise into its host.
  - **INWARD** — incomplete evidence may never become knowledge (UNRESOLVABLE ≠ MISS).
- The refactor target and the metric gates are in `HARNESS.md`. Read it first.
- The historical `harness/root/*.jsonl` runtime was gitignored and is
  unavailable. Never infer or recreate its bytes. `harness/orchestrator.md`
  and `harness/roster.md` describe the operating blueprint; promoted receipts
  and the exclusion registry are the committed research artifacts.

## How to run

1. Open a session in this repo. Prompt: `director: read program.md and start the loop.`
2. The director starts a fresh runtime root, reads only retained evidence, picks
   the next open work item from the phase order in HARNESS.md, authors an
   honestly exposed forecast, and spawns a wave. Missing history stays missing.
3. The verifier runs the gates. The guardian attacks. KEEP only on green.
4. When the journal has no open items: the director writes `harness/digest.md`
   (state FINAL) per the digest spec and stands down. A CHECKPOINT is written at
   every flight boundary, so a crash anywhere in the loop wakes to the last
   interpretation, not a cold start. You wake up to a digest, not to a process.

## The plan (current)

- **Trust guard** — pin replay under distinct hash seeds before core migration.
- **Core trust** — structural exposure, monotone amendments, bounded capture,
  incremental resolution, terminal concurrency, and honest world references.
- **Host 0** — independent plan authority, stable run/leg identities,
  commit-level aggregation, completeness, CI capture/finalize, and G9 build.
- **Receipts + Host Ω** — verify and promote authoritative receipts add-only,
  then attack the same adapter contract with the hostile local host.

## The law the team answers to (exact)

1. never-raise | 2. fail-closed | 3. append-only | 4. domain-blind
5. adjudication precedes evidence | 6. zero-dep | 7. first-valid-terminal
8. EXPOSED is never calibration data. Keep this list alive everywhere.

## Metrics budget

Gates G1–G9 in `HARNESS.md` section 6 are the only budget that keeps a change.
Do not weaken a gate to ship a change — that is laundering, and it is what the
adjudicator is here to catch.

## Contract to the human

- Never edit a runtime ledger by hand or commit a mutable runtime root. A
  sealed, hashed receipt export may be promoted once under
  `experiments/receipts/`; it is thereafter immutable. The append-only
  `experiments/calibration-exclusions.jsonl` policy registry may grow only by
  complete, schema-valid records.
- A worker may only touch the file it owns (roster says which).
- The director is the only agent that authors forecasts.
- Never label a self-visible operational forecast as calibration. The first
  Host 0 receipt proves capture and completeness, not predictive skill.
