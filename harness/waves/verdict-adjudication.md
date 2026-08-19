# Wave record — v7 verdict adjudication (WAVE-ADJUDICATE-2026-08-19)

> An external evaluator issued a v7 verdict on HANISH. This wave did not
> defend the repo against it and did not accept it — it adjudicated it.
> The verdict is not the truth; the file is. Append-only by commit, like
> the ledgers.

## State

```
state:     COMPLETE — verdict adjudicated, build order synthesized
date:      2026-08-19
covering:  the external v7 verdict (5 attacks, 7-step build order, stage table)
workflow:  scout → adversarial-challenge (refute-or-die) → synthesize
agents:    9 — 4 scouts, 4 challengers, 1 synthesizer
runs:      1 — no API lossage this time (unlike memory-wave run 1)
cost:      965k tokens, 196 tool calls; verdicts cite file:line
```

## The design

A wave, not a single reader — a single reader cannot be trusted to tell
Hanish what the verdict got right. The same three phases as the memory
scan, pointed at a single target this time:

1. **Scout (read-only).** Four independent scouts, one per verdict cluster
   (CI/records, economics, identity/exposure, plan composition). Each told:
   *trust the file, not the verdict* — mark every claim
   `confirmed / qualified / refuted` with file:line evidence, and never
   assume the verdict read the code correctly.
2. **Challenge (adversarial).** One challenger per scout's report. Charter:
   *refute.* Nothing survives unless you are forced. Each challenger
   re-opens the files itself, tests the scout's fix shape against the eight
   laws and the gates, and names what the verdict *and* the scout missed.
3. **Synthesize.** One synthesizer over the survivors: sequence into
   NOW/NEXT, honor the memory wave's standing scaffold, discard anything
   that grows the repo without making a law or gate enforceable.

## What was adjudicated

The v7 verdict: **8.5/10** on the V0.0–V0.1 foundation, **~3/10** on the
full v7 research program. Five attacks (CI is not Host 0; capture path
violates the economic architecture; resolution rescans all observations;
`source_seq` lives in adapter process memory; the first self-forecast is
not BLIND), a receipts-publication proposal, a `world_ref` REPLAYABLE
challenge, and a 7-step build order.

## The verdicts — what survived the challenge (9 of 12)

| survivor | cluster | what it is | phase |
|---|---|---|---|
| Cold-capture watermark | economic 1 | cold first-sight dedup scans the WHOLE ledger under the shared append lock; watermark the scan to records-since-open → O(1) reopen, kills a cross-process lock convoy every append waits on | NEXT (R5) |
| P6 tracked-thread confirmation | economic 2+4 | "resolution rescans observations" is a known, tracked thread (P6, index-smith). The untracked delta is the L7 hazard: a naive global cursor would starve late-authored forecasts and launder their first-valid terminal — fold into P6, never a new item | NEXT (R6) |
| Committed per-run receipts | ci-host0·3 | the digest's HIT/brier cite a gitignored, wipeable dir — the ONLY calibration datapoint in the program. A fresh clone cannot re-derive it. Publish immutable `experiments/receipts/<run>/` + one-command reproduce | NEXT (R4) |
| G9 never exercised by CI | ci-host0·4 | G9's check is `python -m build` but the workflow has no build step — CI cannot fail on a packaging break, cannot gate the ship | NEXT (folds into R8) |
| Sequence authority across restart | identity·1 | `self._seq` is adapter memory; a restarted adapter can certify a false-complete stream. The repo's own regression (test_v00.py:149-163, "seq 1 never captured") encodes exactly the guarantee this defeats | NEXT (folds into R8) |
| Exposure is structural, not cosmetic | identity·2 | **LIVE defect, not a policy question:** the run-1 forecast is categorically not BLIND (author = the identity who directed the flight) yet outcomes.jsonl:1 already scored it calibration-eligible HIT. The loop measured its own hand | NOW (R3) |
| WorldManifest or honest downgrade | identity·3 | world_ref declares REPLAYABLE but stores a 16-hex digest; workflow_sha and lockfile_sha are unrecoverable. Persist a manifest, or declare IDENTIFIABLE and withdraw the forensics claim | NEXT (R7) |
| CI-host is one merged item | plan·4 | the 4-python matrix collapses into ONE observation (event_id carries no leg; run_id identical across legs; dedup on source+event) — a passing leg can score a failing commit HIT and the 3 failures are erased. Sequence authority is the predicate of wiring, not a follow-up | NEXT (R8) |
| Doc-honesty correction | plan·1 residue | HARNESS.md:3 "CI proved the loop on a real external stream" and digest.md §6 "the CI result was captured" are FALSE today — the evidence record carries a fabricated run_id ("flight-past"). The substrate's own docs launder absence into an assertion | NOW (R2) |

## Refutations — why the other three died

The refutations are the calibration. Strongest near-survivors that did NOT
survive, with the reason:

