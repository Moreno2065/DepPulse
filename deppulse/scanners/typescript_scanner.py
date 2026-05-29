"""TypeScript source code scanner using tree-sitter-typescript.

Extracts imports, exports, interfaces, type aliases, functions, and classes
from TypeScript (.ts) and TSX (.tsx) files. Skips .d.ts declaration files.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from deppulse.core.ir import (
    ImportKind,
    RawImport,
    RawSymbol,
    SymType,
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
    from tree_sitter import Language as TSLanguage
    from tree_sitter import Tree


_TS_SUFFIXES = frozenset({".ts", ".tsx"})
_DTS_SUFFIX = ".d.ts"


class TypeScriptTreeSitterParser(TreeSitterParser):
    """
    Tree-sitter parser for TypeScript and TSX source files.

    Uses tree-sitter-typescript grammar to extract imports, exports,
    interfaces, type aliases, functions, and classes.
    """

    language_name = "typescript"

    @property
    def language(self) -> TSLanguage:
        import tree_sitter_typescript as _ts
        from tree_sitter import Language

        # tree-sitter-typescript exposes language_ts and language_tsx
        # Try typescript first; if unavailable fall back to any available
        if hasattr(_ts, "language_ts") and callable(_ts.language_ts):
            capsule = _ts.language_ts()
        elif hasattr(_ts, "language_typescript") and callable(_ts.language_typescript):
            capsule = _ts.language_typescript()
        else:
            raise ImportError(
                "tree-sitter-typescript is not installed or lacks language bindings. "
                "Install it with: pip install tree-sitter-typescript"
            )
        return Language(capsule)

    def _is_tsx(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".tsx"

    def parse(self, source: bytes, *, is_tsx: bool = False) -> Tree:
        import tree_sitter

        lang: TSLanguage = self.language
        parser = tree_sitter.Parser(lang)
        return parser.parse(source)

    def parse_file(self, file_path: Path) -> Tree:
        content = file_path.read_bytes()
        return self.parse(content, is_tsx=self._is_tsx(file_path))

    def extract_imports(
        self,
        tree: Tree,
        file_path: str,
    ) -> list[RawImport]:
        imports: list[RawImport] = []
        source = b""
        if Path(file_path).exists():
            with contextlib.suppress(OSError):
                source = Path(file_path).read_bytes()

        for node in self._all_nodes_of_type(tree, "import_statement"):
            raw_text = self._node_text(node, source).strip()
            specifier = ""
            import_kind = ImportKind.ESM_IMPORT
            is_wildcard = False

            # Get the module specifier (string literal after 'from' or as argument to require())
            for child in node.children:
                if child.type == "string":
                    specifier = self._node_text(child, source).strip('"\'')
                    break

            if not specifier:
                # Try to get from 'require()' call_expression
                for child in node.children:
                    if child.type == "call_expression":
                        for c in child.children:
                            if c.type == "string":
                                specifier = self._node_text(c, source).strip('"\'')
                                break

            if not specifier:
                continue

            line = self._node_line(node, source)
            column = self._node_column(node, source)

            # Determine import kind
            has_default = False
            has_namespace = False
            has_named = False

            for child in node.children:
                if child.type == "import_clause":
                    for ic in child.children:
                        if ic.type == "identifier":
                            has_default = True
                        elif ic.type == "named_imports":
                            has_named = True
                        elif ic.type == "namespace_import":
                            has_namespace = True

            if has_namespace:
                import_kind = ImportKind.ESM_NAMESPACE_IMPORT
            elif has_default and has_named:
                import_kind = ImportKind.ESM_IMPORT
            elif has_default:
                import_kind = ImportKind.ESM_DEFAULT_IMPORT
            else:
                import_kind = ImportKind.ESM_IMPORT

            # Check for wildcard import (import * as)
            for child in node.children:
                if child.type == "import_clause":
                    for ic in child.children:
                        if ic.type == "namespace_import":
                            is_wildcard = True
                            import_kind = ImportKind.ESM_NAMESPACE_IMPORT

            imports.append(
                RawImport(
                    raw_text=raw_text,
                    specifier=specifier,
                    kind=import_kind,
                    line=line,
                    column=column,
                    is_wildcard=is_wildcard,
                )
            )

        # Handle CommonJS: const x = require('y')
        for node in self._all_nodes_of_type(tree, "variable_declarator"):
            source_bytes = b""
            if Path(file_path).exists():
                with contextlib.suppress(OSError):
                    source_bytes = Path(file_path).read_bytes()

            init = self._find_child_by_type(node, "call_expression")
            if init is None:
                continue

            func_name_node = self._find_child_by_type(init, "identifier")
            if func_name_node is None:
                continue

            func_name = self._node_text(func_name_node, source_bytes)
            if func_name != "require":
                continue

            string_node = self._find_child_by_type(init, "string")
            if string_node is None:
                continue

            specifier = self._node_text(string_node, source_bytes).strip('"\'')
            raw_text = f"const ... = require('{specifier}')"
            line = self._node_line(node, source_bytes)
            column = self._node_column(node, source_bytes)

            imports.append(
                RawImport(
                    raw_text=raw_text,
                    specifier=specifier,
                    kind=ImportKind.CJS_REQUIRE,
                    line=line,
                    column=column,
                    is_wildcard=False,
                )
            )

        return imports

    def extract_symbols(
        self,
        tree: Tree,
        file_path: str,
    ) -> list[RawSymbol]:
        symbols: list[RawSymbol] = []
        source = b""
        if Path(file_path).exists():
            with contextlib.suppress(OSError):
                source = Path(file_path).read_bytes()

        # Extract interface declarations
        for node in self._all_nodes_of_type(tree, "interface_declaration"):
            name_node = self._find_child_by_type(node, "type_identifier") or \
                self._find_child_by_type(node, "identifier")
            if name_node is None:
                continue

            name = self._node_text(name_node, source)
            fqn = name
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=fqn,
                    sym_type=SymType.INTERFACE,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

        # Extract type alias declarations
        for node in self._all_nodes_of_type(tree, "type_alias_declaration"):
            name_node = self._find_child_by_type(node, "type_identifier") or \
                self._find_child_by_type(node, "identifier")
            if name_node is None:
                continue

            name = self._node_text(name_node, source)
            fqn = name
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=fqn,
                    sym_type=SymType.TYPE_ALIAS,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

        # Extract function declarations
        for node in self._all_nodes_of_type(tree, "function_declaration"):
            name_node = self._find_child_by_type(node, "identifier")
            if name_node is None:
                continue

            name = self._node_text(name_node, source)
            fqn = name
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=fqn,
                    sym_type=SymType.FUNCTION,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

        # Extract class declarations
        for node in self._all_nodes_of_type(tree, "class_declaration"):
            name_node = self._find_child_by_type(node, "type_identifier") or \
                self._find_child_by_type(node, "identifier")
            if name_node is None:
                continue

            name = self._node_text(name_node, source)
            class_fqn = name
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=class_fqn,
                    sym_type=SymType.CLASS,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

            # Extract class members (methods and properties)
            body = self._find_child_by_type(node, "class_body")
            if body is not None:
                for member in body.children:
                    if member.type in ("method_definition", "public_field_definition"):
                        self._extract_class_member(
                            member, class_fqn, file_path, symbols, source
                        )

        # Extract enum declarations
        for node in self._all_nodes_of_type(tree, "enum_declaration"):
            name_node = self._find_child_by_type(node, "identifier")
            if name_node is None:
                continue

            name = self._node_text(name_node, source)
            fqn = name
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=fqn,
                    sym_type=SymType.ENUM,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

        return symbols

    def _extract_class_member(
        self,
        node,
        class_fqn: str,
        file_path: str,
        symbols: list[RawSymbol],
        source: bytes,
    ) -> None:
        """Extract method or property from a class body node."""
        if node.type == "method_definition":
            name_node = self._find_child_by_type(node, "property_identifier") or \
                self._find_child_by_type(node, "identifier")
            if name_node is None:
                return

            name = self._node_text(name_node, source)
            fqn = f"{class_fqn}.{name}"
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            sym_type = SymType.CONSTRUCTOR if name == "constructor" else SymType.METHOD

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=fqn,
                    sym_type=sym_type,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

        elif node.type == "public_field_definition":
            name_node = self._find_child_by_type(node, "property_identifier") or \
                self._find_child_by_type(node, "identifier")
            if name_node is None:
                return

            name = self._node_text(name_node, source)
            fqn = f"{class_fqn}.{name}"
            line_range = self._node_range(node, source)
            visibility = self._visibility_from_node(self._get_modifiers(node, source))

            symbols.append(
                RawSymbol(
                    name=name,
                    fqn=fqn,
                    sym_type=SymType.PROPERTY,
                    file_path=file_path,
                    line_range=line_range,
                    visibility=visibility,
                )
            )

    def _get_modifiers(self, node, source: bytes) -> list[str]:
        """Extract TypeScript access modifiers and other modifiers from a node."""
        modifiers: list[str] = []
        for child in node.children:
            if child.type in ("accessibility_modifier", "modifier"):
                modifiers.append(self._node_text(child, source))
        return modifiers


class TypeScriptScanner(BaseScanner):
    """
    Scanner for TypeScript and TSX source files using tree-sitter-typescript.

    Handles:
    - .ts  files (TypeScript)
    - .tsx files (TypeScript with JSX)
    - Skips .d.ts declaration files

    Extracts:
    - ESM and CommonJS imports
    - Interfaces, type aliases, functions, classes, enums
    - Class members (methods, properties, constructors)
    """

    name = "typescript"

    TS_SUFFIXES = _TS_SUFFIXES

    def __init__(self) -> None:
        self._parser: TypeScriptTreeSitterParser | None = None
        self._resolver: PathResolver | None = None

    @property
    def parser(self) -> TypeScriptTreeSitterParser:
        if self._parser is None:
            self._parser = TypeScriptTreeSitterParser()
        return self._parser

    @property
    def resolver(self) -> PathResolver:
        if self._resolver is None:
            self._resolver = PathResolver(project_root=Path.cwd())
        return self._resolver

    def can_scan(self, path: Path) -> bool:
        """
        Return True if this scanner can handle the given file.

        Accepts .ts and .tsx files, but explicitly rejects .d.ts declaration files.
        """
        suffix = path.suffix.lower()
        if suffix == _DTS_SUFFIX:
            return False
        return suffix in self.TS_SUFFIXES

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] | None = None,
    ) -> ScanResult:
        rel_posix = normalize_path_to_posix(str(file_path), str(project_root))
        suffix = file_path.suffix

        # Ensure resolver has tsconfig paths and file index
        self.resolver.load_tsconfig(project_root)
        if file_index:
            self.resolver.add_files(file_index)

        size_bytes = 0
        try:
            size_bytes = file_path.stat().st_size
        except OSError:
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.TYPESCRIPT,
                suffix=suffix,
                size_bytes=0,
                error="OS error reading file",
            )

        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []
        warnings: list[str] = []
        symbols: list[ExtractedSymbol] = []

        try:
            tree = self.parser.parse_file(file_path)
        except Exception as e:
            warnings.append(f"Parse error: {e}")
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.TYPESCRIPT,
                suffix=suffix,
                size_bytes=size_bytes,
                warnings=warnings,
                error=str(e),
            )

        # Extract imports
        raw_imports = self.parser.extract_imports(tree, rel_posix)
        for raw_import in raw_imports:
            raw_deps.append(
                RawDependency(
                    raw_text=raw_import.raw_text,
                    kind=DependencyKind.IMPORT,
                    line_number=raw_import.line,
                )
            )

            resolved = self._resolve_import(
                raw_import.specifier,
                raw_import.kind,
                raw_import.is_wildcard,
                rel_posix,
                raw_deps[-1],
            )
            resolved_deps.append(resolved)

        # Extract symbols
        raw_symbols = self.parser.extract_symbols(tree, rel_posix)
        for raw_sym in raw_symbols:
            sym_type_map = {
                SymType.FUNCTION: "function",
                SymType.CLASS: "class",
                SymType.METHOD: "method",
                SymType.PROPERTY: "property",
                SymType.CONSTRUCTOR: "constructor",
                SymType.INTERFACE: "interface",
                SymType.TYPE_ALIAS: "type_alias",
                SymType.ENUM: "enum",
            }
            symbols.append(
                ExtractedSymbol(
                    symbol_type=sym_type_map.get(raw_sym.sym_type, "unknown"),
                    name=raw_sym.name,
                    fully_qualified=raw_sym.fqn,
                )
            )

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.TYPESCRIPT,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=warnings,
        )

    def _resolve_import(
        self,
        specifier: str,
        import_kind: ImportKind,
        is_wildcard: bool,
        source_file: str,
        raw_dep: RawDependency,
    ) -> ResolvedDependency:
        """
        Resolve a TypeScript import specifier to a project-relative path,
        or classify as external/stdlib.
        """

        # Check stdlib / external first using PathResolver's helpers
        if self.resolver.is_stdlib(specifier, "typescript"):
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=True,
                is_unresolved=False,
            )

        if self.resolver.is_external(specifier, "typescript"):
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=False,
            )

        # Try relative resolution
        if specifier.startswith(".") or specifier.startswith("/"):
            resolved = self.resolver.resolve_relative(source_file, specifier)
            if resolved:
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=resolved,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                )

        # Try absolute resolution (tsconfig paths, package resolution)
        resolved = self.resolver.resolve_absolute(specifier, "typescript")
        if resolved:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=resolved,
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
            )

        # Unresolved
        return ResolvedDependency(
            raw=raw_dep,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=True,
            resolution_note=f"no project file found for {specifier}",
        )

    def resolve_dependency(
        self,
        raw_text: str,
        source_file: Path,
        project_root: Path,
        file_index: dict[str, Path] | None = None,
    ) -> ResolvedDependency:
        """Resolve a raw import specifier string to a project path or classify."""
        self.resolver.load_tsconfig(project_root)
        if file_index:
            self.resolver.add_files(file_index)

        # Strip import keywords to get the specifier
        specifier = raw_text.strip()
        for prefix in ("import ", "require(", "'", '"', ");"):
            specifier = specifier.replace(prefix, "")
        specifier = specifier.strip()

        import_kind = ImportKind.ESM_IMPORT
        raw_dep = RawDependency(
            raw_text=raw_text,
            kind=DependencyKind.IMPORT,
            line_number=0,
        )

        rel_source = normalize_path_to_posix(str(source_file), str(project_root))
        return self._resolve_import(specifier, import_kind, False, rel_source, raw_dep)
