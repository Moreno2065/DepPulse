"""Risk scoring module with a transparent, explainable 4-factor model.

The v1.0 risk model computes a score (0-100) from four independent factors:

| Factor           | Weight | Sub-factors                                  |
|-----------------|--------|----------------------------------------------|
| Impact Radius   | 30%    | blast_pct (alpha=0.6) + avg_in_degree_norm  |
| Change Nature   | 25%    | file_count, line_count, API change, core_path |
| Historical      | 25%    | bug_fix_rate, churn_frequency, co_change_risk |
| Coupling Risk   | 20%    | betweenness, cycle_participation, fan_ratio   |

Score 0-30: LOW   — low-impact change, safe to merge
Score 30-70: MEDIUM — moderate impact, review recommended
Score 70-100: HIGH  — high-impact change, careful review required

All weights are configurable via `RiskWeights` and can be loaded from
`deppulse.json` under `risk.weights.*`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Optional

import networkx as nx

from deppulse.core.diff_parser import ChangedSymbol, DiffParser
from deppulse.core.hotspot_analyzer import FileHotspotData, HotspotAnalyzer, HotspotReport
from deppulse.models import RiskComponent, RiskLevel, RiskReport

if TYPE_CHECKING:
    from pathlib import Path


# Core-like directory names that are typically more critical.
_CORE_DIR_NAMES: frozenset[str] = frozenset({
    "core", "base", "foundation", "common", "shared",
    "lib", "runtime", "engine", "kernel", "infra",
    "src",
})


# ---------------------------------------------------------------------------
# RiskWeights
# ---------------------------------------------------------------------------


@dataclass
class RiskWeights:
    """
    Configurable weights for the 4-factor risk model.
    All values sum to 1.0 by default.
    """

    # Top-level factor weights
    impact_radius_weight: float = 0.30
    change_nature_weight: float = 0.25
    historical_hotspot_weight: float = 0.25
    coupling_risk_weight: float = 0.20

    # Impact Radius sub-weights
    blast_pct_alpha: float = 0.60

    # Change Nature sub-weights
    file_count_weight: float = 0.15
    line_count_weight: float = 0.25
    api_change_weight: float = 0.35
    core_path_weight: float = 0.25

    # Historical Hotspot sub-weights
    bug_fix_rate_weight: float = 0.40
    churn_frequency_weight: float = 0.30
    co_change_risk_weight: float = 0.30

    # Coupling Risk sub-weights
    betweenness_weight: float = 0.40
    cycle_participation_weight: float = 0.30
    fan_ratio_weight: float = 0.30

    def to_dict(self) -> dict:
        """Serialize weights to a dict (useful for config files)."""
        return {
            "impact_radius_weight": self.impact_radius_weight,
            "change_nature_weight": self.change_nature_weight,
            "historical_hotspot_weight": self.historical_hotspot_weight,
            "coupling_risk_weight": self.coupling_risk_weight,
            "blast_pct_alpha": self.blast_pct_alpha,
            "file_count_weight": self.file_count_weight,
            "line_count_weight": self.line_count_weight,
            "api_change_weight": self.api_change_weight,
            "core_path_weight": self.core_path_weight,
            "bug_fix_rate_weight": self.bug_fix_rate_weight,
            "churn_frequency_weight": self.churn_frequency_weight,
            "co_change_risk_weight": self.co_change_risk_weight,
            "betweenness_weight": self.betweenness_weight,
            "cycle_participation_weight": self.cycle_participation_weight,
            "fan_ratio_weight": self.fan_ratio_weight,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RiskWeights":
        """Deserialize weights from a dict."""
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in cls.__dataclass_fields__.values()}})


# ---------------------------------------------------------------------------
# Default weights
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = RiskWeights()


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------


def compute_risk_score(
    graph: nx.DiGraph,
    involved_files: list[str],
    blast_radius_percent: float,
    cycle_count: int = 0,
    cycle_files: int = 0,
    changed_symbols: Optional[list[ChangedSymbol]] = None,
    hotspot_data: Optional[dict[str, FileHotspotData]] = None,
    weights: Optional[RiskWeights] = None,
    changed_line_count: Optional[int] = None,
) -> RiskReport:
    """
    Compute a transparent, explainable risk score (0-100) for one or more files.

    This function uses a 4-factor model:

    1. **Impact Radius (30%)** — how widely the change propagates through
       the dependency graph. A file that many others depend on is high-risk.

    2. **Change Nature (25%)** — what kind of change it is. API signature
       changes are higher-risk than body-only changes.

    3. **Historical Hotspot (25%)** — whether this file has a history of
       frequent bug fixes or churn. Files that change often are riskier.

    4. **Coupling Risk (20%)** — how entangled the file is in the graph:
       centrality, cycle participation, and fan ratio.

    Parameters
    ----------
    graph : nx.DiGraph
        The dependency graph (nodes = file paths, edges = dependencies).
    involved_files : list[str]
        Files that were changed.
    blast_radius_percent : float
        Percentage of project files in the affected set (0-100).
    cycle_count : int
        Number of dependency cycles in the project.
    cycle_files : int
        Number of files participating in cycles.
    changed_symbols : list[ChangedSymbol], optional
        Line-level symbol changes from DiffParser. Used for Change Nature scoring.
    hotspot_data : dict[str, FileHotspotData], optional
        Historical hotspot data per file. Computed by HotspotAnalyzer.
    weights : RiskWeights, optional
        Custom weights for the model. Uses defaults if not provided.
    changed_line_count : int, optional
        Total number of lines changed across all involved files.
        If not provided, estimated from blast_radius (conservative).

    Returns
    -------
    RiskReport
        Detailed risk assessment with 4 top-level components.
    """
    if not weights:
        weights = DEFAULT_WEIGHTS

    components: list[RiskComponent] = []

    # -- Factor 1: Impact Radius (30%) --
    impact_components, impact_score = _factor_impact_radius(
        graph, involved_files, blast_radius_percent, weights
    )
    components.extend(impact_components)

    # -- Factor 2: Change Nature (25%) --
    change_components, change_score = _factor_change_nature(
        involved_files, changed_symbols, changed_line_count, weights
    )
    components.extend(change_components)

    # -- Factor 3: Historical Hotspot (25%) --
    hotspot_components, hotspot_score_val = _factor_historical_hotspot(
        involved_files, hotspot_data, weights
    )
    components.extend(hotspot_components)

    # -- Factor 4: Coupling Risk (20%) --
    coupling_components, coupling_score_val = _factor_coupling_risk(
        graph, involved_files, cycle_count, cycle_files, weights
    )
    components.extend(coupling_components)

    # Final score: sum of weighted components (each is already 0-100)
    score = impact_score + change_score + hotspot_score_val + coupling_score_val

    # Determine risk level
    if score >= 70:
        level = RiskLevel.HIGH
    elif score >= 30:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    # Build explanation
    top_components = sorted(components, key=lambda c: c.contribution, reverse=True)[:4]
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


# ---------------------------------------------------------------------------
# Factor 1: Impact Radius
# ---------------------------------------------------------------------------


def _factor_impact_radius(
    graph: nx.DiGraph,
    involved_files: list[str],
    blast_radius_percent: float,
    weights: RiskWeights,
) -> tuple[list[RiskComponent], float]:
    """Impact Radius: blast_pct + normalized avg in-degree."""
    components: list[RiskComponent] = []

    # Blast percentage component (weighted by alpha within impact radius)
    norm_blast = min(blast_radius_percent / 100.0, 1.0)
    blast_contribution = norm_blast * weights.impact_radius_weight * weights.blast_pct_alpha

    # Avg in-degree normalized component
    in_degrees = [int(graph.in_degree(f)) for f in involved_files if f in graph]
    max_in_degree = max(in_degrees) if in_degrees else 0
    avg_in_degree_norm = max_in_degree / max(graph.number_of_nodes(), 1)
    in_degree_contribution = (
        avg_in_degree_norm
        * weights.impact_radius_weight
        * (1 - weights.blast_pct_alpha)
    )

    # Top-level Impact Radius component
    impact_radius_contrib = blast_contribution + in_degree_contribution

    components.append(RiskComponent(
        name="impact_radius",
        weight=weights.impact_radius_weight,
        raw_value=blast_radius_percent,
        normalized_value=norm_blast,
        contribution=impact_radius_contrib,
        explanation=(
            f"Impact radius: {blast_radius_percent:.1f}% blast radius, "
            f"max in-degree={max_in_degree}. "
            f"Contributes {impact_radius_contrib:.1f}."
        ),
    ))

    return components, impact_radius_contrib


# ---------------------------------------------------------------------------
# Factor 2: Change Nature
# ---------------------------------------------------------------------------


def _factor_change_nature(
    involved_files: list[str],
    changed_symbols: Optional[list[ChangedSymbol]],
    changed_line_count: Optional[int],
    weights: RiskWeights,
) -> tuple[list[RiskComponent], float]:
    """
    Change Nature: scores based on the type and extent of the change.
    """
    components: list[RiskComponent] = []

    # File count component (how many files changed)
    file_count = len(involved_files)
    norm_file_count = min(file_count / 20.0, 1.0)  # cap at 20 files
    file_count_contrib = (
        norm_file_count
        * weights.change_nature_weight
        * weights.file_count_weight
    )

    components.append(RiskComponent(
        name="change_nature.file_count",
        weight=weights.change_nature_weight * weights.file_count_weight,
        raw_value=float(file_count),
        normalized_value=norm_file_count,
        contribution=file_count_contrib,
        explanation=f"{file_count} file(s) changed. Contributes {file_count_contrib:.1f}.",
    ))

    # Line count component
    line_count = changed_line_count or 0
    if line_count == 0 and changed_symbols:
        # Estimate from symbol line ranges
        for sym in changed_symbols:
            line_count += max(1, sym.line_range[1] - sym.line_range[0] + 1)

    norm_line_count = min(line_count / 500.0, 1.0)  # cap at 500 lines
    line_count_contrib = (
        norm_line_count
        * weights.change_nature_weight
        * weights.line_count_weight
    )

    components.append(RiskComponent(
        name="change_nature.line_count",
        weight=weights.change_nature_weight * weights.line_count_weight,
        raw_value=float(line_count),
        normalized_value=norm_line_count,
        contribution=line_count_contrib,
        explanation=(
            f"{line_count} line(s) changed. Contributes {line_count_contrib:.1f}."
        ),
    ))

    # API change component (signature changes are highest risk)
    api_change_score = 0.0
    if changed_symbols:
        from deppulse.core.diff_parser import ChangeType
        signature_changes = sum(
            1 for s in changed_symbols if s.change_type == ChangeType.SIGNATURE
        )
        body_changes = sum(
            1 for s in changed_symbols if s.change_type == ChangeType.BODY
        )
        if signature_changes > 0:
            api_change_score = min(signature_changes / max(len(changed_symbols), 1) + 0.3, 1.0)
        elif body_changes > 0:
            api_change_score = 0.3

    api_change_contrib = (
        api_change_score
        * weights.change_nature_weight
        * weights.api_change_weight
    )

    components.append(RiskComponent(
        name="change_nature.api_change",
        weight=weights.change_nature_weight * weights.api_change_weight,
        raw_value=api_change_score,
        normalized_value=api_change_score,
        contribution=api_change_contrib,
        explanation=(
            f"API change score={api_change_score:.2f} "
            f"(signature changes are highest risk). "
            f"Contributes {api_change_contrib:.1f}."
        ),
    ))

    # Core path component
    core_count = sum(1 for f in involved_files if _is_core_path(f))
    core_score = core_count / max(len(involved_files), 1)
    core_path_contrib = (
        core_score
        * weights.change_nature_weight
        * weights.core_path_weight
    )

    components.append(RiskComponent(
        name="change_nature.core_path",
        weight=weights.change_nature_weight * weights.core_path_weight,
        raw_value=float(core_count),
        normalized_value=core_score,
        contribution=core_path_contrib,
        explanation=(
            f"{core_count}/{len(involved_files)} file(s) in core directories. "
            f"Contributes {core_path_contrib:.1f}."
        ),
    ))

    change_nature_total = file_count_contrib + line_count_contrib + api_change_contrib + core_path_contrib
    return components, change_nature_total


# ---------------------------------------------------------------------------
# Factor 3: Historical Hotspot
# ---------------------------------------------------------------------------


def _factor_historical_hotspot(
    involved_files: list[str],
    hotspot_data: Optional[dict[str, FileHotspotData]],
    weights: RiskWeights,
) -> tuple[list[RiskComponent], float]:
    """Historical Hotspot: based on git history analysis."""
    components: list[RiskComponent] = []

    if not hotspot_data:
        # No hotspot data — use a neutral contribution of 0
        components.append(RiskComponent(
            name="historical_hotspot",
            weight=weights.historical_hotspot_weight,
            raw_value=0.0,
            normalized_value=0.0,
            contribution=0.0,
            explanation="No hotspot data available. Contributing 0.",
        ))
        return components, 0.0

    hotspot_scores: list[float] = []
    for f in involved_files:
        data = hotspot_data.get(f)
        if data is None:
            continue

        # bug_fix_rate component
        bfr_score = min(data.bug_fix_rate * 2, 1.0)  # double weight for high bug rates

        # churn_frequency component (already capped at 3.0 by HotspotAnalyzer)
        churn_score = min(data.churn_frequency / 3.0, 1.0)

        # co_change_risk component
        co_score = min(data.co_change_count / 10.0, 1.0)  # cap at 10 co-changes

        combined = (
            bfr_score * weights.bug_fix_rate_weight
            + churn_score * weights.churn_frequency_weight
            + co_score * weights.co_change_risk_weight
        )
        hotspot_scores.append(combined)

    avg_hotspot = sum(hotspot_scores) / max(len(hotspot_scores), 1)
    hotspot_contrib = avg_hotspot * weights.historical_hotspot_weight

    components.append(RiskComponent(
        name="historical_hotspot",
        weight=weights.historical_hotspot_weight,
        raw_value=avg_hotspot,
        normalized_value=avg_hotspot,
        contribution=hotspot_contrib,
        explanation=(
            f"Historical hotspot score={avg_hotspot:.3f} "
            f"(from {len(hotspot_scores)} hotspot data point(s)). "
            f"Contributes {hotspot_contrib:.1f}."
        ),
    ))

    return components, hotspot_contrib


# ---------------------------------------------------------------------------
# Factor 4: Coupling Risk
# ---------------------------------------------------------------------------


def _factor_coupling_risk(
    graph: nx.DiGraph,
    involved_files: list[str],
    cycle_count: int,
    cycle_files: int,
    weights: RiskWeights,
) -> tuple[list[RiskComponent], float]:
    """Coupling Risk: centrality, cycle participation, and fan ratio."""
    components: list[RiskComponent] = []

    # Betweenness centrality
    centrality_scores: list[float] = []
    if graph.number_of_nodes() > 1:
        try:
            betweenness = nx.betweenness_centrality(graph, normalized=True)
            centrality_scores = [betweenness.get(f, 0.0) for f in involved_files]
        except Exception:
            centrality_scores = []

    avg_centrality = sum(centrality_scores) / max(len(centrality_scores), 1)
    betweenness_contrib = (
        avg_centrality
        * weights.coupling_risk_weight
        * weights.betweenness_weight
    )

    components.append(RiskComponent(
        name="coupling_risk.betweenness",
        weight=weights.coupling_risk_weight * weights.betweenness_weight,
        raw_value=avg_centrality,
        normalized_value=avg_centrality,
        contribution=betweenness_contrib,
        explanation=(
            f"Avg betweenness centrality={avg_centrality:.4f}. "
            f"Files central in the dependency graph propagate changes further. "
            f"Contributes {betweenness_contrib:.2f}."
        ),
    ))

    # Cycle participation
    cycle_score = 0.0
    if cycle_count > 0 or cycle_files > 0:
        cycle_score = min(
            (cycle_count / 10.0) * 0.5 + (cycle_files / max(graph.number_of_nodes(), 1)) * 0.5,
            1.0,
        )
    cycle_contrib = (
        cycle_score
        * weights.coupling_risk_weight
        * weights.cycle_participation_weight
    )

    components.append(RiskComponent(
        name="coupling_risk.cycle_participation",
        weight=weights.coupling_risk_weight * weights.cycle_participation_weight,
        raw_value=float(cycle_count),
        normalized_value=cycle_score,
        contribution=cycle_contrib,
        explanation=(
            f"{cycle_count} cycles, {cycle_files} files in cycles. "
            f"Contributes {cycle_contrib:.1f}."
        ),
    ))

    # Fan ratio (out_degree / (in_degree + out_degree)) — high fan-out means more coupling
    fan_scores: list[float] = []
    for f in involved_files:
        if f in graph:
            in_d = int(graph.in_degree(f))
            out_d = int(graph.out_degree(f))
            total = in_d + out_d
            if total > 0:
                fan_scores.append(out_d / total)

    avg_fan_ratio = sum(fan_scores) / max(len(fan_scores), 1)
    fan_contrib = (
        avg_fan_ratio
        * weights.coupling_risk_weight
        * weights.fan_ratio_weight
    )

    components.append(RiskComponent(
        name="coupling_risk.fan_ratio",
        weight=weights.coupling_risk_weight * weights.fan_ratio_weight,
        raw_value=avg_fan_ratio,
        normalized_value=avg_fan_ratio,
        contribution=fan_contrib,
        explanation=(
            f"Avg fan-out ratio={avg_fan_ratio:.2f}. "
            f"High fan-out means this file depends on many others. "
            f"Contributes {fan_contrib:.1f}."
        ),
    ))

    coupling_total = betweenness_contrib + cycle_contrib + fan_contrib
    return components, coupling_total


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _is_core_path(path: str) -> bool:
    """Return True if the path contains a core-like directory name."""
    parts = PurePosixPath(path).parts
    return any(p.lower() in _CORE_DIR_NAMES for p in parts)


def compute_risk_from_git_diff(
    graph: nx.DiGraph,
    involved_files: list[str],
    blast_radius_percent: float,
    project_root: "Path",
    cycle_count: int = 0,
    cycle_files: int = 0,
    weights: Optional[RiskWeights] = None,
) -> RiskReport:
    """
    Convenience function: compute risk score using git diff for symbol-level analysis.

    This function runs DiffParser and HotspotAnalyzer internally to provide
    the full 4-factor risk assessment from a git diff.
    """
    # Parse diff for changed symbols
    from deppulse.core.diff_parser import DiffParser
    diff_parser = DiffParser(project_root=project_root)
    diff_output = _run_git_diff(project_root)
    file_diffs = diff_parser.parse(diff_output)

    changed_symbols: list[ChangedSymbol] = []
    changed_line_count = 0
    for fd in file_diffs:
        changed_symbols.extend(fd.changed_symbols)
        for start, end in fd.changed_lines:
            changed_line_count += end - start + 1

    # Compute hotspot data
    from pathlib import Path as _Path
    hotspot_analyzer = HotspotAnalyzer(project_root=_Path(project_root))
    hotspot_report = hotspot_analyzer.analyze()
    hotspot_data = hotspot_report.file_data

    return compute_risk_score(
        graph=graph,
        involved_files=involved_files,
        blast_radius_percent=blast_radius_percent,
        cycle_count=cycle_count,
        cycle_files=cycle_files,
        changed_symbols=changed_symbols if changed_symbols else None,
        hotspot_data=hotspot_data if hotspot_data else None,
        weights=weights,
        changed_line_count=changed_line_count,
    )


def _run_git_diff(project_root: "Path") -> str:
    """Run git diff --unified=0 and return output."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "diff", "--unified=0", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
