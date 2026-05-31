"""viz command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.ui import render as ui

COMMAND_NAME = "viz"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Generate a visual dependency graph (Mermaid, DOT, or HTML dashboard)",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument(
        "--format",
        choices=["html", "mermaid", "dot"],
        default="html",
        help="Output format (default: html)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file (default: deppulse-dashboard.html / stdout)",
    )
    parser.add_argument(
        "--focus", type=str, default=None,
        help="Focus on a specific file and its direct dependencies",
    )
    parser.add_argument(
        "--depth", type=int, default=0,
        help="Depth of dependency traversal (0 = all, 1 = 1-hop, etc.)",
    )
    parser.add_argument(
        "--risk-level",
        choices=["LOW", "MEDIUM", "HIGH"],
        default=None,
        help="Only show nodes at or above this risk level",
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan
    from deppulse.ui.visualize import (
        render_dot_graph,
        render_html_dashboard,
        render_mermaid_graph,
    )

    config = DepPulseConfig.from_path(args.path.resolve())

    if args.ci:
        ui.set_ci_mode(True)

    result, graph, _elapsed = run_scan(args.path, config, use_cache=args.use_cache, debug=args.debug)

    if args.focus:
        focus_node = args.focus.replace("\\", "/")
        if focus_node not in graph:
            ui.console.print(f"[yellow]Focus file '{focus_node}' not found in graph.[/yellow]")
            return 1

        if args.depth > 0:
            nodes_to_keep: set[str] = {focus_node}
            current_frontier: set[str] = {focus_node}

            for _ in range(args.depth):
                next_frontier: set[str] = set()
                for node in current_frontier:
                    next_frontier.update(graph.successors(node))
                    next_frontier.update(graph.predecessors(node))
                nodes_to_keep.update(next_frontier)
                current_frontier = next_frontier

            nodes_to_remove = set(graph.nodes()) - nodes_to_keep
            graph.remove_nodes_from(nodes_to_remove)

    if args.format == "html":
        output_path = args.output or Path("deppulse-dashboard.html")
        render_html_dashboard(result, graph, output_path=output_path)
        ui.console.print(f"[green]HTML dashboard written to {output_path}[/green]")
    elif args.format == "mermaid":
        output = render_mermaid_graph(graph, title="Dependency Graph")
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]Mermaid diagram written to {args.output}[/green]")
        else:
            print(output)
    elif args.format == "dot":
        output = render_dot_graph(graph, title="Dependency Graph")
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            ui.console.print(f"[green]DOT graph written to {args.output}[/green]")
        else:
            print(output)

    return 0
