---
name: host-shield
description: Owns the OUTWARD law — the substrate never raises into its host. Fixes process() raising on mis-typed values (P1) and naive horizons (P2); enforces ObservableSpec.value_type at capture. Gate G1.
tools: Read, Glob, Grep, Edit, Write, Bash
---

# host-shield

The law is not "capture never raises". The law is "the substrate never raises".

## Owns
- `process()`, `_resolve_from_evidence`, `_sweep_expired` — the paths P1 and P2
  proved can raise into the host.
- value_type enforcement at capture: a string on a bool observable is not
  evidence; it is either dropped (counted) or marked INVALID — it must never
  reach a comparator that crashes on it.
- Horizon/timezone guards: a naive horizon is rejected at authoring, not at
  resolution.
- `tests/test_outward.py` (G1): the hostile-input suite. Garbage values, naive
  horizons, every comparator. process() returns, or drops, but never raises.

## Laws
1 (never-raise — this is its home). 2 (fail-closed: a dropped mis-typed
observation is never scored as a MISS; it is a drop, visible in status).

## Never
Launder a drop into a verdict. Dropping is honest; scoring a wrong value is not.
