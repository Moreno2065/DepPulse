"""C/C++ source code scanner using tree-sitter-cpp.

Rewritten from the regex-based scanner to use the tree-sitter C++ grammar
for accurate, syntax-aware extraction of includes, declarations, and symbols.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import tree_sitter_cpp

from deppulse.core.ir import (
    ImportKind,
    RawImport,
    RawSymbol,
    SymType,
    Visibility,
)
from deppulse.core.path_resolver import PathResolver
from deppulse.core.tree_sitter_parser import TreeSitterParser
from deppulse.models import (
    ConfidenceLevel,
    ConfidenceSource,
    DependencyKind,
    ExtractedSymbol,
    Language,
    RawDependency,
    ResolvedDependency,
    ScanResult,
    normalize_path_to_posix,
)
from deppulse.scanners.base import BaseScanner

if TYPE_CHECKING:
    from tree_sitter import Language as TSLanguage
    from tree_sitter import Tree

# Supported C/C++ file extensions.
CPP_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})

# ---------------------------------------------------------------------------
# Comment stripping utility (used by tests)
# ---------------------------------------------------------------------------

_RE_SINGLELINE_COMMENT = re.compile(r"//.*$", re.MULTILINE)
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(code: str) -> str:
    """Remove // and /* */ comments from C++ source code."""
    code = _RE_SINGLELINE_COMMENT.sub("", code)
    code = _RE_BLOCK_COMMENT.sub("", code)
    return code

# ---------------------------------------------------------------------------
# CppTreeSitterParser
# ---------------------------------------------------------------------------


