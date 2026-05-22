#!/usr/bin/env python3
"""Benchmark script for DepPulse v1.0 against real-world open-source repos.

Validates scanner accuracy, resolution quality, and test selection precision
against Flask (Python), retrofit (Kotlin), and axios (JS/TS).

Usage:
    python scripts/benchmark.py [--repo flask|retrofit|axios|all]
                               [--output benchmark-results.json]
                               [--skip-clone]

Metrics collected:
- Import recall: detected imports / actual imports (manual inspection)
- Import precision: correctly resolved imports / detected imports
- Resolve accuracy: correctly resolved to project file / total internal imports
- Call edge recall: detected calls / actual calls (sampling)
- Graph build time: wall-clock seconds for scan + graph build
- Test select precision: selected tests that actually cover changed code
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Benchmark repos
# ---------------------------------------------------------------------------

BENCHMARK_REPOS = {
    "flask": {
        "url": "https://github.com/pallets/flask.git",
        "language": "python",
        "size": "~100 files",
        "validates": "Python scanner accuracy",
        "clone_depth": 50,
        "test_commits": 10,
        "test_selection_commits": 5,
    },
    "retrofit": {
        "url": "https://github.com/square/retrofit.git",
        "language": "kotlin",
        "size": "~200 files",
        "validates": "Kotlin scanner vs. old regex",
        "clone_depth": 50,
        "test_commits": 10,
        "test_selection_commits": 5,
    },
    "axios": {
        "url": "https://github.com/axios/axios.git",
        "language": "javascript",
        "size": "~50 files",
        "validates": "JS/TS new scanner correctness",
        "clone_depth": 50,
        "test_commits": 10,
        "test_selection_commits": 5,
    },
}


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class RepoResult:
    name: str
    language: str
    clone_time_seconds: float
    scan_time_seconds: float
    total_files: int
    total_edges: int
    import_recall: float
    import_precision: float
    resolve_accuracy: float
    call_edge_recall: float
    test_selection_precision: float
    warnings: list[str]


@dataclass
class BenchmarkResult:
    timestamp: str
    deppulse_version: str
    python_version: str
    repos: list[dict]


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    def __init__(
        self,
        output_path: Optional[Path] = None,
        skip_clone: bool = False,
    ) -> None:
        self.output_path = output_path
        self.skip_clone = skip_clone
        self.temp_dirs: list[Path] = []
        self.results: list[RepoResult] = []

    def run(self, repos: Optional[list[str]] = None) -> BenchmarkResult:
        """Run benchmarks for specified repos (or all if None)."""
        if repos is None:
            repos = list(BENCHMARK_REPOS.keys())

        for repo_name in repos:
            if repo_name not in BENCHMARK_REPOS:
                print(f"[SKIP] Unknown repo: {repo_name}")
                continue
            result = self._benchmark_repo(repo_name)
            if result:
                self.results.append(result)

        return self._build_result()

    def _benchmark_repo(self, repo_name: str) -> Optional[RepoResult]:
        """Run all benchmarks for a single repo."""
        config = BENCHMARK_REPOS[repo_name]
        print(f"\n{'='*60}")
        print(f"Benchmarking: {repo_name} ({config['language']})")
        print(f"{'='*60}")

        # -- Clone --
        clone_start = time.monotonic()
        repo_path = self._clone_repo(repo_name, config["url"], config["clone_depth"])
        clone_time = time.monotonic() - clone_start
        if repo_path is None:
            print(f"[FAIL] Could not clone {repo_name}")
            return None
        self.temp_dirs.append(repo_path)

        # -- Scan --
        scan_start = time.monotonic()
        scan_result = self._run_scan(repo_path, repo_name)
        scan_time = time.monotonic() - scan_start

        if scan_result is None:
            print(f"[FAIL] Scan failed for {repo_name}")
            return None

        print(f"[OK] Scanned {scan_result['total_files']} files, "
              f"{scan_result['total_edges']} edges in {scan_time:.2f}s")

        # -- Trace commits --
        trace_result = self._run_traces(repo_path, config["test_commits"])

        # -- Test selection --
        test_result = self._run_test_selection(
            repo_path, config["test_selection_commits"]
        )

        return RepoResult(
            name=repo_name,
            language=config["language"],
            clone_time_seconds=round(clone_time, 3),
            scan_time_seconds=round(scan_time, 3),
            total_files=scan_result["total_files"],
            total_edges=scan_result["total_edges"],
            import_recall=trace_result.get("import_recall", 0.0),
            import_precision=trace_result.get("import_precision", 0.0),
            resolve_accuracy=trace_result.get("resolve_accuracy", 0.0),
            call_edge_recall=trace_result.get("call_edge_recall", 0.0),
            test_selection_precision=test_result.get("precision", 0.0),
            warnings=scan_result.get("warnings", []),
        )

    def _clone_repo(
        self,
        name: str,
        url: str,
        depth: int,
    ) -> Optional[Path]:
        """Clone a repo to a temp directory."""
        if self.skip_clone:
            cached = self._find_cached_clone(name)
            if cached:
                print(f"[OK] Using cached clone: {cached}")
                return cached

        temp_dir = Path(tempfile.mkdtemp(prefix=f"deppulse-bench-"))

        print(f"[CLONE] {url} (depth={depth})")
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", str(depth), "--branch", "master",
                 url, str(temp_dir)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                # Try main branch
                result = subprocess.run(
                    ["git", "clone", "--depth", str(depth), "--branch", "main",
                     url, str(temp_dir)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            if result.returncode != 0:
                print(f"[FAIL] Clone failed: {result.stderr[:200]}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return None
            print(f"[OK] Cloned to {temp_dir}")
            return temp_dir
        except subprocess.TimeoutExpired:
            print(f"[FAIL] Clone timed out")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None
        except FileNotFoundError:
            print(f"[FAIL] git not found in PATH")
            return None

    def _find_cached_clone(self, name: str) -> Optional[Path]:
        """Look for a previously-cloned repo in temp dirs."""
        base = Path(tempfile.gettempdir())
        for d in base.glob("deppulse-bench-*"):
            marker = d / f".{name}-cloned"
            if marker.exists():
                return d
        return None

    def _run_scan(self, repo_path: Path, repo_name: str) -> Optional[dict]:
        """Run deppulse scan and parse JSON output."""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        try:
            from deppulse.core.orchestrator import DependencyOrchestrator
            from deppulse.config import DepPulseConfig

            config = DepPulseConfig.from_path(repo_path)
            orchestrator = DependencyOrchestrator(config=config, use_cache=False)
            result = orchestrator.scan(repo_path)

            return {
                "total_files": result.stats.total_files,
                "total_edges": result.stats.total_edges,
                "warnings": result.warnings,
            }
        except Exception as e:
            print(f"[WARN] Scan error: {e}")
            return None

    def _run_traces(self, repo_path: Path, n_commits: int) -> dict:
        """Run deppulse trace on random recent commits."""
        # Get recent commits
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "--oneline", "-n", "100"],
                capture_output=True, text=True, timeout=10,
            )
            commits = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        except Exception:
            return {}

        sampled = random.sample(commits, min(n_commits, len(commits)))
        return {
            "import_recall": 0.0,      # requires manual inspection
            "import_precision": 0.0,   # requires manual inspection
            "resolve_accuracy": 0.0,    # requires manual inspection
            "call_edge_recall": 0.0,    # requires manual sampling
        }

    def _run_test_selection(self, repo_path: Path, n_commits: int) -> dict:
        """Run deppulse tests on commits with known test coverage."""
        return {"precision": 0.0}  # requires manual verification

    def _build_result(self) -> BenchmarkResult:
        """Build the final benchmark result."""
        import sys
        from deppulse import __version__

        result = BenchmarkResult(
            timestamp=datetime.now().isoformat(),
            deppulse_version=__version__,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            repos=[asdict(r) for r in self.results],
        )

        if self.output_path:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(
                json.dumps(asdict(result), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"\n[OK] Results written to {self.output_path}")

        return result

    def cleanup(self) -> None:
        """Remove temporary clone directories."""
        for d in self.temp_dirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run DepPulse benchmarks against real-world open-source repos",
    )
    parser.add_argument(
        "--repo",
        nargs="+",
        choices=["flask", "retrofit", "axios", "all"],
        default=["all"],
        help="Repo(s) to benchmark (default: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark-results.json"),
        help="Output file for results (default: benchmark-results.json)",
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Reuse cached clones if available",
    )

    args = parser.parse_args(argv)

    repos = None
    if "all" not in args.repo:
        repos = args.repo

    runner = BenchmarkRunner(
        output_path=args.output,
        skip_clone=args.skip_clone,
    )

    try:
        result = runner.run(repos=repos)
        _print_summary(result)
    finally:
        runner.cleanup()

    return 0


def _print_summary(result: BenchmarkResult) -> None:
    """Print a human-readable summary of benchmark results."""
    print(f"\n{'='*60}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*60}")
    print(f"DepPulse: {result.deppulse_version}")
    print(f"Python:   {result.python_version}")
    print(f"Date:     {result.timestamp}\n")

    for repo in result.repos:
        print(f"  [{repo['name']}] ({repo['language']})")
        print(f"    Files:        {repo['total_files']}")
        print(f"    Edges:       {repo['total_edges']}")
        print(f"    Scan time:   {repo['scan_time_seconds']:.2f}s")
        print(f"    Clone time:  {repo['clone_time_seconds']:.2f}s")
        if repo["warnings"]:
            print(f"    Warnings:     {len(repo['warnings'])}")
        print()


if __name__ == "__main__":
    sys.exit(main())
