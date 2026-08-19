"""G3 -- effect-once under an at-least-once transport, even across processes.

Two hosts capturing the same envelope from two processes must yield exactly
one durable record. The in-memory dedup set cannot see across processes, so
the decision happens under the append lock against the file.

The probe is run as a subprocess (not spawned as a pickled function) so the
test is bulletproof across Windows and Linux.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hanish import Substrate
from hanish.adapters.ci import CIAdapter

PROBE = Path(__file__).resolve().parent / "_concurrency_probe.py"


def test_cross_process_effect_once(tmp_path):
    procs = [
        subprocess.Popen(
            [sys.executable, str(PROBE), str(tmp_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]
    results = [p.communicate(timeout=120) for p in procs]
    for p, (out, err) in zip(procs, results, strict=True):
        assert p.returncode == 0, f"probe failed: {err}"
        assert out.strip() == "accepted", f"unexpected probe output: {out!r}"

    sub = Substrate(tmp_path, observables=CIAdapter().observable_specs())
    matches = [
        r for r in sub.evidence_l.raw()
        if r.get("_kind") == "observation"
        and r.get("source_ref") == "github-actions"
        and r.get("event_id") == "run-7:attempt-1:required_checks"
    ]
    assert len(matches) == 1                 # once. not twice. not zero.
