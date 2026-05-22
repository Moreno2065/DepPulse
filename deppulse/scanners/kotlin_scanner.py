"""Kotlin source code scanner using tree-sitter-kotlin.

This module provides two classes:

1. KotlinTreeSitterParser (inherits TreeSitterParser)
   - Handles tree-sitter parsing and raw extraction (RawImport, RawSymbol, RawCall).
   - Used by the IR-based orchestrator.

2. KotlinScanner (inherits BaseScanner, wraps KotlinTreeSitterParser)
   - Backward-compatible scanner producing ScanResult with ExtractedSymbol /
     RawDependency / ResolvedDependency.
   - Used by the legacy graph-building path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from deppulse.core.ir import (
    ImportKind,
    LineRange,
    RawCall,
    RawImport,
    RawSymbol,
    SymType,
    Visibility,
)
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
# Backward-compatible module-level helpers (used by tests)
# Re-exported from PathResolver for test compatibility
# ---------------------------------------------------------------------------

def _is_stdlib(module: str) -> bool:
    """Return True if the import looks like a Kotlin/Java standard library."""
    return module.startswith("kotlin.") or module.startswith("java.") or module.startswith("javax.")


def _is_external(module: str) -> bool:
    """Return True if the import looks like an external/third-party library."""
    prefixes = ("org.", "com.", "android.", "io.", "net.")
    return module.startswith(prefixes)


def _resolve_import_to_path(module: str, file_index: Optional[dict]) -> Optional[str]:
    """
    Convert a fully-qualified Kotlin module name to a project-relative path.
    e.g. com.example.utils -> com/example/utils.kt
    """
    if file_index is None:
        return None
    path_with_slash = module.replace(".", "/")
    candidates = [
        path_with_slash + ".kt",
        path_with_slash + ".kts",
        path_with_slash + "/__init__.kt",
        path_with_slash + "/__init__.kts",
    ]
    for candidate in candidates:
        if candidate in file_index:
            return candidate
    return None


def _fq_with_prefix(sym_type: str, name: str, fqn: str) -> str:
    """
    Ensure the fully-qualified name has a type: prefix.
    e.g. function:hello, class:MyService, method:MyClass.method
    """
    if fqn.startswith(("function:", "class:", "method:", "property:", "constructor:",
                       "interface:", "enum:", "annotation:", "type_alias:")):
        return fqn
    return f"{sym_type}:{name}"


def _extract_symbols_regex(content: str) -> list[ExtractedSymbol]:
    """
    Compatibility shim: extract Kotlin symbols from source code.

    Uses tree-sitter-kotlin for accurate extraction when available.
    Falls back to a regex-based approach for simple test cases.
    Returns list of ExtractedSymbol (same as the old regex implementation).
    """
    from deppulse.models import ExtractedSymbol

    # Try tree-sitter first (skip for now — KotlinTreeSitterParser needs refinement
    # for class name extraction before this is reliable)
    # The regex fallback below handles all the test cases correctly.
    try:
        parser = KotlinTreeSitterParser()
        tree = parser.parse(content.encode("utf-8"))
        raw_symbols = parser.extract_symbols(tree, "")
        if raw_symbols:
            valid = [r for r in raw_symbols if r.name and r.name != "<unknown>"]
            if valid:
                return [
                    ExtractedSymbol(
                        symbol_type=r.sym_type.value,
                        name=r.name,
                        fully_qualified=r.fqn,
                    )
                    for r in valid
                ]
    except Exception:
        pass

    # Fallback: regex-based extraction
    import re
    symbols: list[ExtractedSymbol] = []
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)

    # Track brace depth and current class context
    brace_depth = 0
    class_stack: list[str] = []

    RE_CLASS = re.compile(r"^\s*(?:class|interface|object|annotation\s+class)\s+(\w+)")
    RE_FUNC = re.compile(r"^\s*(?!(?:val|var)\b)\bfun\s+(\w+)\s*(?:[<{(]|$)")
    RE_PROP = re.compile(r"^\s*(?:val|var)\s+(\w+)")

    for line in content.split("\n"):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("//"):
            continue

        opens = stripped.count("{")
        closes = stripped.count("}")

        # Class declarations
        class_match = RE_CLASS.match(stripped)
        if class_match:
            class_name = class_match.group(1)
            sym_type = "class"
            if "annotation" in stripped:
                sym_type = "annotation"
            elif "interface" in stripped:
                sym_type = "interface"
            elif "object" in stripped:
                sym_type = "object"
            symbols.append(ExtractedSymbol(
                symbol_type=sym_type,
                name=class_name,
                fully_qualified=f"{sym_type}:{class_name}",
            ))
            class_stack.append(class_name)
            # Update brace depth BEFORE continuing so we track being inside the class
            brace_depth += opens - closes
            brace_depth = max(0, brace_depth)
            continue

        # Function / property declarations
        in_class = brace_depth >= 1
        current_class = class_stack[-1] if class_stack else None

        if in_class:
            func_match = RE_FUNC.search(stripped)
            if func_match:
                func_name = func_match.group(1)
                symbols.append(ExtractedSymbol(
                    symbol_type="method",
                    name=func_name,
                    fully_qualified=f"method:{current_class}.{func_name}",
                ))
        else:
            prop_match = RE_PROP.match(stripped)
            if prop_match:
                prop_name = prop_match.group(1)
                symbols.append(ExtractedSymbol(
                    symbol_type="property",
                    name=prop_name,
                    fully_qualified=f"property:{prop_name}",
                ))

        # Update brace depth
        brace_depth += opens - closes
        brace_depth = max(0, brace_depth)

        # Exit classes when back to brace_depth 0
        if closes > 0 and brace_depth == 0:
            class_stack.clear()

    return symbols


# ---------------------------------------------------------------------------
# tree-sitter-kotlin language binding
# ---------------------------------------------------------------------------

try:
    from tree_sitter_kotlin import language as _kotlin_language
except ImportError:  # pragma: no cover
    raise ImportError(
        "tree-sitter-kotlin is not installed. "
        "Install it with: pip install tree-sitter-kotlin"
    )


# ---------------------------------------------------------------------------
# KotlinTreeSitterParser
# ---------------------------------------------------------------------------


class KotlinTreeSitterParser(TreeSitterParser):
    """
    Tree-sitter-based parser for Kotlin source files.

    Extracts:
    - Package declarations
    - Imports (including wildcard and aliased)
    - Class / interface / object / annotation-class declarations
    - Function declarations (including extension functions)
    - Property declarations (val / var)
    - Companion object members
    """

    language_name = "kotlin"

    @property
    def language(self) -> "TSLanguage":
        """Return the tree-sitter Language object for Kotlin."""
        from tree_sitter import Language

        capsule = _kotlin_language()  # call the builtin method to get PyCapsule
        return Language(capsule)       # wrap the capsule in a Language object

    # ------------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------------

    def _fq_with_type(self, sym_type: SymType, name: str, parent: Optional[str] = None) -> str:
        """Build a fully-qualified name with type prefix.

        parent_fqn may already have a type prefix (from tree-sitter results).
        We strip any existing prefix before building the new fqn.
        """
        prefix = sym_type.value
        if parent:
            # Strip any existing type prefix from parent
            import re
            clean_parent = re.sub(r"^(function|class|method|property|constructor|interface|enum|annotation|type_alias):", "", parent)
            return f"{prefix}:{clean_parent}.{name}"
        return f"{prefix}:{name}"

    # ------------------------------------------------------------------------
    # Package
    # ------------------------------------------------------------------------

    def extract_package(self, tree: "Tree", source: bytes) -> Optional[str]:
        """Extract the package declaration from the file, if any."""
        root = tree.root_node
        for child in root.children:
            if child.type == "package_header":
                return self._node_text(child, source).replace("package", "").strip()
        return None

    # ------------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------------

    def extract_imports(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawImport]:
        """
        Extract all import directives from the Kotlin source tree.

        Handles:
        - Simple imports:     import com.example.Utils
        - Wildcard imports:  import com.example.*
        - Aliased imports:   import com.example.Utils as U
        """
        imports: list[RawImport] = []
        root = tree.root_node
        source = root.text  # bytes needed for _node_* helpers

        for child in root.children:
            if child.type == "import":
                raw_text = self._node_text(child, source).strip()
                # Remove 'import ' prefix to get the specifier
                specifier = raw_text[7:].strip()  # len("import ") == 7

                is_wildcard = "." in specifier and specifier.endswith(".*")
                import_kind: ImportKind
                if " as " in specifier:
                    import_kind = ImportKind.KOTLIN_IMPORT  # aliased imports use same kind
                elif is_wildcard:
                    import_kind = ImportKind.KOTLIN_WILDCARD_IMPORT
                else:
                    import_kind = ImportKind.KOTLIN_IMPORT

                imports.append(
                    RawImport(
                        raw_text=raw_text,
                        specifier=specifier,
                        kind=import_kind,
                        line=self._node_line(child, source),
                        column=self._node_column(child, source),
                        is_wildcard=is_wildcard,
                    )
                )

        return imports

    # ------------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------------

    def extract_symbols(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawSymbol]:
        """
        Extract all top-level and nested symbol definitions from the Kotlin tree.

        Handles:
        - Classes, interfaces, objects, annotation classes
        - Functions (including extension functions)
        - Properties (val / var)
        - Companion object members (nested)
        """
        symbols: list[RawSymbol] = []
        source = tree.root_node.text
        self._collect_symbols(tree.root_node, source, file_path, symbols, parent_fqn=None)
        return symbols

    def _collect_symbols(
        self,
        node,
        source: bytes,
        file_path: str,
        symbols: list[RawSymbol],
        parent_fqn: Optional[str],
    ) -> None:
        """
        Recursively walk the tree and extract symbol definitions.

        parent_fqn is the fully-qualified name of the enclosing scope
        (e.g. "com.example.MyClass" for a method inside MyClass).
        """
        node_type = node.type

        # ---- Top-level / member declarations ----
        if node_type in (
            "class_declaration",
            "interface_declaration",
            "object_declaration",
            "annotation_class_declaration",
        ):
            self._extract_type_declaration(node, source, file_path, symbols, parent_fqn)
            return  # children processed inside _extract_type_declaration

        if node_type in (
            "function_declaration",
            "property_declaration",
        ):
            self._extract_member_declaration(
                node, source, file_path, symbols, parent_fqn
            )
            # Don't recurse into function/property body — too noisy
            return

        if node_type == "companion_object":
            # Companion object members inherit the enclosing class as parent
            companion_parent = parent_fqn
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "property_declaration",
                    "class_declaration",
                    "interface_declaration",
                    "object_declaration",
                ):
                    self._collect_symbols(
                        child, source, file_path, symbols, companion_parent
                    )
            return

        # ---- Recurse into children for scopes ----
        if node_type in (
            "source_file",
            "class_body",
            "object_body",
            "primary_constructor",  # some properties declared here
        ):
            for child in node.children:
                self._collect_symbols(child, source, file_path, symbols, parent_fqn)

    def _extract_type_declaration(
        self,
        node,
        source: bytes,
        file_path: str,
        symbols: list[RawSymbol],
        parent_fqn: Optional[str],
    ) -> None:
        """Extract a class/interface/object/annotation class and its members."""
        type_map = {
            "class_declaration": SymType.CLASS,
            "interface_declaration": SymType.INTERFACE,
            "object_declaration": SymType.CLASS,  # objects are class-like
            "annotation_class_declaration": SymType.ANNOTATION,
        }
        sym_type = type_map.get(node.type, SymType.UNKNOWN)

        name_node = (
            self._find_child_by_type(node, "type_identifier")
            or self._find_child_by_type(node, "simple_identifier")
            or self._find_child_by_type(node, "identifier")
            or self._find_child_by_type(node, "user_type")
        )
        name = self._node_text(name_node, source) if name_node else "<unknown>"

        fqn = self._fq_with_type(sym_type, name, parent_fqn)

        symbols.append(
            RawSymbol(
                name=name,
                fqn=fqn,
                sym_type=sym_type,
                file_path=file_path,
                line_range=self._node_range(node, source),
                visibility=self._visibility_from_modifiers(node, source),
            )
        )

        # Recurse into class body for member declarations
        class_body = self._find_child_by_type(node, "class_body") or \
                     self._find_child_by_type(node, "object_body")
        if class_body:
            for child in class_body.children:
                self._collect_symbols(child, source, file_path, symbols, fqn)

    def _extract_member_declaration(
        self,
        node,
        source: bytes,
        file_path: str,
        symbols: list[RawSymbol],
        parent_fqn: Optional[str],
    ) -> None:
        """Extract a function or property declaration as a member symbol."""
        if node.type == "function_declaration":
            self._extract_function(node, source, file_path, symbols, parent_fqn)
        elif node.type == "property_declaration":
            self._extract_property(node, source, file_path, symbols, parent_fqn)

    def _extract_function(
        self,
        node,
        source: bytes,
        file_path: str,
        symbols: list[RawSymbol],
        parent_fqn: Optional[str],
    ) -> None:
        """Extract a function (or extension function) declaration."""
        # Check for modifiers (private, internal, etc.)
        visibility = self._visibility_from_modifiers(node, source)

        # Determine if this is an extension function: receiver type before function name
        # Pattern: (type_identifier | user_type) + "fun"  → extension function
        is_extension = False
        receiver_type: Optional[str] = None
        children = node.children
        for i, child in enumerate(children):
            if child.type in ("type_identifier", "user_type"):
                # Check if the next non-whitespace child is "fun"
                for nxt in children[i + 1:]:
                    if nxt.type == "identifier" and self._node_text(nxt, source) == "fun":
                        # This type is a receiver — extension function
                        is_extension = True
                        receiver_type = self._node_text(child, source)
                        break
                break

        # Get the function name (identifier after "fun")
        func_name = None
        seen_fun = False
        for child in node.children:
            text = self._node_text(child, source)
            if text == "fun":
                seen_fun = True
                continue
            if seen_fun and child.type == "identifier":
                func_name = text
                break

        if not func_name:
            return

        if is_extension and receiver_type:
            fqn = f"function:{receiver_type}.{func_name}"
        elif parent_fqn:
            fqn = f"method:{parent_fqn}.{func_name}"
        else:
            fqn = f"function:{func_name}"

        sym_type = SymType.METHOD if parent_fqn else SymType.FUNCTION

        symbols.append(
            RawSymbol(
                name=func_name,
                fqn=fqn,
                sym_type=sym_type,
                file_path=file_path,
                line_range=self._node_range(node, source),
                visibility=visibility,
            )
        )

    def _extract_property(
        self,
        node,
        source: bytes,
        file_path: str,
        symbols: list[RawSymbol],
        parent_fqn: Optional[str],
    ) -> None:
        """Extract a property (val/var) declaration."""
        visibility = self._visibility_from_modifiers(node, source)

        # Get the property name(s) — first identifier after val/var
        for child in node.children:
            if child.type == "identifier":
                prop_name = self._node_text(child, source)
                if parent_fqn:
                    fqn = f"property:{parent_fqn}.{prop_name}"
                else:
                    fqn = f"property:{prop_name}"
                sym_type = SymType.PROPERTY
                symbols.append(
                    RawSymbol(
                        name=prop_name,
                        fqn=fqn,
                        sym_type=sym_type,
                        file_path=file_path,
                        line_range=self._node_range(node, source),
                        visibility=visibility,
                    )
                )
                break

    def _visibility_from_modifiers(self, node, source: bytes) -> Visibility:
        """Determine visibility from modifiers on a declaration node."""
        modifiers = self._find_child_by_type(node, "modifiers")
        if modifiers is None:
            return Visibility.UNKNOWN

        mod_text = self._node_text(modifiers, source)
        if "private" in mod_text:
            return Visibility.PRIVATE
        if "protected" in mod_text:
            return Visibility.PROTECTED
        if "internal" in mod_text:
            return Visibility.INTERNAL
        if "public" in mod_text:
            return Visibility.PUBLIC
        return Visibility.UNKNOWN

    # ------------------------------------------------------------------------
    # Calls (placeholder — Kotlin symbol-level call graph not yet implemented)
    # ------------------------------------------------------------------------

    def extract_calls(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawCall]:
        """Extract call sites from the Kotlin tree (not yet implemented)."""
        return []


# ---------------------------------------------------------------------------
# KotlinScanner (backward-compatible wrapper)
# ---------------------------------------------------------------------------


class KotlinScanner(BaseScanner):
    """
    Backward-compatible scanner for Kotlin source files.

    Wraps KotlinTreeSitterParser internally and converts its output to the
    legacy ScanResult / RawDependency / ResolvedDependency / ExtractedSymbol
    interface.

    Delegates path resolution to PathResolver.
    """

    name = "kotlin"

    KOTLIN_SUFFIXES = frozenset({".kt", ".kts"})

    def __init__(self) -> None:
        self._resolver: Optional[PathResolver] = None
        self._project_root: Optional[Path] = None

    # ------------------------------------------------------------------------
    # PathResolver / parser plumbing (redesign spec)
    # ------------------------------------------------------------------------

    @property
    def resolver(self) -> PathResolver:
        if self._resolver is None:
            root = self._project_root or Path.cwd()
            self._resolver = PathResolver(root)
        return self._resolver

    @property
    def parser(self) -> KotlinTreeSitterParser:
        return _get_parser()

    # ------------------------------------------------------------------------
    # BaseScanner interface
    # ------------------------------------------------------------------------

    def can_scan(self, path: Path) -> bool:
        return path.suffix in self.KOTLIN_SUFFIXES

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] = {},
    ) -> ScanResult:
        self._project_root = project_root
        self._resolver = PathResolver(project_root, file_index)

        rel_posix = normalize_path_to_posix(str(file_path), str(project_root))
        suffix = file_path.suffix

        size_bytes = 0
        content = ""
        try:
            size_bytes = file_path.stat().st_size
            content = file_path.read_text(encoding="utf-8")
        except OSError as e:
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.KOTLIN,
                suffix=suffix,
                size_bytes=0,
                error=f"OS error reading file: {e}",
            )

        source = content.encode("utf-8")
        tree = None
        warnings: list[str] = []

        try:
            tree = self.parser.parse(source)
        except Exception as e:
            warnings.append(f"tree-sitter parse error: {e}")

        # Extract imports via tree-sitter
        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []

        if tree is not None:
            try:
                raw_imports = self.parser.extract_imports(tree, rel_posix)
            except Exception as e:
                warnings.append(f"import extraction error: {e}")
                raw_imports = []

            for raw_import in raw_imports:
                raw_dep = RawDependency(
                    raw_text=raw_import.raw_text,
                    kind=DependencyKind.KOTLIN_IMPORT,
                    line_number=raw_import.line,
                    column_offset=raw_import.column,
                )
                raw_deps.append(raw_dep)

                resolved = self._resolve_import(raw_import, raw_dep, file_index)
                resolved_deps.append(resolved)

        # Extract symbols via tree-sitter
        symbols: list[ExtractedSymbol] = []
        if tree is not None:
            try:
                raw_symbols = self.parser.extract_symbols(tree, rel_posix)
            except Exception as e:
                warnings.append(f"symbol extraction error: {e}")
                raw_symbols = []

            for raw_sym in raw_symbols:
                sym_type_map = {
                    SymType.FUNCTION: "function",
                    SymType.CLASS: "class",
                    SymType.INTERFACE: "interface",
                    SymType.METHOD: "method",
                    SymType.PROPERTY: "property",
                    SymType.CONSTRUCTOR: "constructor",
                    SymType.ENUM: "enum",
                    SymType.ANNOTATION: "annotation",
                    SymType.TYPE_ALIAS: "type_alias",
                    SymType.UNKNOWN: "unknown",
                }
                symbols.append(
                    ExtractedSymbol(
                        symbol_type=sym_type_map.get(raw_sym.sym_type, "unknown"),
                        name=raw_sym.name,
                        fully_qualified=raw_sym.fqn,
                    )
                )

        is_script = suffix == ".kts"

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.KOTLIN,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=warnings,
            is_script=is_script,
        )

    # ------------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------------

    def _resolve_import(
        self,
        raw_import: RawImport,
        raw_dep: RawDependency,
        file_index: dict[str, Path],
    ) -> ResolvedDependency:
        """
        Resolve a Kotlin import to a project file, or classify it as
        external / stdlib.
        """
        specifier = raw_import.specifier

        # Try project file resolution first
        resolved_path = self.resolver.resolve_absolute(specifier, "kotlin")
        if resolved_path is not None:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=resolved_path,
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
            )

        # Classify as stdlib / external
        if self.resolver.is_stdlib(specifier, "kotlin"):
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=True,
                is_unresolved=False,
            )

        if self.resolver.is_external(specifier, "kotlin"):
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
            resolution_note=f"no project file found for {specifier}",
        )

    # ------------------------------------------------------------------------
    # parse_file (redesign spec: public API for orchestrator)
    # ------------------------------------------------------------------------

    def parse_file(self, file_path: Path) -> "Tree":
        """
        Parse a Kotlin file and return the tree-sitter Tree.

        This method is used by the IR-based orchestrator to build the
        Unified IR directly from the parsed tree.
        """
        return self.parser.parse_file(file_path)


# ---------------------------------------------------------------------------
# Module-level parser singleton (lazy; constructed once)
# ---------------------------------------------------------------------------

_KOTLIN_PARSER: Optional[KotlinTreeSitterParser] = None


def _get_parser() -> KotlinTreeSitterParser:
    global _KOTLIN_PARSER
    if _KOTLIN_PARSER is None:
        _KOTLIN_PARSER = KotlinTreeSitterParser()
    return _KOTLIN_PARSER
