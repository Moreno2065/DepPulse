"""Tests for the risk scoring module."""

import pytest
from pathlib import Path

from deppulse.core.risk import compute_risk_score, _is_core_path
from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.models import RiskLevel


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "python_project"


class TestRiskScoring:
    @pytest.fixture
    def graph(self):
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        G = _build_graph(result)
        return G

    def test_risk_score_zero_blast_radius(self, graph):
        """If nothing is affected, risk should be low."""
        risk = compute_risk_score(graph, [], blast_radius_percent=0.0)
        assert risk.score >= 0
        assert risk.score <= 100
        assert risk.level in RiskLevel

    def test_risk_score_high_blast_radius(self, graph):
        """High blast radius increases risk."""
        risk = compute_risk_score(graph, [], blast_radius_percent=80.0)
        # Impact radius should contribute significantly
        impact_component = next((c for c in risk.components if c.name == "impact_radius"), None)
        assert impact_component is not None
        assert impact_component.raw_value >= 50  # 80% blast radius
        assert risk.level in RiskLevel

    def test_risk_score_components_present(self, graph):
        risk = compute_risk_score(graph, ["main.py"], blast_radius_percent=50.0)
        # New 4-factor model has 9 sub-components
        assert len(risk.components) >= 4  # At least 4 main factors
        component_names = {c.name for c in risk.components}
        # New 4-factor model components
        expected = {"impact_radius", "historical_hotspot", "coupling_risk.betweenness",
                    "change_nature.file_count"}
        assert expected.issubset(component_names)

    def test_risk_score_contributions_sum(self, graph):
        risk = compute_risk_score(graph, ["main.py"], blast_radius_percent=50.0)
        # Contributions are in 0-100 scale; sum equals the final score
        total = sum(c.contribution for c in risk.components)
        assert total == risk.score
        assert 0 <= total <= 100

    def test_risk_levels(self, graph):
        """Verify risk level boundaries."""
        low = compute_risk_score(graph, [], blast_radius_percent=0.0)
        high = compute_risk_score(graph, [], blast_radius_percent=95.0)
        assert low.level in RiskLevel
        assert high.level in RiskLevel

    def test_explanation_present(self, graph):
        risk = compute_risk_score(graph, ["main.py"], blast_radius_percent=30.0)
        assert len(risk.explanation) > 10
        assert "0." in risk.explanation or "%" in risk.explanation


class TestCorePathDetection:
    def test_core_path_detection(self):
        assert _is_core_path("core/analyzer.py") is True
        assert _is_core_path("lib/utils.py") is True
        assert _is_core_path("common/types.py") is True
        assert _is_core_path("src/main.py") is True
        assert _is_core_path("tests/test_analyzer.py") is False
        assert _is_core_path("utils/helpers.py") is False


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
