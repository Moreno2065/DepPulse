"""doctor command."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from deppulse.config import DepPulseConfig
from deppulse.git import is_git_repo
from deppulse.ui import render as ui

COMMAND_NAME = "doctor"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        COMMAND_NAME,
        help="Validate environment and project readiness",
    )
    parser.add_argument("path", type=Path, default=".", help="Project root path")
    parser.add_argument(
        "--ci", action="store_true", help="CI mode: reduce output, use GitHub Actions format"
    )


def handle(args: argparse.Namespace) -> int:
    from deppulse.cache import ScanCache
    from deppulse.core.orchestrator import _SCANNER_REGISTRY

    project_path = args.path.resolve()

    if args.ci:
        ui.set_ci_mode(True)

    config = DepPulseConfig.from_path(project_path)

    project_exists = project_path.exists() and project_path.is_dir()
    git_detected = is_git_repo(project_path)

    supported = 0
    try:
        for dirpath, dirnames, filenames in os.walk(project_path):
            dirnames[:] = [d for d in dirnames if not config.should_ignore_dir(d)]
            for fname in filenames:
                if config.should_ignore_file(fname):
                    continue
                if any(s.can_scan(Path(dirpath) / fname) for s in _SCANNER_REGISTRY):
                    supported += 1
    except OSError:
        supported = 0

    cache_dir = config.cache_dir
    cache_exists = cache_dir.exists()
    cache_stats = {"entries": 0, "size_kb": 0}
    if cache_exists:
        try:
            cache = ScanCache.load(cache_dir)
            cache_stats = cache.get_stats()
        except Exception:
            pass

    if cache_exists:
        cache_status = f"Present ({cache_stats['entries']} entries, {cache_stats['size_kb']}KB)"
    else:
        cache_status = "Not present (no cache)"

    config_loaded = (
        "Loaded from deppulse.json" if config._config_file and config._config_file.exists()
        else "Defaults (no deppulse.json)"
    )

    scanner_names = [s.name for s in _SCANNER_REGISTRY]

    ui.render_doctor(
        project_exists=project_exists,
        is_git_repo=git_detected,
        supported_files=supported,
        config_loaded=config_loaded,
        cache_status=cache_status,
        scanner_names=scanner_names,
    )

    return 0
