"""Scoring. Pure.

Given a comparison, a probability, and what happened, the numbers. No
ledgers, no state, no host. If a pair is incomparable, scoring raises --
coercing an incomparable value into a verdict is how malformed evidence
gets laundered into calibration data. The PRESENT layer decides whether an
incomparable observation may score.
"""

from __future__ import annotations

from typing import Any

from ..future.claims import Comparator

_COMPARATORS = {
    Comparator.EQ:  lambda a, b: a == b,
    Comparator.NE:  lambda a, b: a != b,
    Comparator.GT:  lambda a, b: a > b,
    Comparator.GTE: lambda a, b: a >= b,
    Comparator.LT:  lambda a, b: a < b,
    Comparator.LTE: lambda a, b: a <= b,
}


def compare(observed: Any, comparator: Comparator, threshold: Any) -> bool:
    """Raises TypeError/ValueError for incomparable pairs; never coerces."""
    return _COMPARATORS[Comparator(comparator)](observed, threshold)


def brier(probability: float, y: float) -> float:
    """Squared error against an observed binary outcome (1.0/0.0)."""
    return (probability - y) ** 2
