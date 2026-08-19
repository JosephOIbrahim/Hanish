# Changelog

All notable changes to Hanish. Append-only, like the ledgers: a correction is
a new entry, never an edit to an old one.

## 0.1.2 — the waves

Two harness wave records land. No package change: `hanish/` is untouched and
the 41 tests are exactly what 0.1.1 shipped. What grew is the evidence layer —
the harness pointed at other repos, and at the external verdict, and both times
it found the system's own docs were less honest than the system's claims.

### The memory wave — `harness/waves/memory-scan.md` (commit `ae42274`)

The first wave aimed at the fleet: SYNAPSE, Moneta, Octavius, Harlo, SALUS,
cognitive-substrate. Six scouted, six adversarially challenged, **eight
mechanisms survived** into a scaffold. The deepest is the hash-chain root
commitment — a same-length byte flip that still parses as JSON is read as truth
today, the third damage class the ledger cannot see. It sequences behind a
seed-variance determinism gate (`tests/test_replay_determinism.py`), which
converts "deterministic replay" from assertion into proof and becomes the guard
every later change ships behind. The wave recorded its own damage with the same
honesty: run 1 lost five challenges plus the plan to an API 429 and was resumed
13/13; a classifier warning on the SYNAPSE scout was re-checked and the
surviving transfers re-verified per-file.

### The v7 adjudication — `harness/waves/verdict-adjudication.md` +
`adjudication-wave.js` (commit `2cec768`)

An external evaluator scored HANISH 8.5/10 on the V0.0–V0.1 foundation and
~3/10 on the full v7 program. The wave neither defended the repo nor accepted
the verdict — it adjudicated: 9 agents (4 scouts, 4 challengers, 1
synthesizer), every claim checked against the code with file:line, **9 of 12
survived**. The strongest findings were the ones the verdict missed:

- **The docs asserted a proof that never happened.** HARNESS.md and the digest
  both claim a CI result was captured; the evidence record carries a fabricated
  `run_id` ("flight-past"). Absence laundered into an assertion, by the
  system's own documentation.
- **The first self-forecast is not BLIND.** The instrument's only calibration
  sample was authored by the identity that directed the flight — and is already
  scored calibration-eligible HIT. Exposure integrity is a live defect, not a
  policy question.

Build order: **R2** (doc-honesty) and **R3** (exposure amendment) land **NOW**;
**R4–R8** (committed receipts, cold-capture watermark, the L7 hazard folded
into P6, WorldManifest-or-downgrade, the merged CI-host item with sequence
authority) land **NEXT**; all behind the determinism gate. The triad stays
deliberately not next until the instrument is trustworthy.

This entry exists because a landing without a changelog line is a landing that
did not happen. The waves landed; this is the formal record.

## 0.1.1 — the guardian verdict

The guardian attacked the v0.1.0 diff and found the laws were broken. Three
P0s, three mediums, four lows — all verified with live probes, all closed
here, each with a regression test in `tests/test_guardian.py`. The gates
were green and the laws were still broken; the gates are now wrong.

**P0-1 — a damaged record never bricks a rebuild.** `_rebuild` decoded each
ledger record bare. A record that parsed as JSON but failed its decoder
(missing fields, garbage enums) raised out of `Substrate.__init__`, and one
hostile record bricked every future reopen. Decoders are now guarded: a
record this build cannot interpret is corruption — skipped, counted
(`corrupted`), never a brick. G4's loud failure for a *newer* schema version
is preserved.

**P0-2 — a poison observation cannot deny resolution.** `accepts()` and the
`arrived_at`/`horizon` comparison sat outside the per-observation guard. One
garbage `validity` or `arrived_at` aborted the whole `process()` pass — and,
because the poison persists, every later pass too: a permanent, invisible
denial of resolution. They are now inside the guard, counted once
(`invalid_compare`), and the pass keeps draining.

