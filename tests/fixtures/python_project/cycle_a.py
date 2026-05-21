"""Cycle participant A (depends on B)."""

from . import app  # direct cycle
from .cycle_b import forward_to_a


def cycle_a_func():
    return forward_to_a()
