"""diff command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.risk import compute_risk_score
from deppulse.git import get_changed_files, get_git_status_summary, is_git_repo
from deppulse.reporting import assemble_audit_report, write_markdown_report
from deppulse.ui import render as ui

COMMAND_NAME = "diff"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Analyze git diff for change impact",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument("--staged", action="store_true", help="Analyze staged changes")
    parser.add_argument("--ref", type=str, help="Compare against a git ref (e.g. main)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--markdown", type=Path, help="Write impact report as Markdown")
    parser.add_argument(
        "--max-chains", type=int, default=50, help="Max impact chains (default: 50)"
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan

    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    if not is_git_repo(project_path):
        ui.console.print("[yellow]Not a git repository.[/yellow]")
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

    config = DepPulseConfig.from_path(project_path)
    result, graph, _elapsed = run_scan(project_path, config, use_cache=args.use_cache, debug=args.debug)

    graph_files = set(graph.nodes())
    found = [f for f in changed_files if f in graph_files]
    not_found = [f for f in changed_files if f not in graph_files]

    impact = None
    risk = None
    if found:
        analyzer = ImpactAnalyzer(graph)
        impact = analyzer.analyze_files(found, max_chains=args.max_chains)
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
        if risk:
            ui.render_risk_report(risk)

    if args.markdown and impact:
        audit = assemble_audit_report(result, graph, scan_duration=0.0)
        write_markdown_report(audit, args.markdown)
        ui.console.print(f"[green]Markdown report written to {args.markdown}[/green]")

    return 0
