"""snapshot command."""

from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.ui import render as ui

COMMAND_NAME = "snapshot"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Manage dependency graph snapshots for trend monitoring",
    )
    sub = parser.add_subparsers(dest="snapshot_cmd", required=True)

    save_parser = sub.add_parser("save", help="Save a new snapshot")
    save_parser.add_argument("path", type=Path, default=".", help="Project root path")
    save_parser.add_argument(
        "--tag", type=str, default=None,
        help="Optional tag for this snapshot (e.g. v0.2.0)",
    )

    diff_parser = sub.add_parser("diff", help="Compare two snapshots")
    diff_parser.add_argument("path", type=Path, default=".", help="Project root path")
    diff_parser.add_argument("--from", dest="from_tag", type=str, required=True, help="Older snapshot tag")
    diff_parser.add_argument("--to", dest="to_tag", type=str, required=True, help="Newer snapshot tag")
    diff_parser.add_argument("--json", action="store_true", help="Output as JSON")

    list_parser = sub.add_parser("list", help="List all saved snapshots")
    list_parser.add_argument("path", type=Path, default=".", help="Project root path")

    check_parser = sub.add_parser("check", help="Check trends since a snapshot (CI mode)")
    check_parser.add_argument("path", type=Path, default=".", help="Project root path")
    check_parser.add_argument(
        "--since-tag", dest="since_tag", type=str, required=True,
        help="Compare against this snapshot tag",
    )
    check_parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cli.commands.helpers import run_scan
    from deppulse.core.snapshot import SnapshotManager

    project_path = args.path.resolve()
    manager = SnapshotManager(project_path)

    if args.snapshot_cmd == "save":
        config = DepPulseConfig.from_path(project_path)
        result, graph, _elapsed = run_scan(project_path, config, use_cache=args.use_cache)
        meta = manager.save(result, tag=args.tag)
        ui.render_snapshot_meta(meta)
        ui.console.print(f"[green]Snapshot saved: {meta.tag}[/green]")
        return 0

    elif args.snapshot_cmd == "list":
        snapshots = manager.list_snapshots()
        ui.render_snapshot_list(snapshots)
        return 0

    elif args.snapshot_cmd == "diff":
        diff = manager.diff(args.from_tag, args.to_tag)
        if args.json:
            data = {
                "older": {"tag": diff.older.tag, "commit_hash": diff.older.commit_hash,
                          "total_files": diff.older.total_files, "total_edges": diff.older.total_edges},
                "newer": {"tag": diff.newer.tag, "commit_hash": diff.newer.commit_hash,
                          "total_files": diff.newer.total_files, "total_edges": diff.newer.total_edges},
                "edges_delta": diff.total_edges_delta,
                "files_delta": diff.files_delta,
                "new_cycles": [{"nodes": c.nodes, "length": c.length} for c in diff.new_cycles_added],
                "alerts": diff.alerts,
            }
            ui.render_json_output(data)
        else:
            ui.render_snapshot_diff(diff)
        return 0

    elif args.snapshot_cmd == "check":
        from deppulse.ui import render as _ui
        if args.ci:
            _ui.set_ci_mode(True)
        config = DepPulseConfig.from_path(project_path)
        result, graph, _elapsed = run_scan(project_path, config, use_cache=args.use_cache)
        diff, alerts = manager.check_trends(args.since_tag)
        ui.render_snapshot_diff(diff)
        ui.render_trend_alerts(alerts)
        if alerts:
            for alert in alerts:
                if alert.severity.upper() == "CRITICAL":
                    return 1
        return 0

    ui.console.print(f"[red]Unknown snapshot subcommand: {args.snapshot_cmd}[/red]")
    return 1
