# metrics.md — the only budget that keeps a change

The verifier runs these, exactly. A gate passes only when its check command
exits green. Never weaken a gate to ship a change.

| gate | name | check | lands when |
|---|---|---|---|
| G1 | never-raise | `pytest -q tests/test_outward.py` | process() accepts garbage/naive horizons, never raises (P1/P2) |
| G2 | tail-toler | `pytest -q tests/test_tail.py` | torn tail: reopen succeeds, status admits lost tail, capture still works (P3) |
| G3 | once-only | `pytest -q tests/test_concurrency.py` | two processes, one event, one record (P4) |
| G4 | schema | `pytest -q tests/test_schema.py` | old ledgers open; records carry version (P5) |
| G5 | lattice | `pytest -q tests/test_domain_blindness.py` | past←future←present lattice + vocab grep (the refactor) |
| G6 | scoreable | `pytest -q tests/test_v00.py` | all 20 still green; rates still separate (G6) |
| G7 | replay | `pytest -q tests/test_replay_determinism.py` | distinct hash seeds reproduce the pinned semantic digest |
| G8 | Host Ω | `pytest -q tests/test_host_omega.py` | hostile host and CI adapter satisfy one conformance contract |
| G9 | ship | `python -m build && ruff check .` | package builds, lint is clean, and every promoted receipt verifies |

A gate closes a work item. A gate never closes a law.
