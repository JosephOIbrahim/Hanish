# digest.md — the handoff

> **The last artifact a session writes; the first one it reads.**
> The ledgers are the evidence; this is the interpretation. A session that
> ends in silence loses its history; a digest that reads like a ledger loses
> its reader. This file is both the *spec* — what every digest must be — and
> the *instance* — what the director fills in as the loop runs. Old instances
> live on in git: the digest is append-only by commit.

## The Digest law

> You wake up to a digest, not to a process.

1. **COMPLETE** — a cold agent reconstructs all state from this file plus
   the artifacts it cites; no journal spelunking.
2. **HONEST** — a loss is inked with the same weight as a win: a gate not
   KEPT, a discard without a reason, a regression, an open thread. Omitting
   a loss is laundering it.
3. **TRACEABLE** — every claim carries a citation: a command that proves it,
   a path that holds it, a commit that pins it. An uncited claim is a rumor.
4. **BOUNDED** — this is the index, not the ledger. Each section has a size
   budget; a digest that grows forever stops being read.
5. **CALIBRATED** — it reports what the substrate itself says about the team:
   closure, scoreable, capture integrity, verdict tally. A system that does
   not measure itself does not learn.

## Three states, one file

The digest is a snapshot of one moment, not a history. Its state field says
which moment:

| state | when | what the header says |
|---|---|---|
| **INTENT** | the journal opens, before work | what the team believes it will land |
| **CHECKPOINT** | every flight boundary | what has landed since the last one |
| **FINAL** | stand-down, no open items | the whole run, complete |

A crash anywhere in the loop is recoverable by the pair: the journal holds the
evidence, the last digest holds the interpretation. That pair is the harness's
durability.

## The wake test — a digest passes only when

A cold session reads this file, then from the digest alone:

1. states in one sentence what the system is and what state it is in;
2. runs the verify command below and gets green;
3. names the next open thread and why it is next.

Any failure means the digest is defective — rewrite it before standing down.

## The verify command (wake test, step 2)

```
python -m pytest tests/ -q && python -m ruff check hanish/ tests/ demo.py && python demo.py
```

A digest shipped while this fails is a digest that lies about its own state.

## The skeleton every instance must fill

| § | section | budget | filled by |
|---|---|---|---|
| 1 | header — state, date, digest v, covering (commit span), replaces | ≤ 5 lines | director |
| 2 | what this system is — one sentence, for a human who forgot | 1 line | director |
| 3 | standing — laws, gates, open items, regressions | ≤ 12 rows | director |
| 4 | what landed — per flight, one row: item → gate → file → commit | 1 row/item | director |
| 5 | what was discarded — per discard: item → claim → why → retry | "none" is honest | director |
| 6 | the numbers — the substrate on the team | status table | director |
| 7 | the frontier — the team's forecast about itself, resolved | ≤ 6 lines | director |
| 8 | open threads — parked, regressions, un-landed flights | 1 row each | director |
| 9 | resume protocol — the exact commands to wake, verify, pick next | ≤ 15 lines | director |
| 10 | teach-down — for the human: run, see, break, live | ≤ 15 lines | docs-writer |

A row in §5 must carry a reason. `none` is an answer and it must be true.

---

## Instance — the live one

```
state:     CHECKPOINT · Flight PAST landed
date:      2026-08-19
digest v:  1
covering:  hanish/past/ledger.py (repair/raw split), tests G1–G5
replaces:  — (first instance)
```

**1. What this is.** A domain-blind calibration substrate: append-only
ledgers that rebuild all state on open, forecasts that resolve against
observable streams, and — the frontier — a harness that runs the team on the
substrate itself.

