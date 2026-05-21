"""ImpactAnalyzer: reverse-dependency walk to compute blast radius and impact chains."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import networkx as nx

from deppulse.models import (
    ImpactChain,
    ImpactReport,
    PerFileImpact,
    RiskLevel,
)


class ImpactAnalyzer:
    """
    Analyzes the impact of changes to one or more files by computing
    reverse-dependency reachability in the dependency graph.

    Edge direction in the graph: source file -> dependency file.
    Therefore, to find files that DEPEND ON a changed file, we walk
    the graph in REVERSE (upstream traversal).
    """

    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph

    # ------------------------------------------------------------------
    # Single-file impact
    # ------------------------------------------------------------------

    def analyze_file(
        self,
        mutated_file: str,
        max_chains: int = 50,
    ) -> PerFileImpact:
        """
        Compute the impact of changing a single file.

        Returns a PerFileImpact describing affected upstream dependents
        and blast radius.
        """
        # Normalize path
        normalized = self._normalize_path(mutated_file)
        if normalized not in self.graph:
            return self._empty_impact(mutated_file)

        # Find all ancestors (files that depend on this file, directly or transitively)
        # Since edges point: source -> dependency, we want predecessors
        all_affected: set[str] = set()
        directly_affected: set[str] = set()

        # Use BFS on reversed graph
        queue = list(self.graph.predecessors(normalized))
        visited: set[str] = {normalized}
        depth_map: dict[str, int] = {normalized: 0}

        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            all_affected.add(node)
            depth_map[node] = depth_map.get(node, 0)

            # Determine direct vs indirect
            # A file is directly affected if it has an edge FROM the mutated file
            # or if it depends directly on the mutated file
            preds = self.graph.predecessors(node)
            if any(p == normalized or p in directly_affected for p in self.graph.predecessors(node)):
                directly_affected.add(node)

            for pred in self.graph.predecessors(node):
                if pred not in visited:
                    queue.append(pred)

        # Recalculate: a file is directly affected if it has a direct edge from mutated
        directly_affected = set()
        for affected in all_affected:
            # Check if there's a direct edge from affected -> mutated
            if self.graph.has_edge(affected, normalized):
                directly_affected.add(affected)

        all_affected_list = sorted(all_affected)
        directly_affected_list = sorted(directly_affected)

        # Compute impact chains (paths from affected files to mutated file)
        chains = self._compute_impact_chains(all_affected_list, normalized, max_chains)

        total = self.graph.number_of_nodes()
        blast_radius = (len(all_affected) / total * 100) if total > 0 else 0.0

        return PerFileImpact(
            mutated_file=normalized,
            affected_files=all_affected_list,
            directly_affected=directly_affected_list,
            impact_chains=chains,
            total_affected=len(all_affected),
            blast_radius_percent=blast_radius,
        )

    # ------------------------------------------------------------------
    # Multi-file (combined) impact
    # ------------------------------------------------------------------

    def analyze_files(
        self,
        mutated_files: list[str],
        max_chains: int = 50,
    ) -> ImpactReport:
        """
        Compute combined impact for multiple changed files.
        """
        per_file: list[PerFileImpact] = []
        all_affected: set[str] = set()

        for f in mutated_files:
            pfi = self.analyze_file(f, max_chains=max_chains)
            per_file.append(pfi)
            all_affected.update(pfi.affected_files)
            all_affected.update([f])  # include the mutated file itself

        total = self.graph.number_of_nodes()
        blast_radius = (len(all_affected) / total * 100) if total > 0 else 0.0

        # Compute a simple risk level based on blast radius
        if blast_radius >= 50:
            risk_level = RiskLevel.HIGH
            risk_score = min(100.0, blast_radius * 1.5)
        elif blast_radius >= 20:
            risk_level = RiskLevel.MEDIUM
            risk_score = blast_radius * 1.2
        else:
            risk_level = RiskLevel.LOW
            risk_score = blast_radius * 0.8

        return ImpactReport(
            mutated_files=[self._normalize_path(f) for f in mutated_files],
            all_affected_files=sorted(all_affected),
            per_file_impact=per_file,
            combined_affected_count=len(all_affected),
            total_files_in_project=total,
            blast_radius_percent=blast_radius,
            risk_score=risk_score,
            risk_level=risk_level,
        )

    # ------------------------------------------------------------------
    # Impact chains
    # ------------------------------------------------------------------

    def _compute_impact_chains(
        self,
        affected_files: list[str],
        mutated_file: str,
        max_chains: int,
    ) -> list[ImpactChain]:
        """
        Find short paths from each affected file back to the mutated source.
        Uses simple shortest-path traversal on the reversed graph.
        """
        chains: list[ImpactChain] = []
        R = self.graph.reverse(copy=False)

        for affected in affected_files:
            if len(chains) >= max_chains:
                break
            try:
                path = nx.shortest_path(R, affected, mutated_file)
                # Reverse: source -> ... -> mutated
                forward_path = list(reversed(path))
                chain = ImpactChain(chain=forward_path, length=len(forward_path) - 1)
                chains.append(chain)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue

        return chains

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _normalize_path(self, path: str) -> str:
        """Normalize a path to POSIX and strip leading/trailing separators."""
        normalized = path.replace("\\", "/")
        # Strip project root if present (assume graph nodes are already relative)
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    @staticmethod
    def _empty_impact(mutated_file: str) -> PerFileImpact:
        """Return an empty impact for a file not in the graph."""
        return PerFileImpact(
            mutated_file=mutated_file,
            affected_files=[],
            directly_affected=[],
            impact_chains=[],
            total_affected=0,
            blast_radius_percent=0.0,
        )
