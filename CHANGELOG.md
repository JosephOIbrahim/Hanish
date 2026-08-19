# Changelog

All notable changes to Hanish. Append-only, like the ledgers: a correction is
a new entry, never an edit to an old one.

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
