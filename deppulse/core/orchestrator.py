"""DependencyOrchestrator: walks a project tree, dispatches to scanners, builds the dependency graph."""

from __future__ import annotations

import fnmatch
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import networkx as nx

from deppulse.cache import ScanCache
from deppulse.config import DepPulseConfig
from deppulse.models import (
    EdgeMetadata,
    GraphBuildResult,
    GraphStats,
    Language,
    NodeMetadata,
    ResolvedDependency,
    ScanResult,
)
from deppulse.scanners.base import BaseScanner
from deppulse.scanners.cpp_scanner import CppScanner
from deppulse.scanners.java_scanner import JavaScanner
from deppulse.scanners.kotlin_scanner import KotlinScanner
from deppulse.scanners.python_scanner import PythonScanner


# ---------------------------------------------------------------------------
# Supported scanner registry
# ---------------------------------------------------------------------------

_SCANNER_REGISTRY: list[BaseScanner] = [
    PythonScanner(),
    JavaScanner(),
    KotlinScanner(),
    CppScanner(),
]


def _get_scanner(path: Path) -> Optional[BaseScanner]:
    """Return the first scanner that can handle this file."""
    for scanner in _SCANNER_REGISTRY:
        if scanner.can_scan(path):
            return scanner
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class DependencyOrchestrator:
    """
    Main orchestration class for scanning a project and building its dependency graph.

    Responsibilities:
    - Walk the project tree, respecting ignore rules
    - Dispatch files to the appropriate language scanner (Strategy Pattern)
    - Resolve internal cross-references between project files
    - Build a networkx.DiGraph with typed node/edge metadata
    - Return a GraphBuildResult with stats and scan results
    """

    def __init__(
        self,
        config: Optional[DepPulseConfig] = None,
        use_cache: bool = True,
    ) -> None:
        self.config = config or DepPulseConfig(project_root=Path.cwd())
        self.use_cache = use_cache
        self._cache: Optional[ScanCache] = None
        self._file_index: dict[str, Path] = {}  # normalized POSIX path -> absolute Path
        self._warnings: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        project_root: Optional[Path] = None,
    ) -> GraphBuildResult:
        """
        Scan the project at `project_root` and build the dependency graph.

        Parameters
        ----------
        project_root : Path, optional
            Root directory to scan. Defaults to the config's project_root.

        Returns
        -------
        GraphBuildResult
            Contains the networkx.DiGraph, scan results, and statistics.
        """
        start_time = time.monotonic()
        root = (project_root or self.config.project_root).resolve()
        self.config.project_root = root

        # Load cache
        if self.use_cache:
            self._cache = ScanCache.load(self.config.cache_dir)

        # Phase 1: walk the tree and build file index
        all_files, skipped = self._walk_project(root)

        # Build the file index (POSIX path -> absolute Path)
        self._file_index = {}
        for f in all_files:
            rel = self._rel_posix(f, root)
            self._file_index[rel] = f

        # Phase 2: scan each file
        scan_results: list[ScanResult] = []
        errors = 0
        for f in all_files:
            result = self._scan_file(f, root)
            scan_results.append(result)
            if result.error:
                errors += 1

        # Phase 3: build networkx graph
        graph, files_with_cycles = self._build_graph(scan_results, root)

        # Save cache
        if self._cache is not None:
            self._cache.save()

        elapsed = time.monotonic() - start_time

        # Build statistics
        stats = self._compute_stats(graph, scan_results, files_with_cycles)

        return GraphBuildResult(
            project_root=str(root),
            scanned_at=datetime.now(),
            scan_results=scan_results,
            total_files_found=len(all_files) + skipped,
            files_skipped=skipped,
            files_with_errors=errors,
            stats=stats,
            warnings=self._warnings,
        )

    # ------------------------------------------------------------------
    # File tree walking
    # ------------------------------------------------------------------

    def _walk_project(self, root: Path) -> tuple[list[Path], int]:
        """Walk the project tree and return (scannable_files, skipped_count)."""
        scannable: list[Path] = []
        skipped = 0

        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dp = Path(dirpath)

            # Prune ignored directories in-place
            dirnames[:] = [
                d
                for d in dirnames
                if not self.config.should_ignore_dir(d) and not self._is_ignored_dir(dp / d)
            ]

            for fname in filenames:
                full = dp / fname

                if self.config.should_ignore_file(fname):
                    skipped += 1
                    continue

                if self._is_ignored_file(fname):
                    skipped += 1
                    continue

                # Check if any registered scanner can handle this file
                if _get_scanner(full) is None:
                    skipped += 1
                    continue

                # Skip files that are too large
                try:
                    size_kb = full.stat().st_size // 1024
                    if size_kb > self.config.max_file_size_kb:
                        self._warnings.append(f"Skipped (too large, {size_kb}KB): {self._rel_posix(full, root)}")
                        skipped += 1
                        continue
                except OSError:
                    skipped += 1
                    continue

                scannable.append(full)

        return scannable, skipped

    def _is_ignored_dir(self, path: Path) -> bool:
        """Check if path matches any ignore pattern (for full paths, not just names)."""
        name = path.name
        for pattern in self.config.ignore_files:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _is_ignored_file(self, name: str) -> bool:
        """Check if filename matches ignore patterns."""
        for pattern in self.config.ignore_files:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    # ------------------------------------------------------------------
    # File scanning
    # ------------------------------------------------------------------

    def _scan_file(self, file_path: Path, project_root: Path) -> ScanResult:
        """Scan a single file, using cache if available and unchanged."""
        rel_posix = self._rel_posix(file_path, project_root)

        # Check cache
        if self._cache is not None:
            cached = self._cache.get(rel_posix, file_path)
            if cached is not None:
                # Reconstruct ScanResult from cached dict
                return self._result_from_dict(cached, rel_posix, str(file_path))

        # Run the scanner
        scanner = _get_scanner(file_path)
        if scanner is None:
            from deppulse.models import ScanResult, Language

            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.UNKNOWN,
                suffix=file_path.suffix,
                size_bytes=0,
                error="no scanner available",
            )

        result = scanner.scan(file_path, project_root, self._file_index)

        # Cache the result
        if self._cache is not None:
            self._cache.set(rel_posix, file_path, self._result_to_dict(result))

        return result

    # ------------------------------------------------------------------
    # Graph building
    # ------------------------------------------------------------------

    def _build_graph(
        self,
        scan_results: list[ScanResult],
        project_root: Path,
    ) -> tuple[nx.DiGraph, int]:
        """Build a networkx.DiGraph from scan results."""
        G = nx.DiGraph()

        # Add all nodes first
        for result in scan_results:
            if result.error and not result.resolved_dependencies:
                continue  # skip files that completely failed to scan
            node_meta = NodeMetadata(
                path=result.file_path,
                language=result.language,
                suffix=result.suffix,
                size_bytes=result.size_bytes,
                symbol_count=len(result.symbols),
                unresolved_count=len(result.unresolved_dependencies),
                external_count=len(result.external_dependencies),
            )
            G.add_node(result.file_path, **vars(node_meta))

        # Add edges
        files_in_cycles: set[str] = set()
        for result in scan_results:
            if result.file_path not in G:
                continue

            for resolved in result.internal_dependencies:
                if resolved.normalized_path is None:
                    continue
                if resolved.normalized_path not in G:
                    # Create a ghost node for unresolved-but-known internal deps
                    ghost_meta = NodeMetadata(
                        path=resolved.normalized_path,
                        language=Language.UNKNOWN,
                        suffix=Path(resolved.normalized_path).suffix,
                        size_bytes=0,
                        symbol_count=0,
                        unresolved_count=0,
                        external_count=0,
                    )
                    G.add_node(resolved.normalized_path, **vars(ghost_meta))

                edge_meta = EdgeMetadata(
                    raw_text=resolved.raw.raw_text,
                    kind=resolved.raw.kind,
                    line_number=resolved.raw.line_number,
                    resolved_by=getattr(
                        _get_scanner(Path(result.absolute_path)) or _get_scanner(Path(resolved.normalized_path or "")) or _SCANNER_REGISTRY[0],
                        "name",
                        "unknown",
                    ),
                )
                G.add_edge(
                    result.file_path,
                    resolved.normalized_path,
                    **vars(edge_meta),
                )

        return G, len(files_in_cycles)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def _compute_stats(
        self,
        G: nx.DiGraph,
        scan_results: list[ScanResult],
        files_with_cycles: int,
    ) -> GraphStats:
        """Compute summary statistics from the graph and scan results."""
        lang_breakdown: dict[str, int] = {}
        python_count = sum(1 for r in scan_results if r.language == Language.PYTHON)
        java_count = sum(1 for r in scan_results if r.language == Language.JAVA)
        kotlin_count = sum(1 for r in scan_results if r.language == Language.KOTLIN)
        cpp_count = sum(1 for r in scan_results if r.language == Language.CPP)
        unknown_count = sum(1 for r in scan_results if r.language == Language.UNKNOWN)

        lang_breakdown["python"] = python_count
        lang_breakdown["java"] = java_count
        lang_breakdown["kotlin"] = kotlin_count
        lang_breakdown["cpp"] = cpp_count
        lang_breakdown["unknown"] = unknown_count

        # All edges in the graph represent internal dependencies (from internal_dependencies)
        internal_edges = G.number_of_edges()
        external_edges = 0

        return GraphStats(
            total_files=G.number_of_nodes(),
            total_edges=G.number_of_edges(),
            python_files=python_count,
            java_files=java_count,
            kotlin_files=kotlin_count,
            cpp_files=cpp_count,
            unknown_files=unknown_count,
            internal_edges=internal_edges,
            external_edges=external_edges,
            unresolved_edges=0,  # tracked per-node
            total_symbols=sum(len(r.symbols) for r in scan_results),
            language_breakdown=lang_breakdown,
            files_with_cycles=files_with_cycles,
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _rel_posix(file_path: Path, project_root: Path) -> str:
        """Return a project-relative POSIX path string."""
        try:
            rel = file_path.relative_to(project_root)
        except ValueError:
            return str(file_path)
        return str(rel).replace(os.sep, "/")

    @staticmethod
    def _result_to_dict(result: ScanResult) -> dict:
        """Serialize ScanResult to a dict for caching."""
        return {
            "file_path": result.file_path,
            "absolute_path": result.absolute_path,
            "language": result.language.value,
            "suffix": result.suffix,
            "size_bytes": result.size_bytes,
            "symbols": [vars(s) for s in result.symbols],
            "warnings": result.warnings,
            "error": result.error,
            "raw_deps": [
                {"raw_text": d.raw_text, "kind": d.kind.value, "line_number": d.line_number}
                for d in result.raw_dependencies
            ],
            "resolved_deps": [
                {
                    "raw_text": d.raw.raw_text,
                    "kind": d.raw.kind.value,
                    "line_number": d.raw.line_number,
                    "normalized_path": d.normalized_path,
                    "is_external": d.is_external,
                    "is_stdlib": d.is_stdlib,
                    "is_unresolved": d.is_unresolved,
                    "resolution_note": d.resolution_note,
                }
                for d in result.resolved_dependencies
            ],
        }

    @staticmethod
    def _result_from_dict(data: dict, file_path: str, absolute_path: str) -> ScanResult:
        """Reconstruct ScanResult from cached dict."""
        from deppulse.models import (
            DependencyKind,
            ExtractedSymbol,
            Language,
            RawDependency,
            ResolvedDependency,
            ScanResult as SR,
        )

        symbols = [
            ExtractedSymbol(
                symbol_type=s["symbol_type"],
                name=s["name"],
                fully_qualified=s["fully_qualified"],
            )
            for s in data.get("symbols", [])
        ]

        raw_deps = [
            RawDependency(
                raw_text=d["raw_text"],
                kind=DependencyKind(d["kind"]),
                line_number=d["line_number"],
            )
            for d in data.get("raw_deps", [])
        ]

        resolved_deps = [
            ResolvedDependency(
                raw=RawDependency(
                    raw_text=d["raw_text"],
                    kind=DependencyKind(d["kind"]),
                    line_number=d["line_number"],
                ),
                normalized_path=d["normalized_path"],
                is_external=d["is_external"],
                is_stdlib=d["is_stdlib"],
                is_unresolved=d["is_unresolved"],
                resolution_note=d.get("resolution_note", ""),
            )
            for d in data.get("resolved_deps", [])
        ]

        return SR(
            file_path=file_path,
            absolute_path=absolute_path,
            language=Language(data.get("language", "unknown")),
            suffix=data.get("suffix", ""),
            size_bytes=data.get("size_bytes", 0),
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=data.get("warnings", []),
            error=data.get("error"),
        )
