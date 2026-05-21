"""CLI entry point for DepPulse using argparse and rich."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional

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
    audit_report_to_markdown,
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
) -> tuple[GraphBuildResult, nx.DiGraph, float]:
    """Run the orchestrator scan and return results with timing."""
    orchestrator = DependencyOrchestrator(config=config, use_cache=use_cache)
    start = time.monotonic()
    result = orchestrator.scan(project_path)
    elapsed = time.monotonic() - start

    # Build the graph from scan results
    G = _build_graph_from_results(result)
    return result, G, elapsed


def _build_graph_from_results(result: GraphBuildResult) -> nx.DiGraph:
    """Rebuild the networkx graph from a GraphBuildResult."""
    G = nx.DiGraph()
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
        G.add_node(scan_result.file_path, **vars(meta))

    for scan_result in result.scan_results:
        for resolved in scan_result.internal_dependencies:
            if resolved.normalized_path is None:
                continue
            if resolved.normalized_path not in G:
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
                G.add_node(resolved.normalized_path, **vars(ghost))
            G.add_edge(
                scan_result.file_path,
                resolved.normalized_path,
                raw_text=resolved.raw.raw_text,
                kind=resolved.raw.kind.value,
                line_number=resolved.raw.line_number,
            )
    return G


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
    if args.incremental or args.since:
        project_path = args.path.resolve()
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository. Use 'deppulse scan' without --incremental.[/yellow]")
            return 1

        # Build list of files to scan
        import subprocess
        git_cmd = ["git", "-C", str(project_path), "diff", "--name-only", "HEAD"]
        if args.since:
            git_cmd = ["git", "-C", str(project_path), "diff", "--name-only", args.since, "HEAD"]
        try:
            changed_raw = subprocess.check_output(git_cmd, text=True, timeout=30)
            changed_files = [f.strip() for f in changed_raw.strip().split("\n") if f.strip()]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            ui.console.print("[yellow]Could not read git diff. Falling back to full scan.[/yellow]")
            changed_files = None

        if changed_files:
            ui.console.print(f"[dim]Incremental scan: {len(changed_files)} changed file(s)[/dim]")
        else:
            ui.console.print("[dim]No changed files found. Use full scan for initial analysis.[/dim]")

        # For now, fall back to full scan but pass the changed files list
        # The orchestrator can be extended to accept a file filter list
        result, G, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache)
    else:
        result, G, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache)

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
    
    result, G, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache)

    mutated_files = args.mutated_file
    # Normalize paths
    normalized = []
    for f in mutated_files:
        f_str = str(f).replace("\\", "/")
        if f_str.startswith(str(args.path)):
            from deppulse.models import normalize_path_to_posix
            f_str = normalize_path_to_posix(f_str, str(args.path))
        normalized.append(f_str)

    # Filter to files in the graph
    graph_files = set(G.nodes())
    found = [f for f in normalized if f in graph_files]
    not_found = [f for f in normalized if f not in graph_files]

    if not found:
        ui.console.print("[yellow]Warning: none of the specified files were found in the graph.[/yellow]")
        return 0

    if not_found:
        ui.console.print(f"[dim]Files not in graph (unsupported or filtered): {not_found}[/dim]")

    analyzer = ImpactAnalyzer(G)
    impact = analyzer.analyze_files(found, max_chains=args.max_chains)

    # Compute risk
    risk = compute_risk_score(
        G, found,
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
    result, G, _ = _run_scan(project_path, config, use_cache=not args.no_cache)

    # Filter changed files to those in the graph
    graph_files = set(G.nodes())
    found = [f for f in changed_files if f in graph_files]
    not_found = [f for f in changed_files if f not in graph_files]

    # Compute impact
    impact = None
    if found:
        analyzer = ImpactAnalyzer(G)
        impact = analyzer.analyze_files(found, max_chains=args.max_chains)

        # Compute risk
        risk = compute_risk_score(
            G, found,
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
        audit = assemble_audit_report(result, G, scan_duration=0.0)
        write_markdown_report(audit, args.markdown)
        ui.console.print(f"[green]Markdown report written to {args.markdown}[/green]")

    return 0


def _cmd_cycles(args: argparse.Namespace) -> int:
    """Handle: deppulse cycles <path>"""
    config = DepPulseConfig.from_path(args.path.resolve())
    
    if args.ci:
        ui.set_ci_mode(True)
    
    result, G, _ = _run_scan(args.path, config, use_cache=not args.no_cache)

    cycle_report = find_cycles(G)

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
    
    start = time.monotonic()
    result, G, elapsed = _run_scan(args.path, config, use_cache=not args.no_cache)

    cycle_report = find_cycles(G) if args.include_cycles else None

    # Compute risk for high-impact files
    risk_report = None
    if args.include_risk:
        top_files = sorted(G.nodes(), key=lambda n: G.in_degree(n), reverse=True)[:5]
        if top_files:
            blast = len(top_files) / max(G.number_of_nodes(), 1) * 100
            risk_report = compute_risk_score(
                G, top_files,
                blast_radius_percent=blast,
                cycle_count=cycle_report.cycle_count if cycle_report else 0,
                cycle_files=cycle_report.total_files_in_cycles if cycle_report else 0,
            )

    audit = assemble_audit_report(result, G, cycle_report, scan_duration=elapsed)

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

    result, G, _ = _run_scan(args.path, config, use_cache=not args.no_cache)

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
        from deppulse.models import CallGraphResult as _CGR
        cg_result = _CGR(
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

    result, G, _ = _run_scan(args.path, config, use_cache=not args.no_cache)

    # Filter graph by focus/depth/risk
    if args.focus:
        focus_node = args.focus.replace("\\", "/")
        if focus_node not in G:
            ui.console.print(f"[yellow]Focus file '{focus_node}' not found in graph.[/yellow]")
            return 1

        if args.depth > 0:
            # BFS to depth
            import networkx as nx
            ancestors = nx.descendants(G, focus_node)
            descendants = nx.descendants(G.reverse(copy=False), focus_node)
            nodes_to_keep = ancestors | descendants | {focus_node}
            nodes_to_remove = set(G.nodes()) - nodes_to_keep
            G.remove_nodes_from(nodes_to_remove)

    if args.format == "html":
        from deppulse.ui.visualize import render_html_dashboard
        output_path = args.output or Path("deppulse-dashboard.html")
        render_html_dashboard(result, G, output_path=output_path)
        ui.console.print(f"[green]HTML dashboard written to {output_path}[/green]")
    elif args.format == "mermaid":
        from deppulse.ui.visualize import render_mermaid_graph
        from deppulse.ui.visualize import graph_to_mermaid
        output = render_mermaid_graph(G, title="Dependency Graph")
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]Mermaid diagram written to {args.output}[/green]")
        else:
            print(output)
    elif args.format == "dot":
        from deppulse.ui.visualize import render_dot_graph
        output = render_dot_graph(G, title="Dependency Graph")
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
        from deppulse.core.orchestrator import DependencyOrchestrator, _SCANNER_REGISTRY
        for dirpath, dirnames, filenames in __import__("os").walk(project_path):
            dirnames[:] = [d for d in dirnames if not config.should_ignore_dir(d)]
            for fname in filenames:
                if config.should_ignore_file(fname):
                    continue
                from pathlib import Path as P
                if any(s.can_scan(P(dirpath) / fname) for s in _SCANNER_REGISTRY):
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
        f"Loaded from deppulse.json" if config._config_file and config._config_file.exists()
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
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
        if "--debug" in (argv or []):
            import traceback
            traceback.print_exc()
        return 1
