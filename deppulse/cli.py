"""CLI entry point for DepPulse using argparse and rich."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

import networkx as nx

from deppulse import __version__
from deppulse.cache import ScanCache
from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.cycles import find_cycles
from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.core.risk import compute_risk_score
from deppulse.git import get_changed_files, get_git_status_summary, is_git_repo
from deppulse.models import GraphBuildResult
from deppulse.reporting import (
    assemble_audit_report,
    audit_report_to_json,
    graph_to_sarif,
    write_json_report,
    write_markdown_report,
    write_sarif_report,
)
from deppulse.ui import render as ui

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deppulse",
        description="Local source-code dependency topology and change-impact auditing tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"deppulse {__version__}")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print full traceback on error",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching and rescan all files",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- scan ----
    scan_parser = sub.add_parser(
        "scan",
        help="Scan a project and build the dependency graph",
    )
    scan_parser.add_argument("path", type=Path, default=".", help="Project root path")
    scan_parser.add_argument("--json", action="store_true", help="Output as JSON")
    scan_parser.add_argument("--show-table", action="store_true", help="Show dependency table")
    scan_parser.add_argument("--show-unresolved", action="store_true", help="Show unresolved deps")
    scan_parser.add_argument(
        "--max-file-size", type=int, default=512, help="Max file size in KB to scan (default: 512)"
    )
    scan_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )
    scan_parser.add_argument(
        "--sarif-output", type=Path, help="Write SARIF report to file"
    )

    # ---- trace ----
    trace_parser = sub.add_parser(
        "trace",
        help="Trace impact of changes to specific file(s)",
    )
    trace_parser.add_argument("path", type=Path, help="Project root path")
    trace_parser.add_argument(
        "mutated_file",
        nargs="+",
        help="File path(s) that were or will be changed",
    )
    trace_parser.add_argument("--json", action="store_true", help="Output as JSON")
    trace_parser.add_argument(
        "--max-chains", type=int, default=50, help="Max impact chains to show (default: 50)"
    )
    trace_parser.add_argument("--show-chains", action="store_true", help="Show impact chains")
    trace_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- diff ----
    diff_parser = sub.add_parser(
        "diff",
        help="Analyze git diff for change impact",
    )
    diff_parser.add_argument("path", type=Path, default=".", help="Project root path")
    diff_parser.add_argument("--staged", action="store_true", help="Analyze staged changes")
    diff_parser.add_argument("--ref", type=str, help="Compare against a git ref (e.g. main)")
    diff_parser.add_argument("--json", action="store_true", help="Output as JSON")
    diff_parser.add_argument("--markdown", type=Path, help="Write impact report as Markdown")
    diff_parser.add_argument(
        "--max-chains", type=int, default=50, help="Max impact chains (default: 50)"
    )
    diff_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- cycles ----
    cycles_parser = sub.add_parser(
        "cycles",
        help="Detect dependency cycles in the project",
    )
    cycles_parser.add_argument("path", type=Path, default=".", help="Project root path")
    cycles_parser.add_argument("--json", action="store_true", help="Output as JSON")
    cycles_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- report ----
    report_parser = sub.add_parser(
        "report",
        help="Generate a full audit report",
    )
    report_parser.add_argument("path", type=Path, default=".", help="Project root path")
    report_parser.add_argument("--json-output", type=Path, help="Write JSON report to file")
    report_parser.add_argument("--markdown-output", type=Path, help="Write Markdown report to file")
    report_parser.add_argument("--include-cycles", action="store_true", help="Include cycle details")
    report_parser.add_argument("--include-risk", action="store_true", help="Include risk assessments")
    report_parser.add_argument("--include-unresolved", action="store_true", help="Include unresolved deps")
    report_parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    report_parser.add_argument(
        "--sarif-output", type=Path, help="Write SARIF report to file"
    )
    report_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- doctor ----
    doctor_parser = sub.add_parser(
        "doctor",
        help="Validate environment and project readiness",
    )
    doctor_parser.add_argument("path", type=Path, default=".", help="Project root path")
    doctor_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- callgraph ----
    callgraph_parser = sub.add_parser(
        "callgraph",
        help="Build a symbol-level call graph from scan results",
    )
    callgraph_parser.add_argument("path", type=Path, default=".", help="Project root path")
    callgraph_parser.add_argument(
        "--file", type=str, default=None,
        help="Only analyze symbols in the given file (project-relative path)",
    )
    callgraph_parser.add_argument(
        "--format",
        choices=["json", "mermaid", "dot"],
        default="json",
        help="Output format (default: json)",
    )
    callgraph_parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file (default: stdout)",
    )
    callgraph_parser.add_argument(
        "--max-nodes", type=int, default=100,
        help="Max nodes to show in mermaid/dot output (default: 100)",
    )
    callgraph_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- viz ----
    viz_parser = sub.add_parser(
        "viz",
        help="Generate a visual dependency graph (Mermaid, DOT, or HTML dashboard)",
    )
    viz_parser.add_argument("path", type=Path, default=".", help="Project root path")
    viz_parser.add_argument(
        "--format",
        choices=["html", "mermaid", "dot"],
        default="html",
        help="Output format (default: html)",
    )
    viz_parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file (default: deppulse-dashboard.html / stdout)",
    )
    viz_parser.add_argument(
        "--focus", type=str, default=None,
        help="Focus on a specific file and its direct dependencies",
    )
    viz_parser.add_argument(
        "--depth", type=int, default=0,
        help="Depth of dependency traversal (0 = all, 1 = 1-hop, etc.)",
    )
    viz_parser.add_argument(
        "--risk-level",
        choices=["LOW", "MEDIUM", "HIGH"],
        default=None,
        help="Only show nodes at or above this risk level",
    )
    viz_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- tests ----
    tests_parser = sub.add_parser(
        "tests",
        help="Select affected tests based on changed files",
    )
    tests_parser.add_argument("path", type=Path, default=".", help="Project root path")
    tests_parser.add_argument(
        "--since", type=str, default=None,
        help="Compare against a git ref (e.g. main, HEAD~5)",
    )
    tests_parser.add_argument(
        "--files", type=str, nargs="+",
        help="Explicitly specify changed file paths",
    )
    tests_parser.add_argument(
        "--format",
        choices=["list", "args", "json"],
        default="list",
        help="Output format (default: list)",
    )
    tests_parser.add_argument(
        "--max-blast", type=int, default=50,
        help="Max number of selected tests before fallback to all (default: 50)",
    )
    tests_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- snapshot ----
    snapshot_parser = sub.add_parser(
        "snapshot",
        help="Manage dependency graph snapshots for trend monitoring",
    )
    snapshot_sub = snapshot_parser.add_subparsers(dest="snapshot_cmd", required=True)

    save_parser = snapshot_sub.add_parser("save", help="Save a new snapshot")
    save_parser.add_argument("path", type=Path, default=".", help="Project root path")
    save_parser.add_argument(
        "--tag", type=str, default=None,
        help="Optional tag for this snapshot (e.g. v0.2.0)",
    )

    diff_parser = snapshot_sub.add_parser("diff", help="Compare two snapshots")
    diff_parser.add_argument("path", type=Path, default=".", help="Project root path")
    diff_parser.add_argument("--from", dest="from_tag", type=str, required=True, help="Older snapshot tag")
    diff_parser.add_argument("--to", dest="to_tag", type=str, required=True, help="Newer snapshot tag")
    diff_parser.add_argument("--json", action="store_true", help="Output as JSON")

    list_parser = snapshot_sub.add_parser("list", help="List all saved snapshots")
    list_parser.add_argument("path", type=Path, default=".", help="Project root path")

    check_parser = snapshot_sub.add_parser("check", help="Check trends since a snapshot (CI mode)")
    check_parser.add_argument("path", type=Path, default=".", help="Project root path")
    check_parser.add_argument(
        "--since-tag", dest="since_tag", type=str, required=True,
        help="Compare against this snapshot tag",
    )
    check_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- pr-report ----
    pr_parser = sub.add_parser(
        "pr-report",
        help="Generate a PR impact report for code review",
    )
    pr_parser.add_argument("path", type=Path, default=".", help="Project root path")
    pr_parser.add_argument(
        "--base", type=str, default="main",
        help="Base branch to compare against (default: main)",
    )
    pr_parser.add_argument(
        "--format",
        choices=["github-comment", "markdown", "json"],
        default="github-comment",
        help="Output format (default: github-comment)",
    )
    pr_parser.add_argument(
        "--fail-on-high-risk", action="store_true",
        help="Exit with code 1 if risk level is HIGH",
    )
    pr_parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file instead of stdout",
    )
    pr_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )

    # ---- Shared incremental flags on scan ----
    # Add --incremental and --since to scan parser
    scan_parser.add_argument(
        "--incremental", action="store_true",
        help="Only scan changed files (requires git repo)",
    )
    scan_parser.add_argument(
        "--since", type=str, default=None,
        help="Scan files changed since a git ref or date (e.g. HEAD~5, 1 week ago, 2026-01-01)",
    )

    return parser


# ---------------------------------------------------------------------------
# Shared scan helper
# ---------------------------------------------------------------------------

def _run_scan(
    project_path: Path,
    config: DepPulseConfig,
    use_cache: bool,
    files_to_scan: set[str] | None = None,
) -> tuple[GraphBuildResult, nx.DiGraph, float]:
    """Run the orchestrator scan and return results with timing."""
    orchestrator = DependencyOrchestrator(config=config, use_cache=use_cache)
    start = time.monotonic()
    result = orchestrator.scan(project_path, files_to_scan=files_to_scan)
    elapsed = time.monotonic() - start

    graph = _build_graph_from_results(result)
    return result, graph, elapsed


def _build_graph_from_results(result: GraphBuildResult) -> nx.DiGraph:
    """Rebuild the networkx graph from a GraphBuildResult using the orchestrator."""
    # Use the orchestrator's authoritative builder; reuse the same logic path.
    # Build a minimal scan_results list so the orchestrator path is shared.
    from deppulse.core.orchestrator import DependencyOrchestrator

    graph = nx.DiGraph()

    for scan_result in result.scan_results:
        from deppulse.models import NodeMetadata
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
                from deppulse.models import Language, NodeMetadata
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


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_scan(args: argparse.Namespace) -> int:
    """Handle: deppulse scan <path>"""
    config = DepPulseConfig.from_path(args.path.resolve())
    config.max_file_size_kb = args.max_file_size

    if args.ci:
        ui.set_ci_mode(True)

    # Incremental / --since mode
    files_to_scan: set[str] | None = None
    if args.incremental or args.since:
        project_path = args.path.resolve()
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository. Use 'deppulse scan' without --incremental.[/yellow]")
            return 1

        git_cmd = ["git", "-C", str(project_path), "diff", "--name-only", "HEAD"]
        if args.since:
            git_cmd = ["git", "-C", str(project_path), "diff", "--name-only", args.since, "HEAD"]
        try:
            import subprocess
            changed_raw = subprocess.check_output(git_cmd, text=True, timeout=30)
            changed_files = [f.strip() for f in changed_raw.strip().split("\n") if f.strip()]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            ui.console.print("[yellow]Could not read git diff. Falling back to full scan.[/yellow]")
            changed_files = None

        if changed_files:
            ui.console.print(f"[dim]Incremental scan: {len(changed_files)} changed file(s)[/dim]")
            files_to_scan = set(changed_files)
        else:
            ui.console.print("[dim]No changed files found. Use full scan for initial analysis.[/dim]")
            files_to_scan = None

    result, graph, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache, files_to_scan=files_to_scan)

    # Write SARIF first so it's produced regardless of other output flags
    if args.sarif_output:
        sarif_dict = graph_to_sarif(result)
        write_sarif_report(sarif_dict, args.sarif_output)
        ui.console.print(f"[green]SARIF report written to {args.sarif_output}[/green]")

    if args.json:
        from deppulse.reporting import _graph_stats_to_dict
        data = {
            "project_root": result.project_root,
            "scanned_at": result.scanned_at.isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "stats": _graph_stats_to_dict(result.stats),
            "warnings": result.warnings,
        }
        ui.render_json_output(data)
    else:
        ui.render_scan_result(result, show_table=args.show_table, show_unresolved=args.show_unresolved)
        if not args.ci:
            print(f"\nScanned in {elapsed:.2f}s")
        else:
            print(f"Scanned in {elapsed:.2f}s")

    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    """Handle: deppulse trace <path> <mutated_file>"""
    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache)

    mutated_files = args.mutated_file
    # Normalize paths to project-relative POSIX format (must match graph node keys)
    from deppulse.models import normalize_path_to_posix
    project_path_str = str(args.path.resolve())
    normalized = []
    for f in mutated_files:
        f_str = str(f).replace("\\", "/")
        # If absolute path, convert to project-relative
        if os.path.isabs(f_str):
            f_str = normalize_path_to_posix(f_str, project_path_str)
        # If relative path, resolve against cwd and then make project-relative
        elif not f_str.startswith("/") and not (len(f_str) > 1 and f_str[1] == ":"):
            # Try resolving against the actual project directory
            candidate = (args.path / f_str).resolve()
            if candidate.exists():
                f_str = normalize_path_to_posix(str(candidate), project_path_str)
            else:
                # Assume it's already project-relative (e.g. "deppulse/cli.py")
                pass
        normalized.append(f_str)

    # Filter to files in the graph
    graph_files = set(graph.nodes())
    found = [f for f in normalized if f in graph_files]
    not_found = [f for f in normalized if f not in graph_files]

    if not found:
        ui.console.print("[yellow]Warning: none of the specified files were found in the graph.[/yellow]")
        return 0

    if not_found:
        ui.console.print(f"[dim]Files not in graph (unsupported or filtered): {not_found}[/dim]")

    analyzer = ImpactAnalyzer(graph)
    impact = analyzer.analyze_files(found, max_chains=args.max_chains)

    # Compute risk
    risk = compute_risk_score(
        graph, found,
        blast_radius_percent=impact.blast_radius_percent,
    )

    if args.json:
        ui.render_json_output({
            "mutated_files": found,
            "affected_files": impact.all_affected_files,
            "blast_radius_percent": impact.blast_radius_percent,
            "combined_affected_count": impact.combined_affected_count,
            "total_files": impact.total_files_in_project,
            "risk_score": risk.score,
            "risk_level": risk.level.value,
            "risk_components": [
                {
                    "name": c.name,
                    "weight": c.weight,
                    "contribution": c.contribution,
                    "explanation": c.explanation,
                }
                for c in risk.components
            ],
        })
    else:
        ui.render_impact_report(impact, show_chains=args.show_chains, max_chains=args.max_chains)
        ui.render_risk_report(risk)

    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Handle: deppulse diff <path>"""
    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    if not is_git_repo(project_path):
        ui.console.print("[yellow]Not a git repository. Run 'deppulse scan' instead.[/yellow]")
        return 1

    git_summary = get_git_status_summary(project_path)
    changed_files = get_changed_files(
        project_path,
        staged=args.staged,
        ref=args.ref,
    )

    if not changed_files:
        ui.console.print("[green]No changed files detected.[/green]")
        return 0

    # Run the scan
    config = DepPulseConfig.from_path(project_path)
    result, graph, _ = _run_scan(project_path, config, use_cache=not args.no_cache)

    # Filter changed files to those in the graph
    graph_files = set(graph.nodes())
    found = [f for f in changed_files if f in graph_files]
    not_found = [f for f in changed_files if f not in graph_files]

    # Compute impact
    impact = None
    if found:
        analyzer = ImpactAnalyzer(graph)
        impact = analyzer.analyze_files(found, max_chains=args.max_chains)

        # Compute risk
        risk = compute_risk_score(
            graph, found,
            blast_radius_percent=impact.blast_radius_percent,
        )

    if args.json:
        data = {
            "changed_files": changed_files,
            "changed_in_graph": found,
            "unsupported": not_found,
            "git_summary": git_summary,
        }
        if impact:
            data["impact"] = {
                "blast_radius_percent": impact.blast_radius_percent,
                "combined_affected_count": impact.combined_affected_count,
                "all_affected_files": impact.all_affected_files,
                "risk_score": risk.score,
                "risk_level": risk.level.value,
            }
        ui.render_json_output(data)
    else:
        ui.render_diff_report(found, not_found, impact, git_summary)
        if impact:
            ui.render_risk_report(risk)

    # Write markdown if requested
    if args.markdown and impact:
        audit = assemble_audit_report(result, graph, scan_duration=0.0)
        write_markdown_report(audit, args.markdown)
        ui.console.print(f"[green]Markdown report written to {args.markdown}[/green]")

    return 0


