"""Shared CLI helper functions."""

from __future__ import annotations

import time
from pathlib import Path

import networkx as nx

from deppulse.config import DepPulseConfig
from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.models import GraphBuildResult


def run_scan(
    project_path: Path,
    config: DepPulseConfig,
    use_cache: bool,
    files_to_scan: set[str] | None = None,
    debug: bool = False,
) -> tuple[GraphBuildResult, nx.DiGraph, float]:
    """Run the orchestrator scan and return results with timing."""
    orchestrator = DependencyOrchestrator(config=config, use_cache=use_cache, debug=debug)
    start = time.monotonic()
    result = orchestrator.scan(project_path, files_to_scan=files_to_scan)
    elapsed = time.monotonic() - start

    graph = build_graph_from_results(result)
    return result, graph, elapsed


def build_graph_from_results(result: GraphBuildResult) -> nx.DiGraph:
    """Rebuild the networkx graph from a GraphBuildResult."""
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
            resolved_by = DependencyOrchestrator._edge_resolved_by(
                scan_result.absolute_path, resolved.normalized_path
            )
            graph.add_edge(
                scan_result.file_path,
                resolved.normalized_path,
                raw_text=resolved.raw.raw_text,
                kind=resolved.raw.kind.value,
                line_number=resolved.raw.line_number,
                resolved_by=resolved_by,
            )
    return graph
