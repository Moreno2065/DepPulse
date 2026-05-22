"""Java source code scanner using the javalang library for AST parsing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import javalang

from deppulse.core.path_resolver import PathResolver
from deppulse.models import (
    DependencyKind,
    ExtractedSymbol,
    Language,
    RawDependency,
    ResolvedDependency,
    ScanResult,
)
from deppulse.scanners.base import BaseScanner

if TYPE_CHECKING:
    from javalang.tree import CompilationUnit


_RE_STATIC_IMPORT_PREFIX = "import static "


def _is_stdlib(module: str) -> bool:
    """Return True if the import looks like a Java standard library."""
    return module.startswith("java.") or module.startswith("javax.")


def _is_external(module: str) -> bool:
    """Return True if the import looks like an external/third-party library."""
    prefixes = ("org.", "com.", "android.", "io.", "net.")
    return module.startswith(prefixes)


def _resolve_import_to_path(module: str, file_index: dict[str, Path]) -> Optional[str]:
    """
    Convert a fully-qualified Java module name to a project-relative path.

    e.g. com.example.utils -> com/example/utils.java or com/example/utils/__init__.java
    """
    if file_index is None:
        return None
    path_with_slash = module.replace(".", "/")
    candidates = [
        path_with_slash + ".java",
        path_with_slash + "/__init__.java",
    ]
    for candidate in candidates:
        if candidate in file_index:
            return candidate
    return None


class JavaJavalangParser:
    """
    Parser wrapper for Java source files using the javalang library.

    Provides a consistent interface with other parser implementations
    (e.g., tree-sitter-based parsers) while wrapping javalang's parse tree.
    """

    @property
    def language(self) -> type:
        """Return the javalang module as the language marker."""
        return javalang

    def parse(self, source: str) -> "CompilationUnit":
        """Parse Java source code string and return a javalang CompilationUnit."""
        return javalang.parse.parse(source)

    def parse_file(self, file_path: Path) -> "CompilationUnit":
        """Read and parse a Java source file, returning a javalang CompilationUnit."""
        content = file_path.read_text(encoding="utf-8")
        return self.parse(content)


class JavaScanner(BaseScanner):
    """
    Scanner for Java source files using the javalang library.

    Extracts:
    - Package declaration
    - Import statements (including static imports)
    - Class, interface, and method declarations
    """

    name = "java"

    JAVA_SUFFIXES = frozenset({".java"})

    def __init__(
        self,
        project_root: Optional[Path] = None,
        file_index: Optional[dict[str, Path]] = None,
    ) -> None:
        self._parser: Optional[JavaJavalangParser] = None
        self._project_root: Optional[Path] = project_root
        self._file_index: Optional[dict[str, Path]] = file_index
        self._resolver: Optional[PathResolver] = None

    @property
    def parser(self) -> JavaJavalangParser:
        """Return a JavaParser instance (lazily initialized)."""
        if self._parser is None:
            self._parser = JavaJavalangParser()
        return self._parser

    @property
    def resolver(self) -> PathResolver:
        """Return a PathResolver instance (lazily initialized)."""
        if self._resolver is None:
            root = self._project_root or Path.cwd()
            self._resolver = PathResolver(project_root=root, file_index=self._file_index)
        return self._resolver

    def parse_file(self, file_path: Path) -> "CompilationUnit":
        """Parse a Java file and return the javalang CompilationUnit."""
        return self.parser.parse_file(file_path)

    def can_scan(self, path: Path) -> bool:
        return path.suffix.lower() in self.JAVA_SUFFIXES

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] = {},
    ) -> ScanResult:
        from deppulse.models import normalize_path_to_posix

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
                language=Language.JAVA,
                suffix=suffix,
                size_bytes=0,
                error=f"OS error reading file: {e}",
            )

        # Extract package and imports via javalang AST
        package_name: Optional[str] = None
        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []
        warnings: list[str] = []
        symbols: list[ExtractedSymbol] = []

        tree = None
        try:
            tree = javalang.parse.parse(content)
        except javalang.parser.JavaSyntaxError as e:
            warnings.append(f"Syntax error: {e}")
        except Exception as e:
            warnings.append(f"Parse error: {e}")

        if tree is not None:
            try:
                package_name = tree.package.name if tree.package else None
            except Exception:
                pass

            try:
                for path_node, node in tree:
                    if isinstance(node, javalang.tree.Import):
                        is_static = bool(node.static)
                        is_wildcard = bool(node.wildcard)
                        module = node.path  # javalang uses 'path', not 'module'
                        # Reconstruct raw import text including wildcard
                        if is_wildcard:
                            raw_text = f"import {module}.*;"
                        elif is_static:
                            raw_text = f"import static {module};"
                        else:
                            raw_text = f"import {module};"
                        line_number = node.position.line if node.position else 0
                        raw_dep = RawDependency(
                            raw_text=raw_text,
                            kind=DependencyKind.JAVA_IMPORT,
                            line_number=line_number,
                        )
                        raw_deps.append(raw_dep)

                        resolved = self._resolve_import(
                            module, is_static, is_wildcard, raw_dep, file_index
                        )
                        resolved_deps.append(resolved)

                    elif isinstance(node, javalang.tree.ClassDeclaration):
                        fully_qualified = f"class:{node.name}"
                        symbols.append(
                            ExtractedSymbol(
                                symbol_type="class",
                                name=node.name,
                                fully_qualified=fully_qualified,
                            )
                        )
                        # Extract method symbols within the class
                        for method in node.methods or []:
                            if isinstance(method, javalang.tree.MethodDeclaration):
                                method_fq = f"method:{node.name}.{method.name}"
                                symbols.append(
                                    ExtractedSymbol(
                                        symbol_type="method",
                                        name=method.name,
                                        fully_qualified=method_fq,
                                    )
                                )

                    elif isinstance(node, javalang.tree.InterfaceDeclaration):
                        fully_qualified = f"interface:{node.name}"
                        symbols.append(
                            ExtractedSymbol(
                                symbol_type="interface",
                                name=node.name,
                                fully_qualified=fully_qualified,
                            )
                        )
                        # Extract method symbols within the interface
                        for method in node.methods or []:
                            if isinstance(method, javalang.tree.MethodDeclaration):
                                method_fq = f"method:{node.name}.{method.name}"
                                symbols.append(
                                    ExtractedSymbol(
                                        symbol_type="method",
                                        name=method.name,
                                        fully_qualified=method_fq,
                                    )
                                )

                    elif isinstance(node, javalang.tree.EnumDeclaration):
                        fully_qualified = f"enum:{node.name}"
                        symbols.append(
                            ExtractedSymbol(
                                symbol_type="enum",
                                name=node.name,
                                fully_qualified=fully_qualified,
                            )
                        )

                    elif isinstance(node, javalang.tree.AnnotationDeclaration):
                        fully_qualified = f"annotation:{node.name}"
                        symbols.append(
                            ExtractedSymbol(
                                symbol_type="annotation",
                                name=node.name,
                                fully_qualified=fully_qualified,
                            )
                        )
            except Exception as e:
                warnings.append(f"Symbol extraction error: {e}")

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.JAVA,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=warnings,
        )

    def _resolve_import(
        self,
        module: str,
        is_static: bool,
        is_wildcard: bool,
        raw_dep: RawDependency,
        file_index: dict[str, Path] = {},
    ) -> ResolvedDependency:
        """Resolve a Java import to a project file or classify as external/stdlib."""
        # 1. Try to resolve to a project file first
        if file_index:
            resolved_path = _resolve_import_to_path(module, file_index)
            if resolved_path is not None:
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=resolved_path,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                )

        # 2. Check stdlib
        if _is_stdlib(module) or (is_wildcard and module.startswith("java.")):
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=True,
                is_unresolved=False,
            )

        # 3. Check external (third-party)
        if _is_external(module) or is_wildcard:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=False,
            )

        # 4. Unresolved
        return ResolvedDependency(
            raw=raw_dep,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=True,
            resolution_note=f"no project file found for {module}",
        )
