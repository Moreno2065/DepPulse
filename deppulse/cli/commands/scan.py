"""scan command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.git import is_git_repo
from deppulse.ui import render as ui

COMMAND_NAME = "scan"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Scan a project and build the dependency graph",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--show-table", action="store_true", help="Show dependency table")
    parser.add_argument("--show-unresolved", action="store_true", help="Show unresolved deps")
    parser.add_argument(
        "--max-file-size", type=int, default=512, help="Max file size in KB to scan (default: 512)"
    )
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )
    parser.add_argument(
        "--sarif-output", type=Path, help="Write SARIF report to file"
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="Only scan changed files (requires git repo)",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="Scan files changed since a git ref or date",
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan
    from deppulse.reporting import _graph_stats_to_dict, graph_to_sarif, write_sarif_report

    config = DepPulseConfig.from_path(args.path.resolve())
    config.max_file_size_kb = args.max_file_size

    if args.ci:
        ui.set_ci_mode(True)

    files_to_scan: set[str] | None = None
    if args.incremental or args.since:
        project_path = args.path.resolve()
        if not is_git_repo(project_path):
            ui.console.print("[yellow]Not a git repository.[/yellow]")
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
            changed_files = []

        if changed_files:
            ui.console.print(f"[dim]Incremental scan: {len(changed_files)} changed file(s)[/dim]")
            files_to_scan = set(changed_files)
        else:
            ui.console.print("[dim]No changed files found.[/dim]")

    result, graph, elapsed = run_scan(args.path, config, use_cache=args.use_cache, files_to_scan=files_to_scan)

    if args.sarif_output:
        sarif_dict = graph_to_sarif(result)
        write_sarif_report(sarif_dict, args.sarif_output)
        ui.console.print(f"[green]SARIF report written to {args.sarif_output}[/green]")

    if args.json:
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
