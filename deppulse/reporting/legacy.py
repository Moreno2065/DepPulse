"""Report generation: JSON and Markdown audit reports."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import networkx as nx

from deppulse.models import (
    AuditReport,
    CycleReport,
    GraphBuildResult,
    GraphStats,
    ImpactReport,
    RiskReport,
    TopFileEntry,
)

# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def assemble_audit_report(
    graph_result: GraphBuildResult,
    graph: nx.DiGraph,
    cycle_report: CycleReport | None = None,
    impact_report: ImpactReport | None = None,
    risk_report: RiskReport | None = None,
    scan_duration: float = 0.0,
) -> AuditReport:
    """
    Assemble a comprehensive AuditReport from all available data.
    """
    # Top depended-on files (files that others depend on the most = in-degree)
    in_degrees = [(n, graph.in_degree(n)) for n in graph.nodes()]
    top_depended = [
        TopFileEntry(path=n, count=d, language=graph.nodes[n].get("language", "unknown"))
        for n, d in sorted(in_degrees, key=lambda x: -x[1])[:10]
        if d > 0
    ]

    # Top files with most outgoing dependencies (out-degree)
    out_degrees = [(n, graph.out_degree(n)) for n in graph.nodes()]
    top_outgoing = [
        TopFileEntry(path=n, count=d, language=graph.nodes[n].get("language", "unknown"))
        for n, d in sorted(out_degrees, key=lambda x: -x[1])[:10]
        if d > 0
    ]

    # Collect unresolved and external dependencies across all scan results
    unresolved: list = []
    external: list = []
    for result in graph_result.scan_results:
        for dep in result.unresolved_dependencies:
            unresolved.append(dep)
        for dep in result.external_dependencies:
            external.append(dep)

    # High-risk files: files with in-degree in top 20% and/or in cycles
    high_risk: list[str] = []
    if in_degrees:
        threshold = sorted(d for _, d in in_degrees)[-max(1, len(in_degrees) // 5)]
        high_risk = [n for n, d in in_degrees if d >= threshold]

    if cycle_report:
        cycle_file_set = {node for cycle in cycle_report.cycles for node in cycle.nodes}
        high_risk = sorted(set(high_risk + list(cycle_file_set)))[:20]

    return AuditReport(
        project_path=graph_result.project_root,
        generated_at=datetime.now(),
        graph_stats=graph_result.stats,
        cycle_report=cycle_report,
        top_depended_on=top_depended,
        top_outgoing=top_outgoing,
        unresolved_summary=unresolved[:50],
        external_summary=external[:50],
        high_risk_files=high_risk,
        scan_duration_seconds=scan_duration,
    )


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


def audit_report_to_json(report: AuditReport, pretty: bool = True) -> str:
    """
    Serialize an AuditReport to a JSON string.
    """
    data = _audit_report_to_dict(report)
    indent = 2 if pretty else None
    return json.dumps(data, indent=indent, ensure_ascii=False, default=str)


def write_json_report(report: AuditReport, output_path: Path) -> None:
    """Write an audit report as JSON to a file."""
    content = audit_report_to_json(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def _audit_report_to_dict(report: AuditReport) -> dict:
    """Convert AuditReport to a JSON-serializable dict."""
    def _dep_to_dict(d):
        return {
            "raw_text": d.raw.raw_text,
            "kind": d.raw.kind.value,
            "line_number": d.raw.line_number,
            "resolution_note": d.resolution_note,
            "confidence": d.confidence.value if d.confidence else None,
            "confidence_source": d.confidence_source.value if d.confidence_source else None,
        }

    return {
        "version": "1.0",
        "project_path": report.project_path,
        "generated_at": report.generated_at.isoformat(),
        "scan_duration_seconds": round(report.scan_duration_seconds, 3),
        "graph_stats": _graph_stats_to_dict(report.graph_stats),
        "cycle_report": _cycle_report_to_dict(report.cycle_report) if report.cycle_report else None,
        "top_depended_on": [
            {"path": e.path, "count": e.count, "language": e.language.value}
            for e in report.top_depended_on
        ],
        "top_outgoing": [
            {"path": e.path, "count": e.count, "language": e.language.value}
            for e in report.top_outgoing
        ],
        "unresolved_summary": [_dep_to_dict(d) for d in report.unresolved_summary],
        "external_summary": [_dep_to_dict(d) for d in report.external_summary],
        "high_risk_files": report.high_risk_files,
    }


def _graph_stats_to_dict(stats: GraphStats) -> dict:
    return {
        "total_files": stats.total_files,
        "total_edges": stats.total_edges,
        "python_files": stats.python_files,
        "cpp_files": stats.cpp_files,
        "unknown_files": stats.unknown_files,
        "internal_edges": stats.internal_edges,
        "external_edges": stats.external_edges,
        "unresolved_edges": stats.unresolved_edges,
        "total_symbols": stats.total_symbols,
        "language_breakdown": stats.language_breakdown,
        "files_with_cycles": stats.files_with_cycles,
    }


def _cycle_report_to_dict(report: CycleReport | None) -> dict | None:
    if report is None:
        return None
    return {
        "cycle_count": report.cycle_count,
        "cycles": [
            {"nodes": c.nodes, "length": c.length}
            for c in report.cycles[:50]
        ],
        "top_cycle_participants": [
            {"path": p, "count": c}
            for p, c in report.top_cycle_participants
        ],
        "severity": report.severity.value,
        "total_files_in_cycles": report.total_files_in_cycles,
    }


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def audit_report_to_markdown(report: AuditReport) -> str:
    """
    Serialize an AuditReport to a readable Markdown document,
    suitable for PR review or documentation.
    """
    lines: list[str] = []
    lines.append("# DepPulse Audit Report")
    lines.append("")
    lines.append(f"**Project:** `{report.project_path}`")
    lines.append(f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Duration:** {report.scan_duration_seconds:.2f}s")
    lines.append("")

    # --- Graph statistics
    stats = report.graph_stats
    lines.append("## Graph Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total files scanned | {stats.total_files} |")
    lines.append(f"| Total dependency edges | {stats.total_edges} |")
    lines.append(f"| Python files | {stats.python_files} |")
    lines.append(f"| C/C++ files | {stats.cpp_files} |")
    lines.append(f"| Internal edges | {stats.internal_edges} |")
    lines.append(f"| External edges | {stats.external_edges} |")
    lines.append(f"| Total symbols extracted | {stats.total_symbols} |")
    lines.append("")

    # --- Confidence summary (from edge metadata)
    lsp_count = 0
    ast_count = 0
    heuristic_count = 0
    unknown_count = 0
    for result in getattr(report, "scan_results", []):
        for dep in result.resolved_dependencies:
            conf_val = dep.confidence.value if dep.confidence else None
            if conf_val == "lsp":
                lsp_count += 1
            elif conf_val == "ast":
                ast_count += 1
            elif conf_val == "heuristic":
                heuristic_count += 1
            else:
                unknown_count += 1

    if any([lsp_count, ast_count, heuristic_count, unknown_count]):
        total_deps = lsp_count + ast_count + heuristic_count + unknown_count
        lines.append("## Dependency Confidence Summary")
        lines.append("")
        lines.append(
            "Analysis confidence is graded per edge. "
            "LSP-confirmed edges are verified by the language's type system. "
            "Unknown edges are statically unresolvable (e.g. dynamic imports)."
        )
        lines.append("")
        lines.append("| Confidence | Count | Pct |")
        lines.append("|------------|-------|-----|")
        if total_deps > 0:
            if lsp_count:
                lines.append(f"| LSP (verified) | {lsp_count} | {lsp_count/total_deps*100:.1f}% |")
            if ast_count:
                lines.append(f"| AST/CST (parser) | {ast_count} | {ast_count/total_deps*100:.1f}% |")
            if heuristic_count:
                lines.append(f"| Heuristic (name match) | {heuristic_count} | {heuristic_count/total_deps*100:.1f}% |")
            if unknown_count:
                lines.append(f"| Unknown (unresolvable) | {unknown_count} | {unknown_count/total_deps*100:.1f}% |")
        lines.append("")

    # --- Top depended-on files
    if report.top_depended_on:
        lines.append("## Top Depended-On Files")
        lines.append("")
        lines.append("These files are depended on by the most other files (highest in-degree):")
        lines.append("")
        lines.append("| Rank | File | Dependents | Language |")
        lines.append("|------|------|-----------|----------|")
        for i, entry in enumerate(report.top_depended_on, 1):
            lines.append(f"| {i} | `{entry.path}` | {entry.count} | {entry.language.value} |")
        lines.append("")

    # --- Top outgoing dependencies
    if report.top_outgoing:
        lines.append("## Top Outgoing Dependencies")
        lines.append("")
        lines.append("These files depend on the most other files (highest out-degree):")
        lines.append("")
        lines.append("| Rank | File | Dependencies | Language |")
        lines.append("|------|------|-------------|----------|")
        for i, entry in enumerate(report.top_outgoing, 1):
            lines.append(f"| {i} | `{entry.path}` | {entry.count} | {entry.language.value} |")
        lines.append("")

    # --- Cycle detection
    if report.cycle_report and report.cycle_report.cycle_count > 0:
        cr = report.cycle_report
        lines.append("## Dependency Cycles")
        lines.append("")
        lines.append(f"**{cr.cycle_count} cycle(s)** detected — Severity: `{cr.severity.value}`")
        lines.append(f"**{cr.total_files_in_cycles} file(s)** participate in cycles.")
        lines.append("")
        if cr.top_cycle_participants:
            lines.append("### Top Cycle Participants")
            lines.append("")
            lines.append("| File | Cycles |")
            lines.append("|------|--------|")
            for path, count in cr.top_cycle_participants[:10]:
                lines.append(f"| `{path}` | {count} |")
            lines.append("")
        if cr.cycles:
            lines.append("### Cycle Chains")
            lines.append("")
            for i, cycle in enumerate(cr.cycles[:10], 1):
                chain_str = " → ".join(f"`{n}`" for n in cycle)
                lines.append(f"{i}. {chain_str}")
            lines.append("")
    elif report.cycle_report:
        lines.append("## Dependency Cycles")
        lines.append("")
        lines.append("No dependency cycles detected. The dependency graph is acyclic.")
        lines.append("")

    # --- Unresolved dependencies
    if report.unresolved_summary:
        lines.append(f"## Unresolved Dependencies ({len(report.unresolved_summary)} shown, max 50)")
        lines.append("")
        lines.append("| File | Raw Dependency | Line | Note |")
        lines.append("|------|---------------|------|------|")
        seen: set[str] = set()
        for dep in report.unresolved_summary:
            key = f"{dep.raw.raw_text}:{dep.raw.line_number}"
            if key in seen:
                continue
            seen.add(key)
            note = dep.resolution_note[:60] if dep.resolution_note else ""
            lines.append(f"| - | `{dep.raw.raw_text}` | {dep.raw.line_number} | {note} |")
        lines.append("")

    # --- External dependencies
    if report.external_summary:
        lines.append(f"## External Dependencies ({len(report.external_summary)} shown, max 50)")
        lines.append("")
        lines.append("| Raw Dependency | Kind |")
        lines.append("|---------------|------|")
        seen = set()
        for dep in report.external_summary:
            if dep.raw.raw_text in seen:
                continue
            seen.add(dep.raw.raw_text)
            lines.append(f"| `{dep.raw.raw_text}` | {dep.raw.kind.value} |")
        lines.append("")

    # --- High-risk files
    if report.high_risk_files:
        lines.append(f"## High-Risk Files ({len(report.high_risk_files)})")
        lines.append("")
        lines.append("Files with high in-degree or involved in dependency cycles:")
        lines.append("")
        for f in report.high_risk_files[:20]:
            lines.append(f"- `{f}`")
        lines.append("")

    # --- Legend
    lines.append("## Legend")
    lines.append("")
    lines.append("- **In-degree**: number of files that depend on this file")
    lines.append("- **Out-degree**: number of files this file depends on")
    lines.append("- **Blast radius**: % of project files affected by a change")
    lines.append("")

    return "\n".join(lines)


def write_markdown_report(report: AuditReport, output_path: Path) -> None:
    """Write an audit report as Markdown to a file."""
    content = audit_report_to_markdown(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
