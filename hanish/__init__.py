"""Hanish — Temporal Cognitive Substrate, V0.2.

A type system for time plus a scoreboard. The core is a lattice, oldest to
newest:

    time ← past ← future ← present ← adapters

Nothing in a lower layer imports from a higher one. Adapters may translate
names and package values; they may never infer conclusions.
"""

from .present.substrate import Substrate

__all__ = ["Substrate"]
