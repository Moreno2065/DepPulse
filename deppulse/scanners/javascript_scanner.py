"""JavaScript source scanner using tree-sitter-javascript for ESM and CommonJS extraction."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from deppulse.core.path_resolver import PathResolver
from deppulse.core.tree_sitter_parser import TreeSitterParser
from deppulse.models import (
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
    from tree_sitter import Language as TSLanguage, Tree


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs"})


# ---------------------------------------------------------------------------
# Tree-sitter parser
# ---------------------------------------------------------------------------


class JavaScriptTreeSitterParser(TreeSitterParser):
    """
    Tree-sitter-based parser for JavaScript source files.

    Extracts:
    - ESM ``import`` statements (default, named, and namespace imports)
    - CommonJS ``require()`` calls
    - Function and class declarations
    """

    language_name = "javascript"

    @property
    def language(self) -> "TSLanguage":
        import tree_sitter_javascript
        from tree_sitter import Language

        capsule = tree_sitter_javascript.language()  # call builtin → PyCapsule
        return Language(capsule)                     # wrap in Language object

    # ------------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------------

    def extract_imports(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawDependency]:
        from deppulse.models import RawDependency

        source = self._file_source(file_path)
        imports: list[RawDependency] = []

        # ESM import statements
        for imp_node in self._all_nodes_of_type(tree, "import_statement"):
            line_no = self._node_line(imp_node, source)
            raw_text = self._node_text(imp_node, source).strip()
            imports.append(
                RawDependency(
                    raw_text=raw_text,
                    kind=DependencyKind.JAVASCRIPT_IMPORT,
                    line_number=line_no,
                )
            )

        # CommonJS require() call expressions
        for call_node in self._all_nodes_of_type(tree, "call_expression"):
            fn_node = self._find_child_by_type(call_node, "identifier")
            if fn_node is not None and self._node_text(fn_node, source) == "require":
                line_no = self._node_line(call_node, source)
                raw_text = self._node_text(call_node, source).strip()
                imports.append(
                    RawDependency(
                        raw_text=f"const ... = require({raw_text})",
                        kind=DependencyKind.JAVASCRIPT_IMPORT,
                        line_number=line_no,
                    )
                )

        return imports

    # ------------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------------

    def extract_symbols(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[ExtractedSymbol]:
        from deppulse.models import ExtractedSymbol

        source = self._file_source(file_path)
        symbols: list[ExtractedSymbol] = []

        for decl_node in self._all_nodes_of_type(tree, "function_declaration"):
            name_node = self._find_child_by_type(decl_node, "identifier")
            if name_node is not None:
                name = self._node_text(name_node, source)
                symbols.append(
                    ExtractedSymbol(
                        symbol_type="function",
                        name=name,
                        fully_qualified=f"function:{name}",
                    )
                )

        for decl_node in self._all_nodes_of_type(tree, "class_declaration"):
            name_node = self._find_child_by_type(decl_node, "identifier")
            if name_node is not None:
                name = self._node_text(name_node, source)
                symbols.append(
                    ExtractedSymbol(
                        symbol_type="class",
                        name=name,
                        fully_qualified=f"class:{name}",
                    )
                )

        return symbols

    # ------------------------------------------------------------------------
    # Call extraction (require() call sites)
    # ------------------------------------------------------------------------

    def extract_calls(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list:
        # Collected via extract_imports for now; call graph construction
        # would live in the orchestrator.
        return []

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    def _file_source(self, file_path: str) -> bytes:
        """Read file contents as bytes for node text extraction."""
        path = Path(file_path)
        if path.exists():
            return path.read_bytes()
        return b""


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class JavaScriptScanner(BaseScanner):
    """
    Scanner for JavaScript source files (``.js``, ``.jsx``, ``.mjs``).

    Uses ``tree-sitter-javascript`` to parse files and extract:
    - ESM imports: ``import x from 'y'``, ``import {x} from 'y'``, ``import * as x from 'y'``
    - CommonJS requires: ``require('x')``
    - Function and class declarations
    - Required/imported module specifiers resolved via ``PathResolver``
    """

    name = "javascript"

    def __init__(self) -> None:
        self._parser: Optional[JavaScriptTreeSitterParser] = None
        self._resolver: Optional[PathResolver] = None

    # ------------------------------------------------------------------------
    # BaseScanner contract
    # ------------------------------------------------------------------------

    def can_scan(self, path: Path) -> bool:
        """Return True for ``.js``, ``.jsx``, and ``.mjs`` files."""
        return path.suffix in JS_SUFFIXES

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] = {},
    ) -> ScanResult:
        """
        Scan a single JavaScript file and return a ``ScanResult``.

        Parameters
        ----------
        file_path : Path
            Absolute path to the file on disk.
        project_root : Path
            Absolute path to the project root directory.
        file_index : dict[str, Path]
            Project-relative POSIX path → absolute Path mapping.

        Returns
        -------
        ScanResult
        """
        rel_posix = normalize_path_to_posix(str(file_path), str(project_root))
        suffix = file_path.suffix

        size_bytes = 0
        try:
            size_bytes = file_path.stat().st_size
        except OSError:
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.JAVASCRIPT,
                suffix=suffix,
                size_bytes=0,
                error="OS error reading file",
            )

        # Lazily initialise parser and resolver
        if self._parser is None:
            self._parser = JavaScriptTreeSitterParser()
        if self._resolver is None:
            self._resolver = PathResolver(project_root=project_root, file_index=file_index)
            self._resolver.load_package_json(project_root)
            self._resolver.load_tsconfig(project_root)
        else:
            # Refresh file index in case it changed between calls
            self._resolver.file_index.clear()
            self._resolver.file_index.update(file_index)

        tree = self._parser.parse_file(file_path)
        source = file_path.read_bytes()

        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []

        # --- ESM import statements ---
        for imp_node in self._parser._all_nodes_of_type(tree, "import_statement"):
            raw_text = self._parser._node_text(imp_node, source).strip()
            line_no = self._parser._node_line(imp_node, source)
            raw_dep = RawDependency(
                raw_text=raw_text,
                kind=DependencyKind.JAVASCRIPT_IMPORT,
                line_number=line_no,
            )
            raw_deps.append(raw_dep)

            specifier = self._extract_module_specifier(imp_node, source)
            if specifier:
                resolved = self._resolve_specifier(
                    specifier, rel_posix, raw_dep, file_index
                )
            else:
                resolved = ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=None,
                    is_external=True,
                    is_stdlib=False,
                    is_unresolved=True,
                    resolution_note="could not extract module specifier",
                )
            resolved_deps.append(resolved)

        # --- CommonJS require() call expressions ---
        for call_node in self._parser._all_nodes_of_type(tree, "call_expression"):
            fn_node = self._parser._find_child_by_type(call_node, "identifier")
            if fn_node is None:
                continue
            if self._parser._node_text(fn_node, source) != "require":
                continue

            raw_text = self._parser._node_text(call_node, source).strip()
            line_no = self._parser._node_line(call_node, source)
            raw_dep = RawDependency(
                raw_text=f"const ... = require({raw_text})",
                kind=DependencyKind.JAVASCRIPT_IMPORT,
                line_number=line_no,
            )
            raw_deps.append(raw_dep)

            # Extract the specifier argument
            args_node = self._parser._find_child_by_type(call_node, "arguments")
            if args_node is not None:
                str_nodes = self._parser._find_children_by_type(args_node, "string")
                if str_nodes:
                    specifier = self._strip_string(self._parser._node_text(str_nodes[0], source))
                    resolved = self._resolve_specifier(
                        specifier, rel_posix, raw_dep, file_index
                    )
                else:
                    resolved = ResolvedDependency(
                        raw=raw_dep,
                        normalized_path=None,
                        is_external=True,
                        is_stdlib=False,
                        is_unresolved=True,
                        resolution_note="require() with non-literal argument",
                    )
            else:
                resolved = ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=None,
                    is_external=True,
                    is_stdlib=False,
                    is_unresolved=True,
                    resolution_note="require() with no arguments",
                )
            resolved_deps.append(resolved)

        # --- Extract function/class declarations ---
        symbols: list[ExtractedSymbol] = []
        for decl_node in self._parser._all_nodes_of_type(tree, "function_declaration"):
            name_node = self._parser._find_child_by_type(decl_node, "identifier")
            if name_node is not None:
                name = self._parser._node_text(name_node, source)
                symbols.append(
                    ExtractedSymbol(
                        symbol_type="function",
                        name=name,
                        fully_qualified=f"function:{name}",
                    )
                )

        for decl_node in self._parser._all_nodes_of_type(tree, "class_declaration"):
            name_node = self._parser._find_child_by_type(decl_node, "identifier")
            if name_node is not None:
                name = self._parser._node_text(name_node, source)
                symbols.append(
                    ExtractedSymbol(
                        symbol_type="class",
                        name=name,
                        fully_qualified=f"class:{name}",
                    )
                )

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.JAVASCRIPT,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=[],
        )

    # ------------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------------

    @property
    def parser(self) -> JavaScriptTreeSitterParser:
        """Return the lazy-initialised tree-sitter parser."""
        if self._parser is None:
            self._parser = JavaScriptTreeSitterParser()
        return self._parser

    @property
    def resolver(self) -> PathResolver:
        """Return the lazy-initialised path resolver."""
        if self._resolver is None:
            msg = "resolver not available until scan() is called with a project_root"
            raise RuntimeError(msg)
        return self._resolver

    # ------------------------------------------------------------------------
    # Specifier extraction helpers
    # ------------------------------------------------------------------------

    def _extract_module_specifier(self, imp_node, source: bytes) -> Optional[str]:
        """
        Extract the module specifier string from an ``import`` statement node.

        Handles:
        - ``import 'specifier'``  (side-effect only)
        - ``import x from 'specifier'``  (default)
        - ``import {x} from 'specifier'``  (named)
        - ``import * as x from 'specifier'``  (namespace)
        """
        # The specifier string is a child of the import_statement that is a string literal
        str_nodes = self._parser._find_children_by_type(imp_node, "string")
        if str_nodes:
            return self._strip_string(self._parser._node_text(str_nodes[0], source))
        return None

    @staticmethod
    def _strip_string(text: str) -> str:
        """Strip the surrounding quotes from a string literal."""
        if len(text) >= 2 and text[0] in ('"', "'"):
            return text[1:-1]
        return text

    # ------------------------------------------------------------------------
    # Specifier resolution
    # ------------------------------------------------------------------------

    def _resolve_specifier(
        self,
        specifier: str,
        from_file: str,
        raw_dep: RawDependency,
        file_index: dict[str, Path],
    ) -> ResolvedDependency:
        """
        Resolve a JavaScript module specifier to a project-relative POSIX path
        or classify it as external/stdlib.

        Resolution order:
        1. Relative specifier (``./``, ``../``) → PathResolver.resolve_relative()
        2. Absolute / bare specifier → PathResolver.resolve_absolute(language="javascript")
        3. External / stdlib classification via PathResolver
        """
        if self._resolver is None:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=True,
                resolution_note="resolver not initialised",
            )

        # Relative import — resolve against source file directory
        if specifier.startswith("./") or specifier.startswith("../"):
            resolved = self._resolver.resolve_relative(from_file, specifier)
            if resolved is not None:
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=resolved,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                )
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=True,
                resolution_note=f"relative specifier '{specifier}' not found in project",
            )

        # Bare / absolute specifier — use JavaScript resolution
        resolved = self._resolver.resolve_absolute(specifier, language="javascript")
        if resolved is not None:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=resolved,
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
            )

        # Classify as external / stdlib
        is_stdlib = self._resolver.is_stdlib(specifier, language="javascript")
        is_external = self._resolver.is_external(specifier, language="javascript")

        if is_stdlib:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=True,
                is_unresolved=False,
            )

        if is_external:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=False,
            )

        return ResolvedDependency(
            raw=raw_dep,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=True,
            resolution_note=f"bare specifier '{specifier}' not found in project",
        )
