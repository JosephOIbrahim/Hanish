# roster.md — the team

20 roles. `director` orchestrates; 19 workers. Spawnable by name from
`.claude/agents/`. The director activates 2–5 per work item — the roster is the
ceiling, not the concurrency.

| role | flight | owns (one file) | guards | spawnable |
|---|---|---|---|---|
| director | — | the loop | the workflow | always |
| frontier-architect | scaffold | the package split + lattice | G5/G7 | flight PAST |
| ledger-doctor | PAST | hanish/past/ledger.py | G2 | flight PAST |
| schema-keeper | PAST | hanish/past/version.py | G4 | flight PAST |
| crash-scribe | PAST | capture + locking | G3 | flight PAST |
| replay-smith | PAST | startup perf, checkpoints | scaling | flight PAST |
| host-shield | PRESENT | process() never-raise + value_type | G1 | flight PRESENT |
| index-smith | PRESENT | resolution hot path | G6 | flight PRESENT |
| health-watch | PRESENT | status() + telemetry | honesty of rates | flight PRESENT |
| clock-court | PRESENT | time semantics | clock law | flight PRESENT |
| claim-mint | FUTURE | claims.py | claims blind | flight FUTURE |
| scoring-scan | FUTURE | scoring.py | scoreable honesty | flight FUTURE |
| adjudicator | FUTURE | adjudication | first-valid-terminal | flight FUTURE |
| namespace-planner | frontier | host namespaces | G8 | flight HOST-2 |
| harness-self | frontier | adapters/harness.py | G8 | flight HOST-2 |
| guardian | cross | attacks every claim | the adversary | every wave |
| verifier | cross | gates from metrics.md | metrics true | every wave |
| critic | cross | the law | the constitution | before every KEEP |
| docs-writer | cross | ADRs/README/digest (spec: harness/digest.md) | teach-down | flight SHIP |
| release-ship | cross | CI/PyPI/.gitignore | G9 | flight SHIP |

**Single-file rule:** a worker touches only the file it owns. Anything else is
a guardian attack, not a fix.
