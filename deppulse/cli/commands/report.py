"""report command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.risk import compute_risk_score
from deppulse.reporting import (
    assemble_audit_report,
    audit_report_to_json,
    graph_to_sarif,
    write_json_report,
    write_markdown_report,
    write_sarif_report,
)
from deppulse.ui import render as ui

COMMAND_NAME = "report"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Generate a full audit report",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument("--json-output", type=Path, help="Write JSON report to file")
    parser.add_argument("--markdown-output", type=Path, help="Write Markdown report to file")
    parser.add_argument("--include-cycles", action="store_true", help="Include cycle details")
    parser.add_argument("--include-risk", action="store_true", help="Include risk assessments")
    parser.add_argument("--include-unresolved", action="store_true", help="Include unresolved deps")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    parser.add_argument(
        "--sarif-output", type=Path, help="Write SARIF report to file"
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan

    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, elapsed = run_scan(args.path, config, use_cache=args.use_cache)

    cycle_report = find_cycles(graph) if args.include_cycles else None

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
        ui.render_scan_result(result)
        if cycle_report and cycle_report.cycle_count > 0:
            ui.render_cycle_report(cycle_report)

    return 0


def find_cycles(graph):
    """Find cycles in the graph (imported here to avoid circular imports)."""
    from deppulse.core.cycles import find_cycles as _find
    return _find(graph)
