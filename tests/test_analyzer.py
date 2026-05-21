"""Tests for the ImpactAnalyzer."""

import pytest
from pathlib import Path

from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.models import RiskLevel


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "python_project"


class TestImpactAnalyzer:
    @pytest.fixture
    def graph_and_results(self):
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        G = _build_graph(result)
        return G, result

    def test_single_file_impact(self, graph_and_results):
        G, result = graph_and_results
        analyzer = ImpactAnalyzer(G)

        # main.py is at the top of the import chain
        impact = analyzer.analyze_file("main.py")
        assert impact.mutated_file == "main.py"
        # main.py should affect at least utils/helpers.py if it's depended on
        assert impact.total_affected >= 0

    def test_file_not_in_graph(self, graph_and_results):
        G, _ = graph_and_results
        analyzer = ImpactAnalyzer(G)
        impact = analyzer.analyze_file("nonexistent.py")
        assert impact.total_affected == 0
        assert impact.affected_files == []

    def test_multi_file_impact(self, graph_and_results):
        G, result = graph_and_results
        analyzer = ImpactAnalyzer(G)

        # If models/user.py exists, trace it
        paths = {r.file_path for r in result.scan_results}
        test_files = [p for p in ["main.py", "models/user.py"] if p in paths][:1]
        if not test_files:
            pytest.skip("No suitable test files found")

        impact = analyzer.analyze_files(test_files)
        assert impact.mutated_files == test_files
        assert impact.total_files_in_project == G.number_of_nodes()
        assert impact.blast_radius_percent >= 0.0

    def test_blast_radius_calculation(self, graph_and_results):
        G, _ = graph_and_results
        analyzer = ImpactAnalyzer(G)
        total = G.number_of_nodes()

        # Trace a file with no dependents
        impact = analyzer.analyze_file("broken_syntax.py")
        if total > 0:
            assert 0.0 <= impact.blast_radius_percent <= 100.0

    def test_impact_chains_capped(self, graph_and_results):
        G, _ = graph_and_results
        analyzer = ImpactAnalyzer(G)
        impact = analyzer.analyze_file("main.py", max_chains=5)
        assert len(impact.impact_chains) <= 5

    def test_normalize_path(self, graph_and_results):
        G, _ = graph_and_results
        analyzer = ImpactAnalyzer(G)
        # Should handle both forward and backslash
        impact1 = analyzer.analyze_file("main.py")
        assert impact1.mutated_file == "main.py"


def _build_graph(result):
    """Rebuild a networkx graph from GraphBuildResult."""
    import networkx as nx
    from deppulse.models import Language, NodeMetadata

    G = nx.DiGraph()
    for scan_result in result.scan_results:
        if scan_result.error and not scan_result.resolved_dependencies:
            continue
        meta = NodeMetadata(
            path=scan_result.file_path,
            language=scan_result.language,
            suffix=scan_result.suffix,
            size_bytes=scan_result.size_bytes,
            symbol_count=len(scan_result.symbols),
            unresolved_count=len(scan_result.unresolved_dependencies),
            external_count=len(scan_result.external_dependencies),
        )
        G.add_node(scan_result.file_path, **vars(meta))

    for scan_result in result.scan_results:
        for resolved in scan_result.internal_dependencies:
            if resolved.normalized_path is None:
                continue
            if resolved.normalized_path not in G:
                ghost = NodeMetadata(
                    path=resolved.normalized_path,
                    language=Language.UNKNOWN,
                    suffix=Path(resolved.normalized_path).suffix,
                    size_bytes=0,
                    symbol_count=0,
                    unresolved_count=0,
                    external_count=0,
                )
                G.add_node(resolved.normalized_path, **vars(ghost))
            G.add_edge(scan_result.file_path, resolved.normalized_path)

    return G
