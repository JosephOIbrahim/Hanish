# program.md — the research org code

> The only file a human edits. Everything an agent does is steered by this.
> Editing this file IS programming the harness. Keep it short enough to hold
> in one screen and sharp enough that a cold agent can act on it.

## Mission

Harden the Hanish substrate, refactor it to PAST > PRESENT > FUTURE, and prove
the frontier: the harness runs ON the substrate. A substrate that time itself
can't audit is not a substrate.

## The world

- Hanish = a type system for time plus a scoreboard. Domain-blind core.
  Adapters import core; core imports nothing from adapters. Zero runtime deps.
- Ledgers are append-only JSONL, fsync per append. Everything derives from them.
- Two laws are non-negotiable, and the guardian attacks anything that touches them:
  - **OUTWARD** — the substrate may never raise into its host.
  - **INWARD** — incomplete evidence may never become knowledge (UNRESOLVABLE ≠ MISS).
- The refactor target and the metric gates are in `HARNESS.md`. Read it first.
- The journals are `harness/root/*.jsonl`. The director's workflow is
  `harness/orchestrator.md`. Current team roster is `harness/roster.md`.

## How to run

1. Open a session in this repo. Prompt: `director: read program.md and start the loop.`
2. The director boots the substrate, reads the journal, picks the next open work
   item from the phase order in HARNESS.md, authors a forecast, spawns a wave.
3. The verifier runs the gates. The guardian attacks. KEEP only on green.
4. When the journal has no open items: the director writes `digest.md` and stands
   down. You wake up to a digest, not to a process.

## The plan (current)

- **Flight PAST** — split the package into past/present/future (frontier-architect),
  land tail-recovery + locking + schema (ledger-doctor, crash-scribe, replay-smith,
  schema-keeper).
- **Flight PRESENT** — never-raise `process()`, type enforcement, high-water marks,
  honest health (host-shield, index-smith, health-watch, clock-court).
- **Flight FUTURE** — contracts, scoring, adjudication (claim-mint, scoring-scan,
  adjudicator).
- **Flight HOST-2** — namespaces + harness adapter (namespace-planner, harness-self).
- **Flight SHIP** — docs, release, hygiene (docs-writer, release-ship).

## The law the team answers to (exact)

1. never-raise | 2. fail-closed | 3. append-only | 4. domain-blind
5. adjudication precedes evidence | 6. zero-dep | 7. first-valid-terminal
8. EXPOSED is never calibration data. Keep this list alive everywhere.

## Metrics budget

Gates G1–G9 in `HARNESS.md` section 6 are the only budget that keeps a change.
Do not weaken a gate to ship a change — that is laundering, and it is what the
adjudicator is here to catch.

## Contract to the human

- Never edit a ledger by hand. Never commit a ledger artifact.
- A worker may only touch the file it owns (roster says which).
- The director is the only agent that authors forecasts.
- You keep the secret: the whole system is a forecast. The first one the
  harness authors about its own work is the threshold of the frontier.
