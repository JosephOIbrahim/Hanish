# Wave record — memory-substrate scan (WAVE-SCAN-2026-08-19)

> The first wave run against other repos. Append-only by commit, like the
> ledgers: a correction is a new record, never an edit.

## State

```
state:     COMPLETE — verdicts delivered, plan landed
date:      2026-08-19
covering:  6 repos scouted, 6 challenged, 8 mechanisms survived, 1 plan
workflow:  scout → adversarial-challenge (refute-or-die) → synthesize
runs:      2 (first run lost 5 challenges + plan to an API 429; resumed)
```

## The design (how it executed, per first principles)

A wave, not a single reader, because a single reader cannot be trusted to
tell Hanish what it lacks. Three phases:

1. **Scout (read-only).** Six independent scouts, one per repo, each told:
   READ-ONLY, ONE-DIRECTION (find what could strengthen HANISH, nothing else),
   and to inventory mechanisms with a `duplicateOfHanish` self-check — a
   mechanism already in Hanish is cheaper to say so than to port.
2. **Challenge (adversarial).** One challenger per scout's inventory. Charter:
   *refute.* "Nothing survives unless you are forced." Every claim is checked
   against the actual code (file re-opened, line cited), against the eight
   laws, and against the gates. A claim that duplicates an existing
   mechanism, drags a dependency into the zero-dep core, or launders absence
   into a verdict is dead.
3. **Synthesize.** One synthesizer over the survivors: rank by
   effort × law/gate value, sequence into a scaffold (NOW/NEXT/LATER), and
   give a honest single next step.

Why this shape: it is the same adversarial discipline that produced
`tests/test_guardian.py` — a claim is not true because an agent asserted it;
it is true because an adversary could not kill it.

## Repos scanned (6)

SYNAPSE, Moneta, Octavius, Harlo, SALUS, cognitive-substrate.

## Verdicts — what survived the challenge (8)

| survivor | source | what it is |
|---|---|---|
| Hash-chain root commitment | Harlo (MerkleRootPrim, root half) | third damage class: a same-length byte flip that still parses as JSON is currently read as truth |
| Seed-variance differential gate | SALUS (determinism.py) | sibling processes under different PYTHONHASHSEED, digests over the rebuild compared → "deterministic replay" proven, not asserted |
| Writer-must-match-reader seam | SALUS (replay.py/shim.py/adapter_equivalence.py) | emission side rejects what the reader's repair would reject; the shape of host #2 |
| Per-item lossless round-trip | SYNAPSE (evolution.py `_verify_lossless`) | re-parse + hash each emitted record, names the drifted one, binary fidelity |
| Durable pending-adjudication queue | Harlo (ElenchusQueue) | score travels separately from verdict; pending→terminal once-only |
| Forced-promotion verify-revise loop | Harlo (run_gvr) | hard cap forces any loop to terminal; status() carries what-would-help |
| Lexical first-valid intent gate | Harlo (detect_spec_gaming) | spec-gamed forecast cannot win the permanent first-valid slot |
| Copy-and-verify migration, hard gate | SYNAPSE (migrate.py) | tool waiting for host #2 — ships after, not before |

## Notable refutations (why they died)

The refutations are the calibration. The strongest near-transfers that did
NOT survive, with the reason:

- **Moneta Forge/Crucible split** — Hanish's guardian charter is already
  exactly Crucible: adversarial, never edits code, a finding is a finding,
  prove the bite, hostile tests pinned in `test_guardian.py`.
- **Harlo replay-then-archive compaction** — law-3 poison: it DELETEs active
  rows and collapses the per-record identity first-valid-terminal and
  completeness seals depend on.
- **Harlo hot/warm tiering** — breaks "rebuild ALL state on open" in both
  directions; drags an ONNX encoder dep.
- **SALUS counterfactual fork no-op proof** — structurally vacuous in SALUS
  itself (`compute_vitals` never reads events); read-only is already
  structural in Hanish.
- **SYNAPSE degraded-load quarantine** — the crypto/fingerprint sidecar
  would violate law 6; Hanish repairs in place, never wholesale-rewrites.