def _cmd_cycles(args: argparse.Namespace) -> int:
    """Handle: deppulse cycles <path>"""
    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _ = _run_scan(args.path, config, use_cache=not args.no_cache)

    cycle_report = find_cycles(graph)

    if args.json:
        ui.render_json_output({
            "cycle_count": cycle_report.cycle_count,
            "cycles": [{"nodes": c.nodes, "length": c.length} for c in cycle_report.cycles],
            "top_participants": [
                {"path": p, "count": c} for p, c in cycle_report.top_cycle_participants
            ],
            "severity": cycle_report.severity.value,
            "total_files_in_cycles": cycle_report.total_files_in_cycles,
        })
    else:
        ui.render_cycle_report(cycle_report)

    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Handle: deppulse report <path>"""
    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache)

    cycle_report = find_cycles(graph) if args.include_cycles else None

    # Compute risk for high-impact files
    if args.include_risk:
        top_files = sorted(graph.nodes(), key=lambda n: graph.in_degree(n), reverse=True)[:5]
        if top_files:
            blast = len(top_files) / max(graph.number_of_nodes(), 1) * 100
            compute_risk_score(
                graph, top_files,
                blast_radius_percent=blast,
                cycle_count=cycle_report.cycle_count if cycle_report else 0,
                cycle_files=cycle_report.total_files_in_cycles if cycle_report else 0,
            )

    audit = assemble_audit_report(result, graph, cycle_report, scan_duration=elapsed)

    if args.json_output:
        write_json_report(audit, args.json_output)
        ui.console.print(f"[green]JSON report written to {args.json_output}[/green]")

    if args.markdown_output:
        write_markdown_report(audit, args.markdown_output)
        ui.console.print(f"[green]Markdown report written to {args.markdown_output}[/green]")

    if args.sarif_output:
        sarif_dict = graph_to_sarif(result, cycle_report=cycle_report)
        write_sarif_report(sarif_dict, args.sarif_output)
        ui.console.print(f"[green]SARIF report written to {args.sarif_output}[/green]")

    if args.json:
        ui.render_json_output(audit_report_to_json(audit))
    elif not args.json_output and not args.markdown_output and not args.sarif_output:
        # Default: pretty console output
        ui.render_scan_result(result)
        if cycle_report and cycle_report.cycle_count > 0:
            ui.render_cycle_report(cycle_report)

    return 0


def _cmd_callgraph(args: argparse.Namespace) -> int:
    """Handle: deppulse callgraph <path>"""
    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _ = _run_scan(args.path, config, use_cache=not args.no_cache)

    # Build call graph
    from deppulse.core.callgraph import CallGraphBuilder
    builder = CallGraphBuilder(scan_results=result.scan_results, project_root=str(args.path.resolve()))
    cg_result = builder.build()

    # Filter to a specific file if requested
    if args.file:
        filtered_nodes = [s for s in cg_result.nodes if args.file in s.file_path]
        filtered_edges = [
            e for e in cg_result.edges
            if args.file in e.caller.file_path or args.file in e.callee.file_path
        ]
        from deppulse.models import CallGraphResult as Cgr
        cg_result = Cgr(
            project_root=cg_result.project_root,
            scanned_at=cg_result.scanned_at,
            nodes=filtered_nodes,
            edges=filtered_edges,
            stats=cg_result.stats,
            warnings=cg_result.warnings,
        )

    # Render
    if args.format == "json":
        from deppulse.core.callgraph import callgraph_to_json
        data = callgraph_to_json(cg_result)
        if args.output:
            import json
            args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
            ui.console.print(f"[green]Call graph written to {args.output}[/green]")
        else:
            ui.render_json_output(data)
    elif args.format == "mermaid":
        from deppulse.core.callgraph import callgraph_to_mermaid
        output = callgraph_to_mermaid(cg_result, max_nodes=args.max_nodes)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]Mermaid diagram written to {args.output}[/green]")
        else:
            print(output)
    elif args.format == "dot":
        from deppulse.core.callgraph import callgraph_to_dot
        output = callgraph_to_dot(cg_result)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]DOT graph written to {args.output}[/green]")
        else:
            print(output)

    return 0


def _cmd_viz(args: argparse.Namespace) -> int:
    """Handle: deppulse viz <path>"""
    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _ = _run_scan(args.path, config, use_cache=not args.no_cache)

    # Filter graph by focus/depth/risk
    if args.focus:
        focus_node = args.focus.replace("\\", "/")
        if focus_node not in graph:
            ui.console.print(f"[yellow]Focus file '{focus_node}' not found in graph.[/yellow]")
            return 1

        if args.depth > 0:
            # BFS with depth limit: collect all nodes within args.depth hops
            nodes_to_keep: set[str] = {focus_node}
            current_frontier: set[str] = {focus_node}

            for _ in range(args.depth):
                next_frontier: set[str] = set()
                for node in current_frontier:
                    # Descendants: files this node depends on (out-edges)
                    next_frontier.update(graph.successors(node))
                    # Ancestors: files that depend on this node (in-edges)
                    next_frontier.update(graph.predecessors(node))
                nodes_to_keep.update(next_frontier)
                current_frontier = next_frontier

            nodes_to_remove = set(graph.nodes()) - nodes_to_keep
            graph.remove_nodes_from(nodes_to_remove)

    if args.format == "html":
        from deppulse.ui.visualize import render_html_dashboard
        output_path = args.output or Path("deppulse-dashboard.html")
        render_html_dashboard(result, graph, output_path=output_path)
        ui.console.print(f"[green]HTML dashboard written to {output_path}[/green]")
    elif args.format == "mermaid":
        from deppulse.ui.visualize import render_mermaid_graph
        output = render_mermaid_graph(graph, title="Dependency Graph")
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]Mermaid diagram written to {args.output}[/green]")
        else:
            print(output)
    elif args.format == "dot":
        from deppulse.ui.visualize import render_dot_graph
        output = render_dot_graph(graph, title="Dependency Graph")
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]DOT graph written to {args.output}[/green]")
        else:
            print(output)

    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Handle: deppulse doctor <path>"""
    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    config = DepPulseConfig.from_path(project_path)

    project_exists = project_path.exists() and project_path.is_dir()
    git_detected = is_git_repo(project_path)

    # Count supported files
    supported = 0
    try:
        from deppulse.core.orchestrator import _SCANNER_REGISTRY
        for dirpath, dirnames, filenames in __import__("os").walk(project_path):
            dirnames[:] = [d for d in dirnames if not config.should_ignore_dir(d)]
            for fname in filenames:
                if config.should_ignore_file(fname):
                    continue
                if any(s.can_scan(Path(dirpath) / fname) for s in _SCANNER_REGISTRY):
                    supported += 1
    except OSError:
        supported = 0

    cache_dir = config.cache_dir
    cache_exists = cache_dir.exists()
    cache_stats = {"entries": 0, "size_kb": 0}
    if cache_exists:
        try:
            cache = ScanCache.load(cache_dir)
            cache_stats = cache.get_stats()
        except Exception:
            pass

    if cache_exists:
        cache_status = f"Present ({cache_stats['entries']} entries, {cache_stats['size_kb']}KB)"
    else:
        cache_status = "Not present (no cache)"

    config_loaded = (
        "Loaded from deppulse.json" if config._config_file and config._config_file.exists()
        else "Defaults (no deppulse.json)"
    )

    scanner_names = []
    from deppulse.core.orchestrator import _SCANNER_REGISTRY
    for s in _SCANNER_REGISTRY:
        scanner_names.append(s.name)

    ui.render_doctor(
        project_exists=project_exists,
        is_git_repo=git_detected,
        supported_files=supported,
        config_loaded=config_loaded,
        cache_status=cache_status,
        scanner_names=scanner_names,
    )

    return 0


