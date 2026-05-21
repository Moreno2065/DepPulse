"""Dependency cycle detection using networkx.simple_cycles."""

from __future__ import annotations

from collections import Counter
from typing import Optional

import networkx as nx

from deppulse.models import CycleInfo, CycleReport, CycleSeverity


def find_cycles(
    graph: nx.DiGraph,
    max_cycles_to_report: int = 100,
) -> CycleReport:
    """
    Detect all dependency cycles in the graph using Tarjan's algorithm
    (via networkx.simple_cycles which uses Johnson's algorithm internally).

    Parameters
    ----------
    graph : nx.DiGraph
        The dependency graph.
    max_cycles_to_report : int
        Cap the number of cycles to store (to avoid huge output).

    Returns
    -------
    CycleReport
        Cycle count, chains, top participants, and severity.
    """
    if graph.number_of_nodes() == 0:
        return CycleReport(
            cycle_count=0,
            cycles=[],
            top_cycle_participants=[],
            severity=CycleSeverity.NONE,
            total_files_in_cycles=0,
        )

    # simple_cycles returns each cycle as a list of nodes
    try:
        raw_cycles = list(nx.simple_cycles(graph))
    except Exception:
        raw_cycles = []

    # Deduplicate cycles (simple_cycles may return equivalent cycles)
    seen: set[tuple[str, ...]] = set()
    unique_cycles: list[list[str]] = []
    for cycle in raw_cycles:
        # Normalize: sort rotations so the same cycle isn't counted twice
        if len(cycle) < 2:
            continue
        # Create a canonical key by finding the lexicographically smallest rotation
        min_rotation = _canonical_cycle(cycle)
        if min_rotation not in seen:
            seen.add(min_rotation)
            unique_cycles.append(list(min_rotation))

    # Build CycleInfo objects
    cycles: list[CycleInfo] = []
    for cycle in unique_cycles[:max_cycles_to_report]:
        cycles.append(CycleInfo(nodes=cycle, length=len(cycle)))

    # Count how many cycles each node participates in
    participant_counts: Counter[str] = Counter()
    for cycle in unique_cycles:
        for node in cycle:
            participant_counts[node] += 1

    top_participants: list[tuple[str, int]] = participant_counts.most_common(10)

    # Severity assessment
    total_nodes = graph.number_of_nodes()
    files_in_cycles = len(participant_counts)
    cycle_count = len(unique_cycles)

    severity = _assess_severity(cycle_count, files_in_cycles, total_nodes)

    return CycleReport(
        cycle_count=cycle_count,
        cycles=cycles,
        top_cycle_participants=top_participants,
        severity=severity,
        total_files_in_cycles=files_in_cycles,
    )


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """
    Return a canonical tuple representation of a cycle so that
    all rotations of the same cycle map to the same key.
    """
    if not cycle:
        return ()
    doubled = cycle + cycle
    n = len(cycle)
    # Find the lexicographically smallest rotation
    min_idx = min(range(n), key=lambda i: doubled[i : i + n])
    return tuple(doubled[min_idx : min_idx + n])


def _assess_severity(
    cycle_count: int,
    files_in_cycles: int,
    total_files: int,
) -> CycleSeverity:
    """
    Determine cycle severity based on count and percentage of files involved.
    """
    if cycle_count == 0:
        return CycleSeverity.NONE

    percent_involved = files_in_cycles / max(total_files, 1) * 100

    if cycle_count > 20 or percent_involved > 30:
        return CycleSeverity.SEVERE
    elif cycle_count > 5 or percent_involved > 10:
        return CycleSeverity.MODERATE
    else:
        return CycleSeverity.MINOR


def get_cycle_chains_for_file(
    graph: nx.DiGraph,
    file_path: str,
    max_chains: int = 10,
) -> list[CycleInfo]:
    """
    Return all cycles that involve a specific file.
    Useful for per-file cycle reporting.
    """
    all_cycles = list(nx.simple_cycles(graph))
    file_cycles: list[CycleInfo] = []

    for cycle in all_cycles:
        if file_path in cycle and len(file_cycles) < max_chains:
            canonical = _canonical_cycle(cycle)
            file_cycles.append(CycleInfo(nodes=list(canonical), length=len(canonical)))

    return file_cycles
