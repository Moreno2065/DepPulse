"""Cycle participant B (depends on A)."""

from .cycle_a import cycle_a_func


def forward_to_a():
    return cycle_a_func()
