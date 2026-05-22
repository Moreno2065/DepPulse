"""PRReporter: generate PR impact reports for code review."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.risk import compute_risk_score
from deppulse.models import (
    FileRiskEntry,
    ImpactReport,
    PRReportResult,
    RiskLevel,
)


class PRReporter:
    """
    Generate structured PR impact reports for code review.

    Produces both a structured PRReportResult dataclass and a
    markdown-formatted report suitable for posting as a PR comment.
    """

    def __init__(self, graph: nx.DiGraph, config: DepPulseConfig | None = None) -> None:
        self.graph = graph
        self.config = config or DepPulseConfig(project_root=Path.cwd())

    def generate(
        self,
        changed_files: list[str],
        base_ref: str = "main",
    ) -> PRReportResult:
        """
        Generate a full PR impact report for the given changed files.

        Parameters
        ----------
        changed_files : list[str]
            Project-relative POSIX paths of changed files.
        base_ref : str
            The base branch name (for reference only, not used internally).

        Returns
        -------
        PRReportResult
            Structured impact data for the PR.
        """
        if not changed_files:
            return PRReportResult(
                changed_files=[],
                affected_files=[],
                blast_radius=0,
                blast_radius_percent=0.0,
                risk_score=0.0,
                risk_level=RiskLevel.LOW,
                suggested_tests=[],
                top_affected=[],
                markdown_body="## DepPulse Impact Report\n\nNo changes detected.",
            )

        # Normalize paths
        normalized = [f.replace("\\", "/") for f in changed_files]

        # Filter to files in the graph
        graph_files = set(self.graph.nodes())
        in_graph = [f for f in normalized if f in graph_files]

        # Impact analysis (only for files in graph)
        if in_graph:
            analyzer = ImpactAnalyzer(self.graph)
            impact = analyzer.analyze_files(in_graph, max_chains=50)
        else:
            impact = self._empty_impact(normalized)

        # Risk score
        risk = compute_risk_score(
            self.graph,
            normalized,
            blast_radius_percent=impact.blast_radius_percent,
        )

        # Collect all affected files
        all_affected: set[str] = set(normalized)
        for pf in impact.per_file_impact:
            all_affected.update(pf.affected_files)

        # Suggested tests: files that directly import changed files
        suggested_tests = self._suggest_tests(list(all_affected))

        # Top affected files by in-degree
        top_affected = self._top_affected(list(all_affected), risk.level)

        return PRReportResult(
            changed_files=normalized,
            affected_files=sorted(all_affected),
            blast_radius=len(all_affected),
            blast_radius_percent=impact.blast_radius_percent,
            risk_score=risk.score,
            risk_level=risk.level,
            suggested_tests=suggested_tests,
            top_affected=top_affected,
            markdown_body="",  # filled by generate_markdown
        )

    def generate_markdown(
        self,
        result: PRReportResult,
        format: str = "github-comment",
    ) -> str:
        """
        Render a PRReportResult as a markdown string.

        Parameters
        ----------
        result : PRReportResult
            The impact data to render.
        format : str
            "github-comment" for GitHub-flavored markdown with details tags,
            "markdown" for plain markdown.
        """
        lines: list[str] = []

        if format == "github-comment":
            lines.extend([
                "## DepPulse Impact Report",
                "",
                f"**Blast radius:** {result.blast_radius} files affected "
                f"({result.blast_radius_percent:.1f}% of project)",
                f"**Risk level:** {result.risk_level.value} "
                f"(score {result.risk_score:.1f})",
                "",
            ])
        else:
            lines.extend([
                "# DepPulse PR Impact Report",
                "",
                f"**Changed files:** {len(result.changed_files)}",
                f"**Affected files:** {result.blast_radius}",
                f"**Blast radius:** {result.blast_radius_percent:.1f}%",
                f"**Risk level:** {result.risk_level.value} ({result.risk_score:.1f})",
                "",
            ])

        # Top affected table
        if result.top_affected:
            lines.extend(["", "### Top Affected Files", ""])
            lines.append("| File | In-degree | Risk |")
            lines.append("|------|-----------|------|")
            for entry in result.top_affected[:15]:
                lines.append(
                    f"| `{entry.path}` | {entry.in_degree} | "
                    f"{entry.risk_level.value} |"
                )
            lines.append("")

        # Suggested tests
        if result.suggested_tests:
            lines.extend(["", "### Suggested Tests", ""])
            for t in result.suggested_tests[:10]:
                lines.append(f"- `{t}`")
            lines.append("")

        # Changed files list (in github-comment mode, collapsed)
        if result.changed_files:
            if format == "github-comment":
                lines.extend([
                    "<details>",
                    "<summary>All changed files</summary>",
                    "",
                ])
                for f in result.changed_files:
                    lines.append(f"- `{f}`")
                lines.extend(["", "</details>", ""])
            else:
                lines.extend(["", "### Changed Files", ""])
                for f in result.changed_files:
                    lines.append(f"- `{f}`")
                lines.append("")

        lines.append("> Generated by DepPulse")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _suggest_tests(self, affected_files: list[str]) -> list[str]:
        """
        Suggest test files that should cover the affected source files.
        Uses direct edge inspection on the dependency graph.
        """
        tests: set[str] = set()
        affected_set = set(affected_files)

        for node in self.graph.nodes():
            if not self.config.is_test_file(node):
                continue
            # Does this test depend on any affected file?
            for successor in self.graph.successors(node):
                if successor in affected_set and not self.config.is_test_file(successor):
                    tests.add(node)
                    break

        return sorted(tests)

    def _top_affected(
        self,
        affected_files: list[str],
        overall_risk: RiskLevel,
    ) -> list[FileRiskEntry]:
        """
        Return top affected files sorted by in-degree, with risk level.

        Risk level per file:
        - HIGH if in-degree >= 5
        - MEDIUM if in-degree >= 2
        - LOW otherwise
        """
        entries: list[FileRiskEntry] = []

        for f in affected_files:
            in_deg = self.graph.in_degree(f) if f in self.graph else 0
            if in_deg >= 5:
                level = RiskLevel.HIGH
            elif in_deg >= 2:
                level = RiskLevel.MEDIUM
            else:
                level = RiskLevel.LOW
            entries.append(FileRiskEntry(path=f, in_degree=in_deg, risk_level=level))

        entries.sort(key=lambda e: e.in_degree, reverse=True)
        return entries[:10]

    @staticmethod
    def _empty_impact(mutated_files: list[str]) -> ImpactReport:
        """Return an empty impact report."""
        return ImpactReport(
            mutated_files=mutated_files,
            all_affected_files=mutated_files,
            per_file_impact=[],
            combined_affected_count=len(mutated_files),
            total_files_in_project=0,
            blast_radius_percent=0.0,
            risk_score=0.0,
            risk_level=RiskLevel.LOW,
            connected_component_size=0,
        )