**2. Standing.** Eight laws standing — now *audited*, and the audit caught
them broken. **Amendment:** the previous instance's "guardian audit pending"
was resolved the hard way: the guardian attacked the v0.1.0 diff and found
the gates green while the laws were broken. Three P0s (damaged record bricks
rebuild; poison observation denies resolution; foreign seal launders absence
into MISS), three mediums, four lows — all verified with live probes, all
closed in 0.1.1 with a regression test each (`tests/test_guardian.py`). The
stress test that proved M-1 then found a fifth concurrency law-break: the
lock protocol's byte-0 probe collided with Windows' mandatory byte-range
lock (concurrent contender raised instead of waiting). All closed; the gates
that were green-and-wrong are now red-and-right. Gates G1–G9 green by
`pytest tests/ -q` (41), ruff, demo. G8 (host #2 adapter) not built. G9
green: `hanish-0.1.1` builds, released on GitHub (`v0.1.1`). Open flights: 2.
Regressions: 0.

**3. What landed.**

| flight | item | gate | file | commit |
|---|---|---|---|---|
| PAST | lattice split (past←future←present) | G5/G7 | `hanish/past/` `future/` `present/` `time.py` | *this flight, unpushed* |
| PAST | torn-tail + corruption accounting | G2 | `hanish/past/ledger.py` — `repair()` vs `raw()` | *unpushed* |
| PAST | cross-process once-only | G3 | `append_observation_once` under the lock | *unpushed* |
| PAST | schema versioning | G4 | `_v` tag, future-version fails loud | *unpushed* |
| PAST | never-raise `process()` | G1 | `present/substrate.py` — OUTWARD/INWARD | *unpushed* |
| GUARDIAN | P0-1 damaged record never bricks rebuild | G2 | `substrate.py _rebuild` — guarded decoders | *this flight* |
| GUARDIAN | P0-2 poison obs can't deny resolution | G1 | `_resolve_from_evidence` per-obs guard | *this flight* |
| GUARDIAN | P0-3 seals scoped to observable's source | G5 | `ObservableSpec.sources` + `_stream_complete` | *this flight* |
| GUARDIAN | M-1..M-3, L-1/L-2/L-4 + the lock probe | G2/G3/G4 | `ledger.py`, `status()`, `_version_ok` | *this flight* |
| GUARDIAN | regression suite, 10 tests that bite | — | `tests/test_guardian.py` | *this flight* |

**4. What was discarded.** None under the KEEP/DISCARD protocol (first real
flight). The G2 tests caught a genuine ledger defect — the torn-tail check
keyed off the last split element (which a trailing newline makes empty) and
damage counters double-counted across the `_count` and rebuild passes. That is
fixed, not discarded.

**5. The numbers.** 41 tests pass — the 31 of v0.1.0 plus the guardian's 10,
each a regression that would have caught the release. ruff clean; demo runs;
package builds (`hanish-0.1.1`). The substrate's own root (gitignored) holds
one authored forecast, resolved: **HIT**, brier 0.0625 — the team's first
self-measurement by its own instrument.

**6. The frontier.** `harness/root` (host #2, bootstrapped) carried the first
self-forecast: `f_c57a1c3bc61f`, BLIND, p=0.75, claim "flight-past-1 lands
G1–G5 and G7 green", subject `git:e1f10f0`. Every claimed gate is green, the
CI result was captured, and the substrate resolved it HIT. The loop the
harness exists to prove is closed: the harness forecast its own landing and
was scored on it.

**7. Open threads.**

| thread | why next |
|---|---|
| flight PRESENT → FUTURE → HOST-2 | G8 host #2 is the remaining gate |
| this digest → FINAL | when the journal has no open items |

**8. Resume protocol.** Boot per `orchestrator.md`. Verify:
`python -m pytest tests/ -q && python -m ruff check hanish/ tests/ demo.py && python demo.py`.
Next thread: G8 — a second host adapter (the only gate not yet proven), then
the journal resolve. The guardian is done for this diff; it moves to the next
landing.

**9. Teach-down (human).** The substrate is a scoring notebook that trusts
nothing and loses nothing. `demo.py` shows it in one read: author a forecast,
capture observations, process. Everything is rebuilt from three append-only
JSONL ledgers — delete them and it forgets; a crash mid-write and the ledger
repairs itself on reopen. When it breaks, the first move is `status()`; the
counters (tail_loss, corrupted, dropped, invalid_compare) tell you which kind
of damage you're looking at.
