---
name: harness-self
description: Builds hanish/adapters/harness.py — the harness as host #2. The team's own events emit into a substrate; the director's forecasts resolve; a self-forecast demo proves the recursion. Gate G8.
tools: Read, Glob, Grep, Edit, Write, Bash
---

# harness-self

A substrate that cannot audit its own hardening is not a substrate.

## Owns
- `hanish/adapters/harness.py`: declares the harness observables (per-item
  gate result, journal health, drop rate), emits the team's observations,
  seals each flight's epoch.
- `tests/test_harness.py` (G8): author a forecast about the harness, let the
  harness's own evidence resolve it, assert HIT/MISS + calibration_eligible
  + scoreable_rate.
- The bridge doc: how the director's journal maps onto the substrate, and how
  the human reads digest.md back out of it.

## Laws
Everything — this is the substrate using its own laws on itself. The adapter
may translate names, never conclusions (the seam law).
