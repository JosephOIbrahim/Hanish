# orchestrator.md — the director's runbook

> The operating procedure for the loop. The director reads this at boot and
> follows it exactly. A change to this file is a change to the workflow.

## Boot

1. Read `program.md`. Read `HARNESS.md`. Read `harness/digest.md` (the last
   interpretation of state; check its state field). Read the tail of the journal.
2. Boot the substrate root at `harness/root/` (until host #2 lands, the journal
   is plain JSONL under `harness/journal/` — same append-only rules).
3. Reconstruct state: which work items are open, which gates are unmet.
4. Announce: `[director] booted. N open items. Next: <item>.`

## The loop (one item at a time)

1. **Pick.** Next item in phase order with an unmet gate. No item is picked
   twice if it is UNRESOLVABLE with a reason on the journal.
2. **Forecast.** Author a forecast before work begins — subject = the item,
   claim = the gate it will land, probability = your read of the world, exposure
   = the honest value (a forecast the team can see is EXPOSED, not calibration).
3. **Wave.** Spawn the owning worker(s) for the item (roster says who). Give each:
   program.md, orchestrator.md, the metrics, the journal tail, the file it owns.
   Max 5 alive. One item at a time — no parallel items.
4. **Attack.** Spawn the guardian against the worker's diff while it works.
5. **Gate.** Spawn the verifier to run the item's gate(s) exactly as written in
   `metrics.md`. The verifier alone says KEEP / DISCARD.
6. **Resolve.**
   - KEEP → commit the change (message: item id, gate, verdict), append the
     observation, mark the item closed, resolve the forecast.
   - DISCARD → the owning worker reworks once; a second DISCARD closes the item
     as UNRESOLVABLE with the reason on the journal.
7. **Append.** Everything — observations, forecasts, verdicts — lands in the
   journal before the next item starts.

## Rules

- One writer to the journal: the director. Workers post to the director, never
  to the journal directly.
- The verifier is the only issuer of KEEP. The guardian is the only issuer of
  "attack". The critic must clear every diff before KEEP is valid.
- Never weaken a gate to ship. That is laundering. The adjudicator catches it.
- A flight ends when all its gates are green or its items are UNRESOLVABLE
  with reasons. Report to the human before the next flight starts.
- At every flight boundary, write a `harness/digest.md` CHECKPOINT: what
  landed since the last one, what was discarded and why, the substrate's own
  numbers on the team, the next thread.
- Stop when the journal has no open items. Rewrite `harness/digest.md` as
  FINAL per the digest spec (`harness/digest.md`): the wake test must pass —
  a cold agent reads it and can state, verify, and continue.

## Failure modes

- A worker dies mid-item → the journal state is intact (append-only); re-spawn
  the worker with the same item. No data lost by design.
- A gate fails after KEEP: the commit is un-reverted; a new item is opened with
  the regression as its claim. Never rewrite history — append a correction.
- The guardian finds a law break: stop the item, open a P0 item, resolve only
  after the breach is closed with a test that bites.
