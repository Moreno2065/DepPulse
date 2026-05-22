"""Risk scoring module with transparent, explainable component-based scoring."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional

import networkx as nx

from deppulse.models import RiskComponent, RiskLevel, RiskReport


# Core-like directory names that are typically more critical.
_CORE_DIR_NAMES: frozenset[str] = frozenset({
    "core", "base", "foundation", "common", "shared",
    "lib", "runtime", "engine", "kernel", "infra",
    "src",
})


def compute_risk_score(
    graph: nx.DiGraph,
    involved_files: list[str],
    blast_radius_percent: float,
    cycle_count: int = 0,
    cycle_files: int = 0,
) -> RiskReport:
    """
    Compute a transparent, explainable risk score (0-100) for one or more files.

    The score is the weighted sum of five normalized components:

    | Component            | Weight | Description                                           |
    |----------------------|--------|-------------------------------------------------------|
    | blast_radius_percent | 0.50   | % of project files affected by this change           |
    | dependent_ratio      | 0.20   | in-degree / (in-degree + out-degree) ratio            |
    | centrality           | 0.15   | betweenness centrality normalized by graph size      |
    | core_path            | 0.10   | whether file is in a core-like directory             |
    | cycle_penalty        | 0.05   | whether file participates in dependency cycles        |

    Score 0-30: LOW   — low-impact change, safe to merge
    Score 30-70: MEDIUM — moderate impact, review recommended
    Score 70-100: HIGH  — high-impact change, careful review required
    """
    components: list[RiskComponent] = []

    # Blast radius component (weight: 50 out of 100)
    raw_blast = blast_radius_percent
    norm_blast = min(raw_blast / 100.0, 1.0)
    blast_contrib = 50.0 * norm_blast
    components.append(RiskComponent(
        name="blast_radius_percent",
        weight=0.50,
        raw_value=raw_blast,
        normalized_value=norm_blast,
        contribution=blast_contrib,
        explanation=(
            f"{raw_blast:.1f}% of project files are affected. "
            f"This component contributes {blast_contrib:.1f} to the score."
        ),
    ))

    # Dependent ratio component (weight: 20 out of 100)
    in_degrees = [graph.in_degree(f) for f in involved_files]
    out_degrees = [graph.out_degree(f) for f in involved_files]
    avg_in = sum(in_degrees) / max(len(in_degrees), 1)
    avg_out = sum(out_degrees) / max(len(out_degrees), 1)
    total_deps = avg_in + avg_out
    dependent_ratio = (avg_in / total_deps) if total_deps > 0 else 0.0
    dependent_contrib = 20.0 * dependent_ratio
    components.append(RiskComponent(
        name="dependent_ratio",
        weight=0.20,
        raw_value=avg_in,
        normalized_value=dependent_ratio,
        contribution=dependent_contrib,
        explanation=(
            f"Files have avg in-degree={avg_in:.1f} (dependents) and "
            f"out-degree={avg_out:.1f} (dependencies). "
            f"Ratio={dependent_ratio:.2f}. "
            f"Contributes {dependent_contrib:.1f}."
        ),
    ))

    # Centrality component (weight: 15 out of 100)
    centrality_scores: list[float] = []
    if graph.number_of_nodes() > 1:
        try:
            betweenness = nx.betweenness_centrality(graph, normalized=True)
            centrality_scores = [betweenness.get(f, 0.0) for f in involved_files]
        except Exception:
            centrality_scores = []
    avg_centrality = sum(centrality_scores) / max(len(centrality_scores), 1) if centrality_scores else 0.0
    centrality_contrib = 15.0 * avg_centrality
    components.append(RiskComponent(
        name="centrality_score",
        weight=0.15,
        raw_value=avg_centrality,
        normalized_value=avg_centrality,
        contribution=centrality_contrib,
        explanation=(
            f"Avg betweenness centrality={avg_centrality:.4f}. "
            f"Files central in the dependency graph propagate changes further. "
            f"Contributes {centrality_contrib:.2f}."
        ),
    ))

    # Core path component (weight: 10 out of 100)
    core_count = sum(
        1 for f in involved_files
        if _is_core_path(f)
    )
    core_score = core_count / max(len(involved_files), 1)
    core_contrib = 10.0 * core_score
    components.append(RiskComponent(
        name="core_path_score",
        weight=0.10,
        raw_value=core_count,
        normalized_value=core_score,
        contribution=core_contrib,
        explanation=(
            f"{core_count}/{len(involved_files)} files are in core-like directories "
            f"(core/, base/, lib/, common/, etc.). "
            f"Contributes {core_contrib:.1f}."
        ),
    ))

    # Cycle penalty component (weight: 5 out of 100)
    cycle_score = 0.0
    if cycle_count > 0 or cycle_files > 0:
        cycle_score = min(1.0, (cycle_count / 10.0) * 0.5 + (cycle_files / max(graph.number_of_nodes(), 1)) * 0.5)
    cycle_contrib = 5.0 * cycle_score
    components.append(RiskComponent(
        name="cycle_penalty",
        weight=0.05,
        raw_value=float(cycle_count),
        normalized_value=cycle_score,
        contribution=cycle_contrib,
        explanation=(
            f"{cycle_count} cycles detected, {cycle_files} files in cycles. "
            f"Contributes {cycle_contrib:.1f}."
        ),
    ))

    # Final score: contributions are already in 0-100 scale, sum directly
    score = sum(c.contribution for c in components)

    # --- Determine risk level ---
    if score >= 70:
        level = RiskLevel.HIGH
    elif score >= 30:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    # --- Build explanation ---
    top_components = sorted(components, key=lambda c: c.contribution, reverse=True)[:3]
    top_explanations = " ".join(c.explanation for c in top_components)
    explanation = (
        f"Risk score {score:.1f}/100 ({level.value}). "
        f"Top contributing factors: {top_explanations}"
    )

    return RiskReport(
        score=score,
        level=level,
        components=components,
        involved_files=involved_files,
        explanation=explanation,
    )


def _is_core_path(path: str) -> bool:
    """Return True if the path contains a core-like directory name."""
    parts = PurePosixPath(path).parts
    return any(p.lower() in _CORE_DIR_NAMES for p in parts)
