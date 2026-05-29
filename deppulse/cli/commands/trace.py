"""trace command."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.risk import compute_risk_score
from deppulse.models import normalize_path_to_posix
from deppulse.ui import render as ui


COMMAND_NAME = "trace"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Trace impact of changes to specific file(s)",
    )
    parser.add_argument("path", type=Path, help="Project root path")
    parser.add_argument("mutated_file", nargs="+", help="File path(s) that were or will be changed")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--max-chains", type=int, default=50, help="Max impact chains to show (default: 50)"
    )
    parser.add_argument("--show-chains", action="store_true", help="Show impact chains")
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan

    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _elapsed = run_scan(args.path, config, use_cache=True)

    mutated_files = args.mutated_file
    project_path_str = str(args.path.resolve())
    normalized = []
    for f in mutated_files:
        f_str = str(f).replace("\\", "/")
        if os.path.isabs(f_str):
            f_str = normalize_path_to_posix(f_str, project_path_str)
        elif not f_str.startswith("/") and not (len(f_str) > 1 and f_str[1] == ":"):
            candidate = (args.path / f_str).resolve()
            if candidate.exists():
                f_str = normalize_path_to_posix(str(candidate), project_path_str)
        normalized.append(f_str)

    graph_files = set(graph.nodes())
    found = [f for f in normalized if f in graph_files]
    not_found = [f for f in normalized if f not in graph_files]

    if not found:
        ui.console.print("[yellow]Warning: none of the specified files were found in the graph.[/yellow]")
        return 0

    if not_found:
        ui.console.print(f"[dim]Files not in graph: {not_found}[/dim]")

    analyzer = ImpactAnalyzer(graph)
    impact = analyzer.analyze_files(found, max_chains=args.max_chains)

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
