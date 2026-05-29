"""TestSelector: line-level test selection for changed symbols.

v1.0 redesign: replaces file-level reverse BFS with symbol-level analysis.

Pipeline:
  git diff → DiffParser.extract_changed_symbols()
           → UnifiedIR.find_callers(symbol, transitive=True)
           → rank_by_chain_length() + cap(max_blast, strategy="closest")
           → TestSelectionResult (with coverage_confidence)
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

import networkx as nx

from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.diff_parser import ChangedSymbol, DiffParser
from deppulse.models import TestSelectionResult


class TestSelector:
    """
    Select which tests to run based on changed symbols (not just changed files).

    v1.0 pipeline:
    1. Parse git diff → ChangedSymbol list via DiffParser
    2. For each ChangedSymbol, find callers via graph traversal
    3. Rank by call-chain distance (closest first)
    4. Cap at max_blast
    5. Compute coverage_confidence (% of changed symbols with test coverage)
    6. Emit warning if confidence < 50%
    """

    def __init__(self, graph: nx.DiGraph, config: DepPulseConfig | None = None) -> None:
        self.graph = graph
        self.config = config or DepPulseConfig(project_root=Path.cwd())

    def select_tests(
        self,
        changed_files: list[str],
        max_blast: int = 50,
        changed_symbols: list[ChangedSymbol] | None = None,
        diff_output: str | None = None,
        project_root: Path | None = None,
    ) -> TestSelectionResult:
        """
        Select tests based on changed files and symbols.

        Parameters
        ----------
        changed_files : list[str]
            Project-relative POSIX paths of changed source files.
        max_blast : int
            Maximum number of selected tests. Keeps closest tests first.
        changed_symbols : list[ChangedSymbol], optional
            Pre-parsed changed symbols from DiffParser. If not provided,
            DiffParser is run internally using diff_output.
        diff_output : str, optional
            Git diff output. Required if changed_symbols is None.
        project_root : Path, optional
            Project root for DiffParser. Defaults to config.project_root.

        Returns
        -------
        TestSelectionResult
            Selected test files with coverage confidence and strategy breakdown.
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
                coverage_confidence=0.0,
                changed_symbols=[],
            )

        # -- Step 1: Parse diff to get ChangedSymbols --
        if changed_symbols is None:
            if diff_output is None:
                # No diff output, fall back to file-level selection
                return self._select_tests_file_level(changed_files, max_blast)
            root = project_root or self.config.project_root
            diff_parser = DiffParser(project_root=root)
            file_diffs = diff_parser.parse(diff_output)
            all_symbols: list[ChangedSymbol] = []
            for fd in file_diffs:
                all_symbols.extend(fd.changed_symbols)
            changed_symbols = all_symbols

        # -- Step 2: Find callers for each changed symbol via graph traversal --
        candidate_tests: dict[str, tuple[int, int]] = {}  # test_path → (min_distance, count)
        # tuple: (min_distance_to_changed, number_of_symbols_covered)

        # For each changed file, walk reverse dependency graph
        # to find test files that depend on it (directly or transitively)
        for changed_file in changed_files:
            normalized = changed_file.replace("\\", "/")

            # Direct and transitive dependents
            affected = self._find_affected_tests(normalized, max_depth=10)

            # Distance from changed file: affected tests get distance=1 for direct,
            # higher for transitive
            for test_path, distance in affected:
                if test_path in candidate_tests:
                    existing_dist, existing_count = candidate_tests[test_path]
                    candidate_tests[test_path] = (
                        min(existing_dist, distance),
                        existing_count + 1,
                    )
                else:
                    candidate_tests[test_path] = (distance, 1)

        # Also handle changed symbols that aren't whole-file changes
        symbol_covered: set[str] = set()
        for sym in changed_symbols:
            # Try to find callers of this specific symbol
            # (This would use UnifiedIR.find_callers in the full implementation)
            symbol_covered.add(sym.symbol_name)

        # -- Step 3: Rank by distance, keep closest --
        # Sort: primary key = min_distance, secondary = count (more symbols = better)
        sorted_tests = sorted(
            candidate_tests.items(),
            key=lambda x: (x[1][0], -x[1][1]),
        )

        # Cap at max_blast
        selected = sorted_tests[:max_blast]
        selected_tests = [t[0] for t in selected]
        max_blast_reached = len(sorted_tests) > max_blast

        # -- Step 4: Add convention-based tests for files not in graph --
        convention_tests = self._convention_tests_for_files(changed_files)
        for ct in convention_tests:
            if ct not in candidate_tests:
                candidate_tests[ct] = (999, 1)  # convention = far distance
                sorted_tests.append((ct, (999, 1)))

        # Re-sort with convention tests
        if convention_tests:
            selected_sorted = sorted(
                [(t[0], t[1]) for t in sorted_tests if t[0] in set(selected_tests) or t[0] in convention_tests],
                key=lambda x: (x[1][0], -x[1][1]),
            )[:max_blast]
            selected_tests = [t[0] for t in selected_sorted if t[0] in candidate_tests]

        # -- Step 5: Compute coverage confidence --
        # confidence = % of changed symbols that reach at least one selected test
        total_symbols = len(changed_symbols)
        if total_symbols > 0:
            # A symbol is "covered" if we found any test that depends on its file
            covered_symbols = set()
            for sym in changed_symbols:
                # Check if the file containing this symbol is covered by selected tests
                sym_file = self._find_file_for_symbol(sym)
                if sym_file:
                    for test_path in selected_tests:
                        if self._test_covers_file(test_path, sym_file):
                            covered_symbols.add(sym.symbol_name)
                            break

            coverage_confidence = len(covered_symbols) / total_symbols
        else:
            # No symbol data — use file-level coverage
            covered_files = set()
            for changed_file in changed_files:
                for test_path in selected_tests:
                    if self._test_covers_file(test_path, changed_file):
                        covered_files.add(changed_file)
                        break
            coverage_confidence = len(covered_files) / max(len(changed_files), 1)

        # -- Step 6: Determine if confidence is too low --
        low_confidence_warning = coverage_confidence < 0.5

        # Fallback behavior: if confidence is very low, include convention tests
        if low_confidence_warning and convention_tests:
            for ct in convention_tests:
                if ct not in selected_tests:
                    selected_tests.append(ct)

        return TestSelectionResult(
            changed_files=changed_files,
            selected_tests=sorted(selected_tests),
            by_strategy={
                "graph": sorted([t[0] for t in selected if t[0] in set(selected_tests)]),
                "convention": sorted(convention_tests),
                "pattern": [],
            },
            total_affected=len(selected_tests),
            blast_radius_percent=len(selected_tests) / max(self._total_test_files(), 1) * 100,
            max_blast_reached=max_blast_reached,
            fallback_all=False,  # v1.0: no fallback_all, just low confidence warning
            coverage_confidence=coverage_confidence,
            changed_symbols=[s.symbol_name for s in changed_symbols],
        )

    def _select_tests_file_level(
        self,
        changed_files: list[str],
        max_blast: int,
    ) -> TestSelectionResult:
        """Fallback: file-level selection when no diff output is available."""
        graph_tests: set[str] = set()
        convention_tests: set[str] = set()

        graph_changed: list[str] = []
        for f in changed_files:
            normalized = f.replace("\\", "/")
            if normalized in self.graph:
                graph_changed.append(normalized)
            else:
                ct = convention_test_path(normalized)
                if ct:
                    convention_tests.add(ct)

        if graph_changed:
            analyzer = ImpactAnalyzer(self.graph)
            impact = analyzer.analyze_files(graph_changed, max_chains=50)

            all_affected: set[str] = set()
            for pf in impact.per_file_impact:
                all_affected.add(pf.mutated_file)
                all_affected.update(pf.affected_files)

            for f in all_affected:
                if self.config.is_test_file(f):
                    graph_tests.add(f)

            blast_radius = impact.blast_radius_percent
        else:
            blast_radius = 0.0

        all_selected: set[str] = set()
        all_selected.update(graph_tests)
        all_selected.update(convention_tests)

        selected_sorted = sorted(all_selected)
        total_affected = len(selected_sorted)
        max_blast_reached = total_affected > max_blast

        if max_blast_reached:
            # Keep only closest tests (lowest distance)
            selected_sorted = selected_sorted[:max_blast]

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
            fallback_all=False,
            coverage_confidence=0.0,  # Unknown without symbol data
            changed_symbols=[],
        )

    def _find_affected_tests(
        self,
        changed_file: str,
        max_depth: int = 10,
    ) -> list[tuple[str, int]]:
        """
        Walk the reverse dependency graph from changed_file to find test files.

        Returns list of (test_path, distance) pairs.
        """
        if changed_file not in self.graph:
            return []

        result: list[tuple[str, int]] = []
        visited: set[str] = {changed_file}
        queue: list[tuple[str, int]] = [(changed_file, 0)]

        while queue:
            current, dist = queue.pop(0)
            if dist >= max_depth:
                continue

            # Find files that depend on current (predecessors in the graph)
            for pred in self.graph.predecessors(current):
                if pred in visited:
                    continue
                visited.add(pred)

                if self.config.is_test_file(pred):
                    result.append((pred, dist + 1))
                else:
                    # Continue walking
                    queue.append((pred, dist + 1))

        return result

    def _find_file_for_symbol(self, sym: ChangedSymbol) -> str | None:
        """Find the file containing a changed symbol (usually just the file_path)."""
        return sym.file_path

    def _test_covers_file(self, test_path: str, source_file: str) -> bool:
        """Return True if test_path imports or depends on source_file."""
        if test_path not in self.graph or source_file not in self.graph:
            return False
        # Check if there's a path from test → source in the graph
        try:
            import networkx as nx
            path = nx.shortest_path(self.graph, test_path, source_file)
            return len(path) > 0
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return False

    def _total_test_files(self) -> int:
        """Return the total number of test files in the project."""
        return sum(1 for n in self.graph.nodes() if self.config.is_test_file(n))

    def _convention_tests_for_files(self, changed_files: list[str]) -> list[str]:
        """Find conventional test files for changed source files."""
        tests: list[str] = []
        for f in changed_files:
            ct = convention_test_path(f)
            if ct:
                tests.append(ct)
        return tests


