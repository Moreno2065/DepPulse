"""cycles command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.cycles import find_cycles
from deppulse.ui import render as ui

COMMAND_NAME = "cycles"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Detect dependency cycles in the project",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan

    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _elapsed = run_scan(args.path, config, use_cache=args.use_cache)

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