class CppTreeSitterParser(TreeSitterParser):
    """
    Tree-sitter-based parser for C++ source files.

    Extracts:
    - #include directives (local "foo.h" vs system <foo.h>)
    - Class/struct declarations
    - Function definitions
    - Variable/field declarations
    """

    language_name = "cpp"

    def __init__(self) -> None:
        self._current_source: bytes = b""
        self._language: TSLanguage | None = None

    @property
    def language(self) -> TSLanguage:
        """Return the tree-sitter Language object for C++, wrapping the PyCapsule."""
        if self._language is None:
            from tree_sitter import Language

            capsule = tree_sitter_cpp.language()  # call the builtin method to get PyCapsule
            self._language = Language(capsule)     # wrap the capsule in a Language object
        return self._language

    # ------------------------------------------------------------------------
    # tree-sitter query execution
    # ------------------------------------------------------------------------

    def _run_query(
        self,
        tree: Tree,
        source: bytes,
        pattern: str,
    ) -> list[tuple[int, int, bytes]]:
        """
        Execute a tree-sitter query and return a list of (capture_id, start_byte, end_byte) tuples.

        Each pattern should contain one or more captures like ``(node_type [field_name] @capture_name)``.
        """
        from tree_sitter import Query

        try:
            query = Query(self.language, pattern)
        except Exception:
            return []
        captures: list[tuple[int, int, bytes]] = []
        for _pattern_index, _ in enumerate(query.patterns):
            pass  # validate patterns compile
        for capture_id, node in query.captures(tree.root_node):
            captures.append((capture_id, node.byte_range[0], node.byte_range[1]))
        return captures

    def query(self, tree: Tree, pattern: str) -> list:
        """Override to use actual tree-sitter query execution."""
        return self._run_query(tree, b"", pattern)

    # ------------------------------------------------------------------------
    # Include extraction
    # ------------------------------------------------------------------------

    def extract_imports(
        self,
        tree: Tree,
        file_path: str,
        source: bytes | None = None,
    ) -> list[RawImport]:
        """
        Extract all #include directives from a C++ file using tree-sitter.

        tree-sitter-cpp parses ``#include "foo.h"`` as:
            (preproc_include
              (string_literal ["] @path_content ["] ]))
        and ``#include <foo.h>`` as:
            (preproc_include
              (system_lib_string))

        We inspect children of each preproc_include node directly to determine
        the include style and extract the path.

        Parameters
        ----------
        tree : Tree
            The parsed tree-sitter Tree.
        file_path : str
            Project-relative POSIX path of the source file.
        source : bytes, optional
            Raw source bytes. If not provided, uses self._current_source.
        """
        imports: list[RawImport] = []
        src = source if source is not None else self._current_source

        for node in self._all_nodes_of_type(tree, "preproc_include"):
            kind, specifier, raw_text = self._extract_include(node, src)
            if specifier is None:
                continue
            imports.append(RawImport(
                raw_text=raw_text,
                specifier=specifier,
                kind=kind,
                line=self._node_line(node, src),
                column=self._node_column(node, src),
            ))

        return imports

    def _extract_include(
        self,
        node,
        source: bytes,
    ) -> tuple[ImportKind, str | None, str]:
        """
        Inspect a preproc_include node and return (ImportKind, specifier, raw_text).

        Handles both quoted includes (#include "foo.h") and system includes
        (#include <foo.h>).
        """
        raw_bytes = source[node.byte_range[0]:node.byte_range[1]]
        raw_text = raw_bytes.decode("utf-8", errors="replace").strip()

        is_quoted = False
        path_text: str | None = None

        for child in node.children:
            child_type = child.type
            # Quoted include: string_literal contains the path text
            if child_type == "string_literal":
                inner = source[child.byte_range[0]:child.byte_range[1]]
                decoded = inner.decode("utf-8", errors="replace")
                # Strip leading/trailing quotes
                path_text = decoded.strip().strip('"').strip("'")
                is_quoted = True
            # System include: system_lib_string (tree-sitter-cpp uses this for <foo.h>)
            elif child_type == "system_lib_string":
                path_text = self._node_text(child, source).strip()
                if path_text.startswith("<") and path_text.endswith(">"):
                    path_text = path_text[1:-1]
                is_quoted = False
            # System include: system_type > identifier (older tree-sitter-cpp)
            elif child_type == "system_type":
                for grandchild in child.children:
                    if grandchild.type == "identifier":
                        path_text = self._node_text(grandchild, source).strip()
                        break
            elif child_type == "preproc_arg":
                arg_text = self._node_text(child, source).strip()
                if arg_text.startswith("<") and arg_text.endswith(">"):
                    path_text = arg_text[1:-1].strip()
                    is_quoted = False
                elif arg_text.startswith('"') and arg_text.endswith('"'):
                    path_text = arg_text[1:-1].strip()
                    is_quoted = True

        if path_text is None:
            return (ImportKind.UNKNOWN, None, raw_text)

        kind = ImportKind.INCLUDE_LOCAL if is_quoted else ImportKind.INCLUDE_SYSTEM
        return (kind, path_text, raw_text)

    # ------------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------------

    def extract_symbols(
        self,
        tree: Tree,
        file_path: str,
        source: bytes | None = None,
    ) -> list[RawSymbol]:
        """
        Extract class, struct, and function symbol definitions from a C++ file.

        - ``class_specifier`` → class name
        - ``struct_specifier`` → struct name
        - ``function_definition`` → function name
        - ``declaration`` → top-level variable declarations

        Parameters
        ----------
        tree : Tree
            The parsed tree-sitter Tree.
        file_path : str
            Project-relative POSIX path of the source file.
        source : bytes, optional
            Raw source bytes. If not provided, uses self._current_source.
        """
        src = source if source is not None else self._current_source
        symbols: list[RawSymbol] = []

        # Classes and structs
        for node in self._all_nodes_of_type(tree, "class_specifier"):
            sym = self._extract_class_or_struct(node, src, file_path)
            if sym:
                symbols.append(sym)

        # Function definitions
        for node in self._all_nodes_of_type(tree, "function_definition"):
            sym = self._extract_function(node, src, file_path)
            if sym:
                symbols.append(sym)

        return symbols

    def _extract_class_or_struct(
        self,
        node,
        source: bytes,
        file_path: str,
    ) -> RawSymbol | None:
        """Extract a class or struct symbol from its class_specifier node."""
        name_node = self._find_child_by_type(node, "type_identifier") or \
                    self._find_child_by_type(node, "identifier")
        if not name_node:
            return None

        name = self._node_text(name_node, source)
        if not name or name.startswith("_") and len(name) == 1:
            return None  # skip anonymous / unnamed

        fqn = name
        sym_type = SymType.CLASS

        # Distinguish struct from class via node type
        # class_specifier node covers both; check the keyword text
        kw_text = source[node.byte_range[0]:node.byte_range[0] + 20].decode("utf-8", errors="replace")
        if kw_text.lstrip().startswith("struct"):
            sym_type = SymType.CLASS  # treat struct same as class

        return RawSymbol(
            name=name,
            fqn=fqn,
            sym_type=sym_type,
            file_path=file_path,
            line_range=self._node_range(node, source),
            visibility=Visibility.PUBLIC,
        )

    def _extract_function(
        self,
        node,
        source: bytes,
        file_path: str,
    ) -> RawSymbol | None:
        """Extract a function symbol from its function_definition node."""
        # Look for function_declarator > identifier or field_identifier
        declarator = self._find_child_by_type(node, "function_declarator")
        if not declarator:
            # Some forms have the declarator directly
            declarator = node

        name_node = self._find_child_by_type(declarator, "identifier") or \
                    self._find_child_by_type(declarator, "field_identifier")
        if not name_node:
            return None

        name = self._node_text(name_node, source)
        if not name:
            return None

        # Skip constructors/destructors (no simple name) and operators
        if name.startswith("operator"):
            return None

        return RawSymbol(
            name=name,
            fqn=f"function:{name}",
            sym_type=SymType.FUNCTION,
            file_path=file_path,
            line_range=self._node_range(node, source),
            visibility=Visibility.PUBLIC,
        )

    # ------------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------------

    def _get_source(self, tree: Tree) -> bytes:
        """Return the source bytes from the parser's current source buffer."""
        return self._current_source

    def parse(self, source: bytes) -> Tree:
        """
        Parse source bytes into a tree-sitter Tree.

        Uses the new tree-sitter v0.25 API where Parser is constructed
        with a language argument rather than calling set_language.
        Stores source in self._current_source for use by extraction methods.
        """
        from tree_sitter import Parser
        self._current_source = source
        parser = Parser(self.language)
        return parser.parse(source)

    def parse_file(self, file_path: Path) -> Tree:
        """Parse a C++ file from disk."""
        content = file_path.read_bytes()
        return self.parse(content)


