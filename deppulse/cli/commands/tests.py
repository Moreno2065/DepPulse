"""tests command."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.core.test_selector import TestSelector
from deppulse.git import get_changed_files, is_git_repo
from deppulse.ui import render as ui

COMMAND_NAME = "tests"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Select affected tests based on changed files",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument(
        "--since", type=str, default=None,
        help="Compare against a git ref (e.g. main, HEAD~5)",
    )
    parser.add_argument(
        "--files", type=str, nargs="+",
        help="Explicitly specify changed file paths",
    )
    parser.add_argument(
        "--format",
        choices=["list", "args", "json"],
        default="list",
        help="Output format (default: list)",
    )
    parser.add_argument(
        "--max-blast", type=int, default=50,
        help="Max number of selected tests before fallback to all (default: 50)",
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan

    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    changed_files: list[str]
    diff_output: str = ""
    if args.files:
        changed_files = [str(f).replace("\\", "/") for f in args.files]
    elif args.since:
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository. Use --files to specify files directly.[/yellow]")
            return 1
        changed_files = get_changed_files(project_path, ref=args.since)
        diff_output = _get_git_diff(project_path, ref=args.since)
    else:
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository. Use --files to specify files directly.[/yellow]")
            return 1
        changed_files = get_changed_files(project_path)
        diff_output = _get_git_diff(project_path)

    if not changed_files:
        ui.console.print("[green]No changed files detected.[/green]")
        return 0

    config = DepPulseConfig.from_path(project_path)
    result, graph, _elapsed = run_scan(project_path, config, use_cache=args.use_cache, debug=args.debug)

    selector = TestSelector(graph, config=config)
    test_result = selector.select_tests(
        changed_files,
        max_blast=args.max_blast,
        diff_output=diff_output,
        project_root=project_path,
    )

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
    else:
        ui.render_test_selection(test_result)

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
