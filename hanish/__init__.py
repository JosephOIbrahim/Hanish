"""Hanish — Temporal Cognitive Substrate, V0.0.

A type system for time plus a scoreboard. Domain-blind core; adapters import
core; core imports nothing from adapters.
"""

from .core.substrate import Substrate

__all__ = ["Substrate"]