# ---------------------------------------------------------------------------
# CppScanner
# ---------------------------------------------------------------------------


class CppScanner(BaseScanner):
    """
    Scanner for C/C++ source files using tree-sitter-cpp.

    Wraps ``CppTreeSitterParser`` and converts its IR output to the
    legacy ``ScanResult`` format for backward compatibility.

    Extracts:
    - #include "local.h"  → INCLUDE_LOCAL
    - #include <system.h> → INCLUDE_SYSTEM
    - class/struct declarations → symbols
    - function definitions → symbols

    Resolution:
    - Local includes are resolved via ``PathResolver`` using the file index.
    - System includes are classified as external.
    - Stdlib headers are detected via ``PathResolver._is_cpp_stdlib``.
    """

    name = "cpp"

    def __init__(
        self,
        project_root: Path | None = None,
        file_index: dict[str, Path] | None = None,
    ) -> None:
        self._parser = CppTreeSitterParser()
        self._resolver = PathResolver(
            project_root=project_root or Path.cwd(),
            file_index=file_index,
        )

    # -- Properties ---------------------------------------------------------

    @property
    def parser(self) -> CppTreeSitterParser:
        """Return the underlying tree-sitter parser instance."""
        return self._parser

    @property
    def resolver(self) -> PathResolver:
        """Return the path resolver instance."""
        return self._resolver

    # -- BaseScanner interface ----------------------------------------------

    def can_scan(self, path: Path) -> bool:
        """Return True if the file has a C/C++ extension."""
        return path.suffix.lower() in CPP_EXTENSIONS

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] | None = None,
    ) -> ScanResult:
        """
        Scan a C++ source file and return a ``ScanResult``.

        Uses ``CppTreeSitterParser`` for accurate include and symbol extraction,
        then resolves local includes via the file index.
        """
        rel_posix = normalize_path_to_posix(str(file_path), str(project_root))
        suffix = file_path.suffix.lower()

        size_bytes = 0
        content_bytes = b""

        try:
            size_bytes = file_path.stat().st_size
            content_bytes = file_path.read_bytes()
        except OSError as e:
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.CPP,
                suffix=suffix,
                size_bytes=0,
                error=f"OS error reading file: {e}",
            )

        # Update resolver with the latest project_root and file_index
        self._resolver.project_root = project_root.resolve()
        if file_index:
            self._resolver.file_index.update(file_index)

        # Parse with tree-sitter
        tree = self._parser.parse(content_bytes)

        # Extract includes
        raw_imports = self._parser.extract_imports(tree, rel_posix, source=content_bytes)

        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []
        warnings: list[str] = []

        for raw_import in raw_imports:
            raw_text = raw_import.raw_text
            kind = self._to_dependency_kind(raw_import.kind)
            raw_dep = RawDependency(
                raw_text=raw_text,
                kind=kind,
                line_number=raw_import.line,
                column_offset=raw_import.column,
            )
            raw_deps.append(raw_dep)

            resolved = self._resolve_include(
                raw_import,
                raw_dep,
                file_path,
                project_root,
                file_index,
            )
            resolved_deps.append(resolved)

            if resolved.is_unresolved and "multiple" in resolved.resolution_note:
                warnings.append(
                    f"Line {raw_import.line}: ambiguous include '{raw_import.specifier}': "
                    f"{resolved.resolution_note}"
                )

        # Extract symbols
        raw_symbols = self._parser.extract_symbols(tree, rel_posix, source=content_bytes)
        symbols = self._to_extracted_symbols(raw_symbols)

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.CPP,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=warnings,
        )

    # -- Parsing / resolution helpers ---------------------------------------

    def parse_file(self, file_path: Path) -> Tree:
        """Parse a C++ file and return the tree-sitter Tree."""
        return self._parser.parse_file(file_path)

    def _resolve_include(
        self,
        raw_import: RawImport,
        raw_dep: RawDependency,
        source_file: Path,
        project_root: Path,
        file_index: dict[str, Path],
    ) -> ResolvedDependency:
        """
        Resolve a C++ #include directive to a project file or classify it.

        - Quoted includes (#include "foo.h"): resolved via the file index,
          searching relative to the source file, then by basename.
        - Angle-bracket includes (#include <foo.h>): always external/system.
        """
        specifier = raw_import.specifier
        is_local = raw_import.kind == ImportKind.INCLUDE_LOCAL

        if not is_local:
            # System include: check stdlib
            if self._resolver._is_cpp_stdlib(specifier):
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=None,
                    is_external=True,
                    is_stdlib=True,
                    is_unresolved=False,
                    confidence=ConfidenceLevel.AST,
                    confidence_source=ConfidenceSource.STATIC_AST,
                )
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=False,
                confidence=ConfidenceLevel.AST,
                confidence_source=ConfidenceSource.STATIC_AST,
            )

        # Local include: try to resolve
        return self._resolve_local_include(
            specifier,
            raw_dep,
            source_file,
            project_root,
            file_index,
        )

    def _resolve_local_include(
        self,
        include_text: str,
        raw_dep: RawDependency,
        source_file: Path,
        project_root: Path,
        file_index: dict[str, Path],
    ) -> ResolvedDependency:
        """
        Resolve a quoted #include path to a project-relative POSIX path.

        Search order:
        1. Relative to the source file's directory.
        2. Relative to the project root.
        3. Basename search across the file index (raises ambiguity warning).
        """
        from pathlib import PurePosixPath

        normalized = include_text.replace("\\", "/")

        # Strategy 1: relative to source file directory
        if "/" in normalized or "\\" in include_text:
            rel_path = source_file.parent / normalized.replace("/", "\\")
            if rel_path.exists() and rel_path.is_file():
                rel = normalize_path_to_posix(str(rel_path), str(project_root))
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=rel,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                    confidence=ConfidenceLevel.AST,
                    confidence_source=ConfidenceSource.STATIC_AST,
                )

        # Strategy 2: relative to project root
        root_path = project_root / normalized.replace("/", "\\")
        if root_path.exists() and root_path.is_file():
            rel = normalize_path_to_posix(str(root_path), str(project_root))
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=rel,
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
                confidence=ConfidenceLevel.AST,
                confidence_source=ConfidenceSource.STATIC_AST,
            )

        # Strategy 3: basename search in file index
        basename = PurePosixPath(normalized).name
        matches: list[str] = []

        if file_index:
            for proj_rel in file_index:
                if PurePosixPath(proj_rel).name == basename:
                    matches.append(proj_rel)
        elif self._resolver.file_index:
            for proj_rel in self._resolver.file_index:
                if PurePosixPath(proj_rel).name == basename:
                    matches.append(proj_rel)

        if not matches:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=True,
                resolution_note=f"header '{include_text}' not found in project",
                confidence=ConfidenceLevel.UNKNOWN,
                confidence_source=ConfidenceSource.UNRESOLVED,
            )

        if len(matches) == 1:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=matches[0],
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
                confidence=ConfidenceLevel.AST,
                confidence_source=ConfidenceSource.STATIC_AST,
            )

        # Multiple matches: ambiguous
        return ResolvedDependency(
            raw=raw_dep,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=True,
            resolution_note=f"multiple matches: {', '.join(matches)}",
            confidence=ConfidenceLevel.HEURISTIC,
            confidence_source=ConfidenceSource.NAME_MATCH,
        )

    # -- Conversion helpers -------------------------------------------------

    @staticmethod
    def _to_dependency_kind(kind: ImportKind) -> DependencyKind:
        """Map IR ImportKind to models DependencyKind."""
        mapping = {
            ImportKind.INCLUDE_LOCAL: DependencyKind.INCLUDE_LOCAL,
            ImportKind.INCLUDE_SYSTEM: DependencyKind.INCLUDE_SYSTEM,
        }
        return mapping.get(kind, DependencyKind.UNKNOWN)

    @staticmethod
    def _to_extracted_symbols(raw_symbols: list[RawSymbol]) -> list[ExtractedSymbol]:
        """Convert IR RawSymbol list to models ExtractedSymbol list."""
        from deppulse.core.ir import SymType as IRSymType

        result: list[ExtractedSymbol] = []
        for sym in raw_symbols:
            type_map = {
                IRSymType.FUNCTION: "function",
                IRSymType.CLASS: "class",
                IRSymType.METHOD: "method",
                IRSymType.PROPERTY: "property",
                IRSymType.CONSTRUCTOR: "constructor",
                IRSymType.INTERFACE: "interface",
                IRSymType.ENUM: "enum",
            }
            symbol_type = type_map.get(sym.sym_type, "unknown")
            result.append(ExtractedSymbol(
                symbol_type=symbol_type,
                name=sym.name,
                fully_qualified=sym.fqn,
            ))
        return result
