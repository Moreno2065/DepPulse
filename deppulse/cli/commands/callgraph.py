"""callgraph command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.callgraph import (
    CallGraphBuilder,
    callgraph_to_dot,
    callgraph_to_json,
    callgraph_to_mermaid,
)
from deppulse.ui import render as ui

COMMAND_NAME = "callgraph"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Build a symbol-level call graph from scan results",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument(
        "--file", type=str, default=None,
        help="Only analyze symbols in the given file",
    )
    parser.add_argument(
        "--format",
        choices=["json", "mermaid", "dot"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file (default: stdout)",
    )
    parser.add_argument(
        "--max-nodes", type=int, default=100,
        help="Max nodes to show in mermaid/dot output (default: 100)",
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan
    from deppulse.models import CallGraphResult as Cgr

    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _elapsed = run_scan(args.path, config, use_cache=args.use_cache)

    builder = CallGraphBuilder(scan_results=result.scan_results, project_root=str(args.path.resolve()))
    cg_result = builder.build()

    if args.file:
        filtered_nodes = [s for s in cg_result.nodes if args.file in s.file_path]
        filtered_edges = [
            e for e in cg_result.edges
            if args.file in e.caller.file_path or args.file in e.callee.file_path
        ]
        cg_result = Cgr(
            project_root=cg_result.project_root,
            scanned_at=cg_result.scanned_at,
            nodes=filtered_nodes,
            edges=filtered_edges,
            stats=cg_result.stats,
            warnings=cg_result.warnings,
        )

    if args.format == "json":
        data = callgraph_to_json(cg_result)
        if args.output:
            import json
            args.output.write_text(json.dumps(data, indent=2), encoding="utf-8")
            ui.console.print(f"[green]Call graph written to {args.output}[/green]")
        else:
            ui.render_json_output(data)
    elif args.format == "mermaid":
        output = callgraph_to_mermaid(cg_result, max_nodes=args.max_nodes)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]Mermaid diagram written to {args.output}[/green]")
        else:
            print(output)
    elif args.format == "dot":
        output = callgraph_to_dot(cg_result)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]DOT graph written to {args.output}[/green]")
        else:
            print(output)

    return 0
