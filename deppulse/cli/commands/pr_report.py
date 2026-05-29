"""pr_report command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.pr_reporter import PRReporter
from deppulse.git import get_changed_files, is_git_repo
from deppulse.ui import render as ui

COMMAND_NAME = "pr-report"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Generate a PR impact report for code review",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument(
        "--base", type=str, default="main",
        help="Base branch to compare against (default: main)",
    )
    parser.add_argument(
        "--format",
        choices=["github-comment", "markdown", "json"],
        default="github-comment",
        help="Output format (default: github-comment)",
    )
    parser.add_argument(
        "--fail-on-high-risk", action="store_true",
        help="Exit with code 1 if risk level is HIGH",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write output to file instead of stdout",
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

    config = DepPulseConfig.from_path(project_path)

    changed_files = get_changed_files(project_path, ref=args.base)

    if not changed_files:
        ui.console.print("[green]No changed files detected against base branch.[/green]")
        return 0

    result, graph, _elapsed = run_scan(project_path, config, use_cache=args.use_cache)

    reporter = PRReporter(graph, config=config)
    report = reporter.generate(changed_files, base_ref=args.base)

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

    if args.fail_on_high_risk and report.risk_level.value == "HIGH":
        ui.console.print("[red]High risk change detected. Exiting with error.[/red]")
        return 1

    return 0
