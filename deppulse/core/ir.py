"""Unified IR (Intermediate Representation) for DepPulse.

Replaces the dual file-level dependency graph and symbol-level call graph
with a single intermediate representation consumed by all downstream modules.

IR Nodes:
  - FileNode(path, language, symbols[])
  - SymDef(name, fqn, type, file_path, line_range, visibility)

IR Edges:
  - ImportEdge(from_file, to_file, specifier, line, kind)
  - CallEdge(caller_sym, callee_sym, line, is_polymorphic, is_external)

The orchestrator builds this IR from per-scanner ParseResults. The nx.DiGraph
is then derived from the IR for backward compatibility. Callers (CLI, cache,
snapshot) continue to receive GraphBuildResult without changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SymType(str, Enum):
    """Kind of a symbol definition."""

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    PROPERTY = "property"
    CONSTRUCTOR = "constructor"
    INTERFACE = "interface"
    ENUM = "enum"
    ANNOTATION = "annotation"
    TYPE_ALIAS = "type_alias"
    UNKNOWN = "unknown"


class Visibility(str, Enum):
    """Visibility of a symbol."""

    PUBLIC = "public"
    PRIVATE = "private"
    PROTECTED = "protected"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class ImportKind(str, Enum):
    """Kind of an import edge."""

    # Python
    IMPORT = "import"               # import x
    FROM_IMPORT = "from_import"     # from x import y
    RELATIVE_IMPORT = "relative_import"  # from . import y

    # Java
    JAVA_IMPORT = "java_import"
    JAVA_STATIC_IMPORT = "java_static_import"
    JAVA_WILDCARD_IMPORT = "java_wildcard_import"

    # Kotlin
    KOTLIN_IMPORT = "kotlin_import"
    KOTLIN_WILDCARD_IMPORT = "kotlin_wildcard_import"

    # C/C++
    INCLUDE_LOCAL = "include_local"     # #include "local.h"
    INCLUDE_SYSTEM = "include_system"   # #include <system.h>

    # JS/TS
    ESM_IMPORT = "esm_import"           # import { x } from 'y'
    ESM_DEFAULT_IMPORT = "esm_default_import"  # import React from 'react'
    ESM_NAMESPACE_IMPORT = "esm_namespace_import"  # import * as x from 'y'
    CJS_REQUIRE = "cjs_require"        # const x = require('y')
    DYNAMIC_IMPORT = "dynamic_import"   # import('y')

    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Raw data types (produced by scanners before resolution)
# ---------------------------------------------------------------------------


@dataclass
class LineRange:
    """A range of lines in a source file."""

    start: int  # 1-indexed
    end: int    # 1-indexed, inclusive

    def __post_init__(self) -> None:
        if self.start < 1:
            self.start = 1
        if self.end < self.start:
            self.end = self.start

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def overlaps(self, other: LineRange) -> bool:
        """Return True if this range overlaps with another."""
        return self.start <= other.end and other.start <= self.end


@dataclass
class RawImport:
    """A raw import/include directive before path resolution."""

    raw_text: str            # e.g. "import com.example.Utils;"
    specifier: str           # e.g. "com.example.Utils" (module path, no keywords)
    kind: ImportKind
    line: int                # 1-indexed line number
    column: int = 0          # 0-indexed column offset
    is_wildcard: bool = False


@dataclass
class RawCall:
    """A raw call site before symbol resolution."""

    caller_name: str         # e.g. "process" (simple name at call site)
    callee_name: str          # e.g. "helper" (simple name being called)
    line: int                # 1-indexed line number
    column: int = 0


@dataclass
class RawSymbol:
    """A raw symbol definition extracted from a file."""

    name: str
    fqn: str                 # fully-qualified name, e.g. "method:MyClass.process"
    sym_type: SymType
    file_path: str            # project-relative POSIX path
    line_range: LineRange
    visibility: Visibility = Visibility.UNKNOWN


# ---------------------------------------------------------------------------
# IR Nodes
# ---------------------------------------------------------------------------


@dataclass
class FileNode:
    """A source file in the IR."""

    path: str                 # project-relative POSIX path
    language: str             # e.g. "python", "java", "kotlin", "cpp", "javascript", "typescript"
    suffix: str               # e.g. ".py", ".kt", ".hpp"
    symbols: list[RawSymbol] = field(default_factory=list)
    size_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def symbol_names(self) -> set[str]:
        """Return the set of all simple symbol names defined in this file."""
        return {s.name for s in self.symbols}


@dataclass
class SymDef:
    """
    A resolved symbol definition — the node type in the symbol-level graph.
    """

    name: str                 # simple name, e.g. "process"
    fqn: str                  # fully-qualified, e.g. "com.example.utils:process" or "method:Utils.process"
    sym_type: SymType
    file_path: str            # project-relative POSIX path
    line_range: LineRange
    visibility: Visibility = Visibility.UNKNOWN

    # Language this symbol belongs to
    language: str = "unknown"

    # For methods: name of the containing class/type
    owner: Optional[str] = None


# ---------------------------------------------------------------------------
# IR Edges
# ---------------------------------------------------------------------------


@dataclass
class ImportEdge:
    """
    A dependency edge from one file to another via an import/include directive.
    Direction: from_file → to_file (from_file depends on to_file).
    """

    from_file: str            # project-relative POSIX path of the importing file
    to_file: Optional[str]    # project-relative POSIX path of the imported file (None if external/stdlib)
    specifier: str            # e.g. "com.example.Utils" or "utils/helper.h"
    import_kind: ImportKind
    line: int                 # 1-indexed line number in from_file
    is_external: bool = False # True if to_file is None (external/stdlib)
    is_stdlib: bool = False
    is_unresolved: bool = False
    resolution_note: str = ""  # e.g. "no project file found"


@dataclass
class CallEdge:
    """
    A call relationship from one symbol to another.
    Direction: caller → callee (caller calls callee).
    """

    caller: SymDef
    callee: SymDef
    line: int                 # 1-indexed line number of the call site
    is_polymorphic: bool = False  # virtual dispatch (Java/C++ override)
    is_external: bool = False     # cross-module call
    call_site_file: str = ""      # project-relative path where the call occurs


# ---------------------------------------------------------------------------
# Unified IR
# ---------------------------------------------------------------------------


@dataclass
class UnifiedIR:
    """
    The unified intermediate representation for a project scan.

    Contains all file nodes, symbol definitions, import edges, and call edges
    in a single coherent structure. The nx.DiGraph is derived from this IR
    for backward compatibility.
    """

    project_root: str
    scanned_at: datetime
    file_nodes: list[FileNode] = field(default_factory=list)
    sym_defs: list[SymDef] = field(default_factory=list)
    import_edges: list[ImportEdge] = field(default_factory=list)
    call_edges: list[CallEdge] = field(default_factory=list)

    # Derived index structures (built lazily)
    _file_index: dict[str, FileNode] = field(default_factory=dict, init=False, repr=False)
    _sym_index: dict[str, list[SymDef]] = field(default_factory=dict, init=False, repr=False)
    _fqn_index: dict[str, SymDef] = field(default_factory=dict, init=False, repr=False)
    _built: bool = field(default=False, init=False, repr=False)

    def build_indices(self) -> None:
        """Build internal index structures for fast lookup. Call after construction."""
        if self._built:
            return

        for fn in self.file_nodes:
            self._file_index[fn.path] = fn

        for sym in self.sym_defs:
            self._sym_index.setdefault(sym.name, []).append(sym)
            self._fqn_index[sym.fqn] = sym

        self._built = True

    # -- File lookups --

    def get_file(self, path: str) -> Optional[FileNode]:
        """Get a file node by project-relative path."""
        if not self._built:
            self.build_indices()
        return self._file_index.get(path)

    def all_files(self) -> list[str]:
        """Return all file paths in the IR."""
        if not self._built:
            self.build_indices()
        return list(self._file_index.keys())

    # -- Symbol lookups --

    def find_symdefs(self, name: str) -> list[SymDef]:
        """Find all symbol definitions with the given simple name."""
        if not self._built:
            self.build_indices()
        return self._sym_index.get(name, [])

    def find_symdef(self, fqn: str) -> Optional[SymDef]:
        """Find a symbol by its fully-qualified name."""
        if not self._built:
            self.build_indices()
        return self._fqn_index.get(fqn)

    def find_symdefs_in_file(self, file_path: str) -> list[SymDef]:
        """Find all symbol definitions in a specific file."""
        return [s for s in self.sym_defs if s.file_path == file_path]

    def find_callers(
        self,
        callee: SymDef,
        transitive: bool = False,
        max_depth: int = 10,
    ) -> list[tuple[SymDef, int]]:
        """
        Find all callers of a given symbol.

        Parameters
        ----------
        callee : SymDef
            The target symbol to find callers of.
        transitive : bool
            If True, traverse the call graph transitively.
        max_depth : int
            Maximum traversal depth for transitive search.

        Returns
        -------
        list[tuple[SymDef, int]]
            List of (caller, distance) pairs, sorted by distance ascending.
        """
        if not self._built:
            self.build_indices()

        # Build call graph adjacency: callee → callers
        callers_of: dict[str, list[tuple[SymDef, int]]] = {}
        for edge in self.call_edges:
            callers_of.setdefault(edge.callee.fqn, []).append(
                (edge.caller, 1)
            )

        if not transitive:
            return callers_of.get(callee.fqn, [])

        # BFS for transitive callers
        result: list[tuple[SymDef, int]] = []
        visited_fqns: set[str] = {callee.fqn}
        queue: list[tuple[str, int]] = [(callee.fqn, 0)]

        while queue:
            current_fqn, dist = queue.pop(0)
            if dist >= max_depth:
                continue

            for caller, _ in callers_of.get(current_fqn, []):
                if caller.fqn not in visited_fqns:
                    visited_fqns.add(caller.fqn)
                    result.append((caller, dist + 1))
                    queue.append((caller.fqn, dist + 1))

        result.sort(key=lambda x: x[1])
        return result

    # -- Import graph (derive nx.DiGraph from this) --

    def to_dependency_graph(self) -> "nx.DiGraph":
        """
        Derive a networkx DiGraph from the import edges.
        Node: file path. Edge: file → dependency file.
        """
        import networkx as nx

        G = nx.DiGraph()

        for fn in self.file_nodes:
            if fn.error and not self.import_edges:
                continue
            G.add_node(fn.path)

        for edge in self.import_edges:
            if not edge.is_external and edge.to_file:
                if edge.from_file in G:
                    if edge.to_file not in G:
                        G.add_node(edge.to_file)
                    G.add_edge(edge.from_file, edge.to_file)

        return G
