"""Emit a canonical semantic replay snapshot for hash-seed comparison."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from hanish import Substrate
from hanish.adapters.ci import CIAdapter
from hanish.past.ledger import to_json


def _record(value: object) -> dict:
    return json.loads(to_json(value))


def _canonical_state(root: Path) -> str:
    substrate = Substrate(root, observables=CIAdapter().observable_specs())
    state = {
        "forecasts": [
            _record(substrate.forecasts[key]) for key in sorted(substrate.forecasts)
        ],
        "observations": [_record(value) for value in substrate._observations],
        "seals": [
            _record(substrate._seals[key])
            for key in sorted(substrate._seals)
        ],
        "outcomes": [
            _record(substrate.outcomes[key]) for key in sorted(substrate.outcomes)
        ],
    }
    return json.dumps(state, separators=(",", ":"), sort_keys=True)


def main(root: str) -> None:
    canonical = _canonical_state(Path(root))
    payload = {
        "canonical": canonical,
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        # Python salts string hashes between processes. The parent requires
        # this value to differ, proving the semantic equality was not a
        # same-process or accidentally unsalted comparison.
        "salted_hash": hash("hanish-replay-determinism"),
    }
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1])
