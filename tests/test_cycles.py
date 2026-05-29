"""Tests for the cycle detection module."""

from pathlib import Path

import networkx as nx

from deppulse.core.cycles import _assess_severity, _canonical_cycle, find_cycles
from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.models import CycleSeverity

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "python_project"


class TestCanonicalCycle:
    def test_canonical_same_rotation(self):
        c1 = _canonical_cycle(["a", "b", "c"])
        c2 = _canonical_cycle(["b", "c", "a"])
        c3 = _canonical_cycle(["c", "a", "b"])
        assert c1 == c2 == c3

    def test_canonical_different_cycle(self):
        c1 = _canonical_cycle(["a", "b", "c"])
        c2 = _canonical_cycle(["x", "y", "z"])
        assert c1 != c2


class TestCycleDetection:
    def test_empty_graph_no_cycles(self):
        graph = nx.DiGraph()
        report = find_cycles(graph)
        assert report.cycle_count == 0
        assert report.severity == CycleSeverity.NONE
        assert report.cycles == []

    def test_linear_graph_no_cycles(self):
        graph = nx.DiGraph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("c", "d")])
        report = find_cycles(graph)
        assert report.cycle_count == 0

    def test_simple_cycle_detected(self):
        graph = nx.DiGraph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("c", "a")])
        report = find_cycles(graph)
        assert report.cycle_count >= 1
        assert report.severity != CycleSeverity.NONE

    def test_multiple_cycles(self):
        graph = nx.DiGraph()
        graph.add_edges_from([
            ("a", "b"), ("b", "c"), ("c", "a"),  # cycle 1
            ("x", "y"), ("y", "z"), ("z", "x"),  # cycle 2
        ])
        report = find_cycles(graph)
        assert report.cycle_count >= 2

    def test_severity_none_when_no_cycles(self):
        graph = nx.DiGraph()
        graph.add_edges_from([("a", "b")])
        report = find_cycles(graph)
        assert report.severity == CycleSeverity.NONE

    def test_fixture_project_cycles(self):
        """The cycle_a.py, cycle_b.py fixture should produce at least one cycle."""
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        graph = _build_graph(result)

        # Check if cycle files exist in the graph
        has_cycle_files = "cycle_a.py" in graph.nodes() and "cycle_b.py" in graph.nodes()
        if has_cycle_files:
            report = find_cycles(graph)
            # Should detect at least one cycle
            assert report.cycle_count >= 1

    def test_top_participants(self):
        graph = nx.DiGraph()
        graph.add_edges_from([("a", "b"), ("b", "c"), ("c", "a"), ("a", "d")])
        report = find_cycles(graph)
        assert len(report.top_cycle_participants) > 0
        # "a" participates in the cycle
        participant_paths = {p for p, _ in report.top_cycle_participants}
        assert "a" in participant_paths


class TestCycleAssessment:
    def test_severity_none(self):
        assert _assess_severity(0, 0, 10) == CycleSeverity.NONE

    def test_severity_minor(self):
        assert _assess_severity(2, 2, 100) == CycleSeverity.MINOR

    def test_severity_moderate(self):
        assert _assess_severity(8, 5, 100) == CycleSeverity.MODERATE

    def test_severity_severe(self):
        assert _assess_severity(25, 40, 100) == CycleSeverity.SEVERE


def _build_graph(result):
    import networkx as nx

    from deppulse.models import Language, NodeMetadata

    graph = nx.DiGraph()
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
        graph.add_node(scan_result.file_path, **vars(meta))

    for scan_result in result.scan_results:
        for resolved in scan_result.internal_dependencies:
            if resolved.normalized_path is None:
                continue
            if resolved.normalized_path not in graph:
                ghost = NodeMetadata(
                    path=resolved.normalized_path,
                    language=Language.UNKNOWN,
                    suffix=Path(resolved.normalized_path).suffix,
                    size_bytes=0,
                    symbol_count=0,
                    unresolved_count=0,
                    external_count=0,
                )
                graph.add_node(resolved.normalized_path, **vars(ghost))
            graph.add_edge(scan_result.file_path, resolved.normalized_path)

    return graph