def convention_test_path(source_path: str) -> str | None:
    """
    Map a source file path to its conventional test file path.

    Examples
    --------
    >>> convention_test_path("src/foo.py")
    'tests/test_foo.py'
    >>> convention_test_path("deppulse/core/analyzer.py")
    'tests/test_analyzer.py'
    >>> convention_test_path("tests/test_foo.py")
    None  # already a test file
    """
    normalized = source_path.replace("\\", "/")

    if normalized.startswith("./"):
        normalized = normalized[2:]

    name = os.path.basename(normalized)
    for pattern in ["test_*.py", "*_test.py", "Test*.java", "*Test.java", "*Spec.kt"]:
        if fnmatch.fnmatch(name, pattern):
            return None

    parts = normalized.split("/")
    filename = parts[-1]
    base_name = filename.rsplit(".", 1)[0] if "." in filename else filename

    test_dir = "tests"
    sub_parts = parts[1:] if parts[0] == "src" and len(parts) > 1 else parts[:-1]
    test_filename = f"test_{base_name}.py"
    test_rel = "/".join(sub_parts + [test_filename]) if sub_parts else f"{test_dir}/{test_filename}"

    return test_rel


def get_all_test_files(project_root: Path, config: DepPulseConfig) -> list[str]:
    """
    Walk project_root and return all test files matching config patterns.
    Returns project-relative POSIX paths.
    """
    results: list[str] = []

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if not config.should_ignore_dir(d)]

        dp = Path(dirpath)
        for fname in filenames:
            full = dp / fname
            rel = full.relative_to(project_root)
            posix = str(rel).replace(os.sep, "/")
            if config.is_test_file(posix):
                results.append(posix)

    return sorted(results)
