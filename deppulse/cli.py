"""CLI entry point for DepPulse using argparse and rich."""

from __future__ import annotations

import argparse
import sys

from deppulse import __version__
from deppulse.cli.commands import (
    callgraph,
    cycles,
    diff,
    doctor,
    pr_report,
    report,
    scan,
    snapshot,
    tests,
    trace,
    viz,
)

COMMANDS = [
    scan, trace, diff, cycles, report, doctor, callgraph, viz, tests, snapshot, pr_report,
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deppulse",
        description="Local source-code dependency topology and change-impact auditing tool.",
    )
    parser.add_argument("--version", action="version", version=f"deppulse {__version__}")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print full traceback on error",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching and rescan all files",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    for mod in COMMANDS:
        mod.register(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args is None or args.command is None:
        parser.print_help()
        return 0

    handlers = {mod.COMMAND_NAME: mod.handle for mod in COMMANDS}
    handler = handlers.get(args.command)
    if handler is None:
        from deppulse.ui import render as ui
        ui.console.print(f"[red]Unknown command: {args.command}[/red]")
        return 1

    use_cache = not getattr(args, 'no_cache', False)
    debug = getattr(args, 'debug', False)
    args.use_cache = use_cache

    try:
        return handler(args)
    except KeyboardInterrupt:
        from deppulse.ui import render as ui
        ui.console.print("\n[yellow]Interrupted.[/yellow]")
        return 130
    except Exception as e:
        from deppulse.ui import render as ui
        ui.console.print(f"[red]Error: {e}[/red]")
        if debug:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