def _cmd_tests(args: argparse.Namespace) -> int:
    """Handle: deppulse tests <path>"""
    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    config = DepPulseConfig.from_path(project_path)

    # Get changed files
    changed_files: list[str]
    diff_output: str = ""
    if args.files:
        changed_files = [str(f).replace("\\", "/") for f in args.files]
    elif args.since:
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository. Use --files to specify files directly.[/yellow]")
            return 1
        from deppulse.git import get_changed_files
        changed_files = get_changed_files(project_path, ref=args.since)
        # Get git diff output for DiffParser
        diff_output = _get_git_diff(project_path, ref=args.since)
    else:
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository. Use --files to specify files directly.[/yellow]")
            return 1
        from deppulse.git import get_changed_files
        changed_files = get_changed_files(project_path)
        # Get git diff output for DiffParser
        diff_output = _get_git_diff(project_path)

    if not changed_files:
        ui.console.print("[green]No changed files detected.[/green]")
        return 0

    # Run the scan
    result, graph, _ = _run_scan(project_path, config, use_cache=not args.no_cache)

    # Select tests using DiffParser for line-level analysis
    from deppulse.core.test_selector import TestSelector
    selector = TestSelector(graph, config=config)
    test_result = selector.select_tests(
        changed_files,
        max_blast=args.max_blast,
        diff_output=diff_output,
        project_root=project_path,
    )

    # Output based on format
    if args.format == "json":
        data = {
            "changed_files": test_result.changed_files,
            "selected_tests": test_result.selected_tests,
            "by_strategy": test_result.by_strategy,
            "total_affected": test_result.total_affected,
            "blast_radius_percent": test_result.blast_radius_percent,
            "max_blast_reached": test_result.max_blast_reached,
            "fallback_all": test_result.fallback_all,
            "coverage_confidence": test_result.coverage_confidence,
            "changed_symbols": test_result.changed_symbols,
        }
        ui.render_json_output(data)
    elif args.format == "args":
        print(" ".join(test_result.selected_tests))
    else:  # list
        ui.render_test_selection(test_result)

    # Warn if confidence is low
    if test_result.coverage_confidence < 0.5 and test_result.coverage_confidence > 0:
        ui.console.print(
            f"[yellow]Warning: coverage confidence is {test_result.coverage_confidence:.0%}. "
            f"Consider running more tests or reviewing manually.[/yellow]"
        )

    return 0


