"""G3 probe: one process half of the cross-process once-only test.

Run by the parent test as a subprocess (never imported as a module). Every
probe constructs the identical envelope and captures it against the same
root, then prints whether the substrate accepted it.
"""

from __future__ import annotations

import sys

from hanish import Substrate
from hanish.adapters.ci import CIAdapter


def main(root: str) -> None:
    ci = CIAdapter()
    event = ci.checks_result("abc123", run_id="7", attempt=1, passed=True)
    sub = Substrate(root, observables=ci.observable_specs())
    ok = sub.capture(event)
    print("accepted" if ok else "rejected", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