- **Octavius measurement completion probe** — needs an expected-slot
  denominator no ledger carries; absence-as-MISS is law-2 laundering.
- **cognitive-substrate typed-record taxonomy** — duplicates the `_kind`
  tag + closed dispatch in `_rebuild`; the OO hierarchy is ceremony.
- **SYNAPSE statistical silence floors** — denying law 2 (fail-closed must
  alarm on the FIRST failure, not the tenth).
- **Moneta swap-and-drain channel / WAL-lite** — Hanish's capture() is
  synchronous and fsync-durable; a snapshot+WAL tier would add a silent
  accepted-loss window — the opposite of the counted-loss contract.

**Which gate/law each died on matters more than the name.** The challenge
grid was: (1) is the code real, (2) does Hanish already have it, (3) does
it violate a law, (4) does it need a dep, (5) does it launder.

## The scaffold plan (synthesizer's verdict)

**NOW — the two integrity guarantees, the deepest holes the wave found.**
Both turn a claimed strength into an enforced one and everything else rides
on them:

1. **Hash-chain root commitment.** In an append-only ledger the O(log n)
   Merkle collapses to O(1): `root_n = H(root_{n-1} || record_digest)`,
   committed per ledger as a seal-side record at epoch end (law 3 holds —
   appended, never edited), recomputed on rebuild, mismatch counted as a
   THIRD honest damage class. Closes the same-length-silent-flip hole that
   currently launders as truth (law 2). *Effort: medium. Gate: G2/G4.*
2. **Seed-variance differential gate.** CI today is a plain 4-python
   pytest+ruff+demo with zero seed variance, yet HARNESS.md claims
   "deterministic replay." A gate that rebuilds the ledger in two sibling
   subprocesses under PYTHONHASHSEED=1 vs 2 and compares sorted-state
   digests converts the assertion into a proof in seconds. *Effort: small.
   Gate: G7 — and it becomes the guard every later change ships behind.*

**NEXT — the documented frontier lands with its discipline (G8, host #2).**

3. Writer-must-match-reader seam: the adapter validates its typed boundary
   before appending — rejecting the shapes the reader would reject — and a
   lossless replay-equivalence gate re-parses + hashes every emitted record.
   Host #2 becomes the reference adapter every future host copies.
4. Durable pending queue + forced-promotion cap + lexical first-valid gate:
   once a second host's adjudication loop exists, it gets the same once-only
   guarantee the ledger already gives events.

**LATER.** Copy-and-verified migration — a tool waiting for a box; ships
when there is something to move.

No proposal touches the zero-dep core or any of the eight laws.

## The wave's own damage (honesty)

- **Label lossage.** The challenger agents' output `repo` field is not
  trustworthy: SYNAPSE's challenge self-labeled "HANISH" and Moneta's
  self-labeled "G:/Hanish". Reconciles by cited file paths (migrate.py /
  evolution.py → SYNAPSE; Forge/Crucible / USD-writer → Moneta). The
  survivor and verdict content was verified per-file and is unaffected.
- **API 429 on run 1:** 5 challenges + the plan failed on backend usage
  limits; the resume re-ran them and completed 13/13. Nothing assumed from
  run 1 survives except the design itself.
- **A classifier-availability warning** was attached to the SYNAPSE scout in
  the same run; SYNAPSE's claims were independently re-verified by the
  challenger against the code, so the surviving transfers are re-checked.

## Calibrated

- **What the wave proved about the team:** every repo in the fleet has a
  durability story; the fleet's shared instinct is adversarial verification.
  Hanish's guardian-shaped culture was independently reinvented three times
  (Moneta, Octavius, cognitive-subs) — evidence the pattern is load-bearing,
  not decorative.
- **Next single step (synthesizer's):** `tests/test_replay_determinism.py`
  — the seed-variance gate — then wire into `harness/metrics.md` G7 row and
  `.github/workflows/ci.yml`. It is the guard the hash-root commitment then
  ships behind.
