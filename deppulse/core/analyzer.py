"""ImpactAnalyzer: reverse-dependency walk to compute blast radius and impact chains."""

from __future__ import annotations

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
        cc_size_cache: Optional[dict[str, int]] = None,
    ) -> PerFileImpact:
        """
        Compute the impact of changing a single file.

        Returns a PerFileImpact describing affected upstream dependents
        and blast radius.
        """
        normalized = self._normalize_path(mutated_file)
        if normalized not in self.graph:
            return self._empty_impact(mutated_file)

        all_affected: set[str] = set()
        directly_affected: set[str] = set()

        cc_size = (cc_size_cache or {}).get(normalized) or self._connected_component_size(normalized)

        queue = list(self.graph.predecessors(normalized))
        visited: set[str] = {normalized}

        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            all_affected.add(node)

            if self.graph.has_edge(node, normalized):
                directly_affected.add(node)

            for pred in self.graph.predecessors(node):
                if pred not in visited:
                    queue.append(pred)

        all_affected_list = sorted(all_affected)
        directly_affected_list = sorted(directly_affected)

        chains = self._compute_impact_chains(all_affected_list, normalized, max_chains)

        blast_radius = (len(all_affected) / cc_size * 100) if cc_size > 0 else 0.0

        return PerFileImpact(
            mutated_file=normalized,
            affected_files=all_affected_list,
            directly_affected=directly_affected_list,
            impact_chains=chains,
            total_affected=len(all_affected),
            blast_radius_percent=blast_radius,
            connected_component_size=cc_size,
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
        # Precompute connected component sizes once for all mutated files
        cc_size_cache: dict[str, int] = {}
        if self.graph.number_of_nodes() > 0:
            for cc in nx.weakly_connected_components(self.graph):
                cc_size = len(cc)
                for node in cc:
                    cc_size_cache[node] = cc_size

        per_file: list[PerFileImpact] = []
        all_affected: set[str] = set()
        cc_size = self.graph.number_of_nodes()

        for f in mutated_files:
            pfi = self.analyze_file(f, max_chains=max_chains, cc_size_cache=cc_size_cache)
            per_file.append(pfi)
            all_affected.update(pfi.affected_files)
            all_affected.update([f])
            cc_size = max(cc_size, pfi.connected_component_size)

        blast_radius = (len(all_affected) / cc_size * 100) if cc_size > 0 else 0.0

        # Risk level and score are computed by compute_risk_score() in the caller,
        # not here — avoid duplicating the logic in the model layer.
        return ImpactReport(
            mutated_files=[self._normalize_path(f) for f in mutated_files],
            all_affected_files=sorted(all_affected),
            per_file_impact=per_file,
            combined_affected_count=len(all_affected),
            total_files_in_project=self.graph.number_of_nodes(),
            blast_radius_percent=blast_radius,
            risk_score=0.0,
            risk_level=RiskLevel.LOW,
            connected_component_size=cc_size,
        )

    # ------------------------------------------------------------------
    # Connected component
    # ------------------------------------------------------------------

    def _connected_component_size(self, node: str) -> int:
        """Return the size of the weakly-connected component containing `node`."""
        if node not in self.graph or self.graph.number_of_nodes() == 0:
            return 0
        for cc in nx.weakly_connected_components(self.graph):
            if node in cc:
                return len(cc)
        return 0

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
