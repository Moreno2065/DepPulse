"""TestSelector: select affected tests for a given set of changed source files."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

import networkx as nx

from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.models import TestSelectionResult


class TestSelector:
    """
    Select which tests to run based on changed source files.

    Uses a three-layer strategy:
    1. Graph: walk reverse-dependency graph from each changed file, filter test files
    2. Convention mapping: apply filename conventions for changed files not in graph
    3. Pattern scan: walk project tree for test files matching patterns
    """

    def __init__(self, graph: nx.DiGraph, config: DepPulseConfig | None = None) -> None:
        self.graph = graph
        self.config = config or DepPulseConfig(project_root=Path.cwd())

    def select_tests(
        self,
        changed_files: list[str],
        max_blast: int = 50,
    ) -> TestSelectionResult:
        """
        Select which tests to run given changed source files.

        Parameters
        ----------
        changed_files : list[str]
            Project-relative POSIX paths of changed source files.
        max_blast : int
            Maximum number of selected tests before fallback to all tests.

        Returns
        -------
        TestSelectionResult
            Selected test files organized by strategy, with metadata.
        """
        if not changed_files:
            return TestSelectionResult(
                changed_files=[],
                selected_tests=[],
                by_strategy={"graph": [], "convention": [], "pattern": []},
                total_affected=0,
                blast_radius_percent=0.0,
                max_blast_reached=False,
                fallback_all=False,
            )

        graph_tests: set[str] = set()
        convention_tests: set[str] = set()

        # Normalize changed files that are in the graph
        graph_changed: list[str] = []
        for f in changed_files:
            normalized = f.replace("\\", "/")
            if normalized in self.graph:
                graph_changed.append(normalized)
            else:
                # Try convention mapping for files not in graph
                ct = convention_test_path(normalized)
                if ct:
                    convention_tests.add(ct)

        # Strategy 1: Graph-based test selection
        if graph_changed:
            analyzer = ImpactAnalyzer(self.graph)
            impact = analyzer.analyze_files(graph_changed, max_chains=50)

            # Collect ALL affected source files (including the changed ones themselves)
            all_affected: set[str] = set()
            for pf in impact.per_file_impact:
                all_affected.add(pf.mutated_file)
                all_affected.update(pf.affected_files)

            # Add changed files not in graph to affected set
            for f in changed_files:
                all_affected.add(f.replace("\\", "/"))

            # Filter to test files
            for f in all_affected:
                if self.config.is_test_file(f):
                    graph_tests.add(f)

            blast_radius = impact.blast_radius_percent
        else:
            blast_radius = 0.0

        # Merge and deduplicate
        all_selected: set[str] = set()
        all_selected.update(graph_tests)
        all_selected.update(convention_tests)

        # Strategy 3: Pattern scan - find test files that directly import affected files
        # Only do this if graph_tests is non-empty (otherwise convention covered it)
        if graph_tests:
            pattern_tests = self._pattern_scan_tests(graph_changed, all_affected)
            all_selected.update(pattern_tests)

        selected_sorted = sorted(all_selected)
        total_affected = len(selected_sorted)
        max_blast_reached = total_affected > max_blast

        # Fallback: if blast radius too high, return all test files in project
        fallback_all = max_blast_reached
        if fallback_all:
            selected_sorted = _find_all_project_tests(self.config.project_root, self.config)

        return TestSelectionResult(
            changed_files=changed_files,
            selected_tests=selected_sorted,
            by_strategy={
                "graph": sorted(graph_tests),
                "convention": sorted(convention_tests),
                "pattern": [],
            },
            total_affected=total_affected,
            blast_radius_percent=blast_radius,
            max_blast_reached=max_blast_reached,
            fallback_all=fallback_all,
        )

    def _pattern_scan_tests(
        self,
        changed_files: list[str],
        affected_files: set[str],
    ) -> set[str]:
        """
        Find test files that directly import any of the affected source files.
        Uses edge inspection on the dependency graph.
        """
        result: set[str] = set()

        # For each test file in the graph, check if it has an edge to an affected file
        for node in self.graph.nodes():
            if not self.config.is_test_file(node):
                continue
            # Check if this test file depends on any affected source file
            for successor in self.graph.successors(node):
                if successor in affected_files and not self.config.is_test_file(successor):
                    result.add(node)
                    break

        return result


def convention_test_path(source_path: str) -> str | None:
    """
    Map a source file path to its conventional test file path.

    Examples
    --------
    >>> convention_test_path("src/foo.py")
    'tests/test_foo.py'
    >>> convention_test_path("deppulse/core/analyzer.py")
    'tests/test_analyzer.py'
    >>> convention_test_path("src/core/utils.py")
    'tests/test_utils.py'
    >>> convention_test_path("tests/test_foo.py")
    None  # already a test file
    """
    normalized = source_path.replace("\\", "/")

    # Strip leading ./ if present
    if normalized.startswith("./"):
        normalized = normalized[2:]

    # Already a test file
    name = os.path.basename(normalized)
    for pattern in ["test_*.py", "*_test.py", "Test*.java", "*Test.java", "*Spec.kt"]:
        if fnmatch.fnmatch(name, pattern):
            return None

    # Build test path
    parts = normalized.split("/")
    filename = parts[-1]

    # Get the module name (strip extension)
    if "." in filename:
        base_name = filename.rsplit(".", 1)[0]
    else:
        base_name = filename

    # Determine the test directory
    test_dir = "tests"

    # Strip src/ prefix if present (e.g. src/foo.py -> tests/)
    if parts[0] == "src" and len(parts) > 1:
        sub_parts = parts[1:]
    else:
        sub_parts = parts[:-1]

    # Build test filename: test_{basename}.py
    test_filename = f"test_{base_name}.py"

    if sub_parts:
        test_rel = "/".join(sub_parts + [test_filename])
    else:
        test_rel = f"{test_dir}/{test_filename}"

    return test_rel


def get_all_test_files(project_root: Path, config: DepPulseConfig) -> list[str]:
    """
    Walk project_root and return all test files matching config patterns.
    Returns project-relative POSIX paths.
    """
    results: list[str] = []

    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune ignored directories
        dirnames[:] = [d for d in dirnames if not config.should_ignore_dir(d)]

        dp = Path(dirpath)
        for fname in filenames:
            full = dp / fname
            rel = full.relative_to(project_root)
            posix = str(rel).replace(os.sep, "/")
            if config.is_test_file(posix):
                results.append(posix)

    return sorted(results)


def _find_all_project_tests(project_root: Path, config: DepPulseConfig) -> list[str]:
    """Return all test files in the project as fallback."""
    return get_all_test_files(project_root, config)