**P0-3 — completeness is scoped to the observable's channel.** A seal used
to complete any stream whose epoch matched the forecast's subject. A seal
from a source that never emits the forecast's observable could turn absence
into MISS. `ObservableSpec` now declares which `sources` emit it; a seal
from any other source says nothing and absence stays UNRESOLVABLE.

**M-1** repair's read+truncate now runs under the append lock, so a
concurrent writer's record can never be eaten by a stale truncate. The
stress test that proved it found the lock protocol itself broken: the
contention probe (`_lock.__enter__` reading byte 0 to seed the byte the
runtime needs) collided with Windows' mandatory byte-range lock — while a
holder owns byte 0, every other handle that *reads* it raises
PermissionError instead of waiting. The probe now uses `getsize` (directory
metadata, region-free) and the seed write lands at EOF (append mode), so
contenders only ever wait on the lock itself.
**M-2** forecasts and outcomes are now `_v`-tagged and version-gated like
evidence — a future writer on any ledger fails loud. **M-3** an
UNRESOLVABLE closure is housekeeping, not a verdict: valid in-time evidence
may reopen it; a settled RESOLVED never changes. **L-1** `status(at)` no
longer raises on a malformed clock. **L-2** a whitespace torn tail is
truncated and counted like any other. **L-4** dead code (`locked()`,
`read()`) removed.

### Law status

The eight laws stand again. The guardian verified the soundness of
cross-process dedup, tail accounting, and domain-blindness while it was
here. The digest's "eight laws standing (guardian audit pending)" was
premature — it is amended in `harness/digest.md`.

## 0.1.0 — the lattice

The refactor target from HARNESS.md. The core becomes an explicit lattice,
oldest to newest, with no upward import edges:

    time ← past ← future ← present ← adapters

**Split.** The monolith becomes four layers. `time` knows only now/parse.
`past` owns the append-only ledgers and the event vocabulary. `future` owns
forecasts, resolution specs, and scoring — pure declaration, no I/O. `present`
composes both into the substrate. Adapters translate; nothing in the core
imports an adapter.

**Damage tolerance (G2).** The ledger distinguishes a torn tail from mid-file
corruption. A torn tail is a write that died before its newline: truncated
away and counted (`tail_loss`). A bad interior line is skipped and counted
(`corrupted`) — never fabricated, never a launder into a verdict. Both are
surfaced by `status()`; neither bricks a reopen. The integrity pass runs once,
at construction (`repair()`); replay (`raw()`) is read-only and never
re-counts.

**Cross-process once-only (G3).** Dedup decides under the append lock against
the file, so two hosts ingesting the same envelope cannot both win.

**Schema versioning (G4).** Records carry `_v`. Untagged records read as v1; a
record from a NEWER writer fails loud rather than being silently misread.

**Never-raise (G1).** `process()` and `capture()` are guaranteed not to raise
into the host. Malformed evidence is counted (`invalid_compare`,
`process_errors`) and surfaced, never thrown and never scored.

**The harness begins.** A 20-role hardening team runs on the substrate itself
(`harness/`), including the first self-forecast authored against the harness
commit. The digest (`harness/digest.md`) is the loop's handoff: you wake up to
a digest, not to a process.

### Gates landed

| gate | name | check |
|---|---|---|
| G1 | never-raise | `pytest -q tests/test_outward.py` |
| G2 | tail-toler | `pytest -q tests/test_tail.py` |
| G3 | once-only | `pytest -q tests/test_concurrency.py` |
| G4 | schema | `pytest -q tests/test_schema.py` |
| G5 | lattice | `pytest -q tests/test_domain_blindness.py` |
| G6/G7 | scoreable + replay | full suite + ruff + demo green |

## 0.0.0 — the seed (pre-release)

V0.0.0 was never published. The package grew through three commits under the
name `temporal_substrate`, then renamed to `hanish`: package layout, the
20-test guarantee suite (`tests/test_v00.py`), CI wiring, and a first design
that treated the whole system as a type system for time. The 20 tests survive
unchanged into 0.1.0 — replay-identical is a gate, not a coincidence.