def _get_git_diff(project_path: Path, ref: str = "HEAD") -> str:
    """Get git diff output for DiffParser."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "diff", "--unified=0", ref, "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Handle: deppulse snapshot <subcommand>"""
    from deppulse.core.snapshot import SnapshotManager

    project_path = args.path.resolve()
    manager = SnapshotManager(project_path)

    if args.snapshot_cmd == "save":
        config = DepPulseConfig.from_path(project_path)
        result, graph, _ = _run_scan(project_path, config, use_cache=not args.no_cache)
        meta = manager.save(result, tag=args.tag)
        ui.render_snapshot_meta(meta)
        ui.console.print(f"[green]Snapshot saved: {meta.tag}[/green]")
        return 0

    elif args.snapshot_cmd == "list":
        snapshots = manager.list_snapshots()
        ui.render_snapshot_list(snapshots)
        return 0

    elif args.snapshot_cmd == "diff":
        diff = manager.diff(args.from_tag, args.to_tag)
        if args.json:
            data = {
                "older": {"tag": diff.older.tag, "commit_hash": diff.older.commit_hash,
                          "total_files": diff.older.total_files, "total_edges": diff.older.total_edges},
                "newer": {"tag": diff.newer.tag, "commit_hash": diff.newer.commit_hash,
                          "total_files": diff.newer.total_files, "total_edges": diff.newer.total_edges},
                "edges_delta": diff.total_edges_delta,
                "files_delta": diff.files_delta,
                "new_cycles": [{"nodes": c.nodes, "length": c.length} for c in diff.new_cycles_added],
                "alerts": diff.alerts,
            }
            ui.render_json_output(data)
        else:
            ui.render_snapshot_diff(diff)
        return 0

    elif args.snapshot_cmd == "check":
        if args.ci:
            ui.set_ci_mode(True)
        config = DepPulseConfig.from_path(project_path)
        result, graph, _ = _run_scan(project_path, config, use_cache=not args.no_cache)
        diff, alerts = manager.check_trends(args.since_tag)
        ui.render_snapshot_diff(diff)
        ui.render_trend_alerts(alerts)
        if alerts:
            for alert in alerts:
                if alert.severity.upper() == "CRITICAL":
                    return 1
        return 0

    ui.console.print(f"[red]Unknown snapshot subcommand: {args.snapshot_cmd}[/red]")
    return 1