- **"CI is not really Host 0"** (ci-host·1). The wiring observation is exact
  (ci.yml:16-31, no CIAdapter call) but the defect framing collapses on its
  own repo: HARNESS.md's claim is substantively accurate — the single
  evidence record IS a genuine operator-session capture of a real CI result
  on the real commit — and the proposed fix is dead on arrival (program.md:62
  forbids committing ledger artifacts; an ephemeral runner cannot write the
  substrate root). Refuted as a build item; its residue is the two-line doc
  honesty edit, now R2.
- **"The CI-first calibration promise isn't happening"** (ci-host·2). A
  strawman: no file promises buckets fill from unattended CI. Calibration
  buckets are on the deliberately-absent list; "you wake up to a digest" is
  about an operator-driven loop that is exactly what exists. Refuted outright.
- **"Capture latency ≠ history size, as a permanent invariant"** (economic·3).
  An unenforced norm is decoration — the gate budget has no performance gate
  to pin it to, and the warm path already satisfies it. Its correct reading
  is exactly the R5 watermark. Refuted as a norm; retained as rationale.
- **"Resolution rescans observations — new cursor item"** (economic·2). Code
  exactly as claimed, but it is a verbatim restatement of the harness's OWN
  tracked P6 — no unowned work. The surviving delta is the L7 starvation
  hazard above. Refuted as new work; folded into P6.
- **"The triad isn't implemented"** (plan·3). Absence is real and re-verified
  — but building it now is the documented anti-goal ("the measurement
  instrument must be trustworthy first"). Not an actionable build item.
- **"Deliberately-absent list vs the verdict's 8"** (plan·3). A bookkeeping
  slip: all ten README members are present, the two "missing" ones are
  already covered by the standing scaffold. Nothing to build.

## The wave's own damage (honesty)

- **Two scout overreaches, killed by the challenges:** the arch-plan scout
  claimed "Host-Omega is not a defined repo concept" — README does define
  host Ω (V0.2); what is undefined is any artifact. The identity-exposure
  scout proposed deriving `final_source_seq` from the ledger high-water
  mark — the challenger killed it as fail-open: it strips the seal of its
  only independent authority (the whole point of test_v00.py:150-163) and
  makes every sealed stream complete-by-construction.
- **One fabrication found, not asserted:** the "fabricated run_id
  'flight-past'" finding came from the challengers re-opening the records —
  the verdict never flagged it. It is the strongest item in the wave.
- **No API lossage:** single run, 9/9 agents, no 429 (unlike memory-wave run
  1 which lost 5 challenges + the plan and required a resume).

## The scaffold plan (synthesizer's verdict)

The memory wave's floor stands unchanged and is not re-ranked: **NOW** =
seed-variance determinism gate + hash-root commitment; **NEXT** =
writer-must-match-reader seam (host #2) + pending queue + forced-promotion
cap; **LATER** = copy-and-verified migration. Every item in THIS wave
sequences behind the determinism gate.

This wave adds the trust layer, then the reconstruction:

1. **R2 (NOW) — doc honesty.** Correct the fabricated-CI claim and stale
   counts before any wiring ships on a doc that already asserts a completed
   false proof — the exact laundering Hanish exists to prevent.
2. **R3 (NOW) — exposure integrity.** Force EXPOSED for self/team
   forecasts; an append-only amendment record rescinds the polluted run-1
   sample from scoreable_rate. The one core-touching change, kept minimal,
   under a guardian regression.
3. **R4 — commit the receipts** (NEXT) with a one-command digest reproduce.
4. **R5 + R6 — performance trust, both small:** watermark cold capture;
   fold the L7 hazard into P6.
5. **R7 + R8 — the adapter items:** WorldManifest-or-downgrade; the merged
   CI-host item (sequence authority + composite signal + G9 in the matrix).

Honest about thin: the CI end-to-end guarantees (mixed 4-leg pass/fail →
exactly one commit-level signal) are proven locally at the adapter layer
but need one live GitHub Actions run. Nothing in this wave changes the
eight laws, drags a dependency, or touches the deliberately-absent list;
the triad stays deliberately NOT next until the instrument is trustworthy.

## The verdict's own verdict, independently confirmed

The evaluator said the guardian result was the encouraging result. The
wave agrees, and sharpens it: the loop closed, then produced a second
correction out of thin air — the repo's own docs asserted a proof that
never happened, and the instrument's first calibration sample was the loop
measuring its own hand. The self-correction system worked twice.

## Calibrated

- **What the wave proved about the verdict:** 8.5/10 was earned and the
  attack list is real, but the verdict's framing was wrong in half the
  cases — where it was wrong, the code's own laws (fail-closed,
  append-only, never-commit-ledger-artifacts) did the refuting. The
  strongest findings (fabricated provenance, live exposure pollution) were
  BOTH missed by the verdict.
- **Next single step (synthesizer's):** land R2 — the one-commit doc-honesty
  correction in `HARNESS.md` + `harness/digest.md` (fabricated CI capture)
  and the stale counts in `README.md`. Then R3, the exposure amendment,
  ships the receipts behind an honest base.
