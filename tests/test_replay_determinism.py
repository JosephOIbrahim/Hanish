"""Replay must describe the same history under distinct hash seeds."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).with_name("_replay_probe.py")
FIXTURE = Path(__file__).with_name("fixtures") / "replay_v1"
PINNED_V1_DIGEST = "91448d2c15f6edbd4dd13e6828d2ca34250901caf316194b7ccdc0b60f8f894d"


def _probe(seed: int, fixture: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(ROOT), env.get("PYTHONPATH", "")) if value
    )
    result = subprocess.run(
        [sys.executable, str(PROBE), str(fixture)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return json.loads(result.stdout)


def test_v1_replay_is_hash_seed_independent(tmp_path):
    fixture = tmp_path / "replay_v1"
    shutil.copytree(FIXTURE, fixture)
    first = _probe(1, fixture)
    second = _probe(8675309, fixture)

    assert first["salted_hash"] != second["salted_hash"]
    assert first["canonical"] == second["canonical"]
    assert first["digest"] == second["digest"] == PINNED_V1_DIGEST