def _cmd_pr_report(args: argparse.Namespace) -> int:
    """Handle: deppulse pr-report <path>"""
    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    if not is_git_repo(project_path):
        ui.console.print("[yellow]Not a git repository. Cannot determine changed files.[/yellow]")
        return 1

    config = DepPulseConfig.from_path(project_path)

    # Get changed files vs base ref
    from deppulse.git import get_changed_files
    changed_files = get_changed_files(project_path, ref=args.base)

    if not changed_files:
        ui.console.print("[green]No changed files detected against base branch.[/green]")
        return 0

    # Run the scan
    result, graph, _ = _run_scan(project_path, config, use_cache=not args.no_cache)

    # Generate PR report
    from deppulse.core.pr_reporter import PRReporter
    reporter = PRReporter(graph, config=config)
    report = reporter.generate(changed_files, base_ref=args.base)

    # Output based on format
    if args.format == "json":
        data = {
            "changed_files": report.changed_files,
            "affected_files": report.affected_files,
            "blast_radius": report.blast_radius,
            "blast_radius_percent": report.blast_radius_percent,
            "risk_score": report.risk_score,
            "risk_level": report.risk_level.value,
            "suggested_tests": report.suggested_tests,
            "top_affected": [
                {"path": e.path, "in_degree": e.in_degree, "risk_level": e.risk_level.value}
                for e in report.top_affected
            ],
        }
        ui.render_json_output(data)
    else:
        markdown = reporter.generate_markdown(report, format=args.format)
        if args.output:
            args.output.write_text(markdown, encoding="utf-8")
            ui.console.print(f"[green]PR report written to {args.output}[/green]")
        else:
            print(markdown)

    # Fail on high risk
    if args.fail_on_high_risk and report.risk_level.value == "HIGH":
        ui.console.print("[red]High risk change detected. Exiting with error.[/red]")
        return 1

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args is None or args.command is None:
        parser.print_help()
        return 0

    handlers = {
        "scan": _cmd_scan,
        "trace": _cmd_trace,
        "diff": _cmd_diff,
        "cycles": _cmd_cycles,
        "report": _cmd_report,
        "doctor": _cmd_doctor,
        "callgraph": _cmd_callgraph,
        "viz": _cmd_viz,
        "tests": _cmd_tests,
        "snapshot": _cmd_snapshot,
        "pr-report": _cmd_pr_report,
    }

    handler = handlers.get(args.command)
    if handler is None:
        ui.console.print(f"[red]Unknown command: {args.command}[/red]")
        return 1

    try:
        return handler(args)
    except KeyboardInterrupt:
        ui.console.print("\n[yellow]Interrupted.[/yellow]")
        return 130
    except Exception as e:
        ui.console.print(f"[red]Error: {e}[/red]")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
