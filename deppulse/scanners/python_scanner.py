"""Python source code scanner using the AST module."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Optional

from deppulse.models import (
    DependencyKind,
    ExtractedSymbol,
    Language,
    RawDependency,
    ResolvedDependency,
    ScanResult,
)
from deppulse.scanners.base import BaseScanner


# Standard library modules that are unlikely to be project files.
_STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names)


class PySymbolVisitor(ast.NodeVisitor):
    """AST visitor that extracts top-level function, class, and method symbols."""

    def __init__(self, module_name: str = "") -> None:
        self.module_name = module_name
        self.symbols: list[ExtractedSymbol] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if hasattr(ast, "AsyncFunctionDef") and isinstance(node, ast.AsyncFunctionDef):
            return
        fully_qualified = f"function:{node.name}"
        self.symbols.append(
            ExtractedSymbol(
                symbol_type="function",
                name=node.name,
                fully_qualified=fully_qualified,
            )
        )

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_name = f"class:{node.name}"
        self.symbols.append(
            ExtractedSymbol(
                symbol_type="class",
                name=node.name,
                fully_qualified=class_name,
            )
        )
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                method_qualified = f"method:{node.name}.{item.name}"
                self.symbols.append(
                    ExtractedSymbol(
                        symbol_type="method",
                        name=item.name,
                        fully_qualified=method_qualified,
                    )
                )
            elif hasattr(ast, "AsyncFunctionDef") and isinstance(item, ast.AsyncFunctionDef):
                method_qualified = f"method:{node.name}.{item.name}"
                self.symbols.append(
                    ExtractedSymbol(
                        symbol_type="method",
                        name=item.name,
                        fully_qualified=method_qualified,
                    )
                )
        self.generic_visit(node)


class PythonScanner(BaseScanner):
    """
    Scanner for Python source files using the `ast` module.

    Extracts:
    - Import statements: `import x`, `import x as alias`
    - From-import statements: `from x import y`, `from . import z`
    - Relative imports: `from ..pkg import mod`
    - Top-level functions, classes, and methods
    """

    name = "python"

    PY_SUFFIXES = frozenset({".py", ".pyw"})

    def can_scan(self, path: Path) -> bool:
        return path.suffix in self.PY_SUFFIXES

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: Optional[dict[str, Path]] = None,
    ) -> ScanResult:
        from deppulse.models import normalize_path_to_posix

        rel_posix = normalize_path_to_posix(str(file_path), str(project_root))
        size_bytes = 0
        content = ""

        try:
            size_bytes = file_path.stat().st_size
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = file_path.read_text(encoding="latin-1")
                except Exception as e:
                    return ScanResult(
                        file_path=rel_posix,
                        absolute_path=str(file_path),
                        language=Language.PYTHON,
                        suffix=".py",
                        size_bytes=size_bytes,
                        error=f"Failed to read file encoding: {e}",
                    )
        except OSError as e:
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.PYTHON,
                suffix=".py",
                size_bytes=0,
                error=f"OS error: {e}",
            )

        tree = None
        warnings: list[str] = []
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            warnings.append(f"Syntax error at line {e.lineno}: {e.msg}")
        except ValueError as e:
            warnings.append(f"Parse error: {e}")

        symbols: list[ExtractedSymbol] = []
        if tree is not None:
            visitor = PySymbolVisitor(module_name=rel_posix)
            try:
                visitor.visit(tree)
                symbols = visitor.symbols
            except Exception as e:
                warnings.append(f"Symbol extraction error: {e}")

        # Extract imports
        visitor2 = PyImportVisitor(file_path, project_root, file_index, rel_posix)
        if tree is not None:
            try:
                visitor2.visit(tree)
            except Exception as e:
                warnings.append(f"Import extraction error: {e}")

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.PYTHON,
            suffix=".py",
            size_bytes=size_bytes,
            raw_dependencies=visitor2.raw_deps,
            resolved_dependencies=visitor2.resolved_deps,
            symbols=symbols,
            warnings=warnings,
        )


class PyImportVisitor(ast.NodeVisitor):
    """
    AST visitor that extracts Python import statements and resolves them
    to project-relative paths.
    """

    def __init__(
        self,
        source_file: Path,
        project_root: Path,
        file_index: Optional[dict[str, Path]],
        rel_posix: str,
    ) -> None:
        self.source_file = source_file
        self.project_root = project_root
        self.file_index = file_index or {}
        self.rel_posix = rel_posix
        self.raw_deps: list[RawDependency] = []
        self.resolved_deps: list[ResolvedDependency] = []

    # ------------------------------------------------------------------
    # Import node visitors
    # ------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.lineno is not None and node.names or []:
            pass
        for alias_node in node.names:
            raw_text = f"import {alias_node.name}" + (
                f" as {alias_node.asname}" if alias_node.asname else ""
            )
            self._record_import(raw_text, alias_node.name, node.lineno or 0, level=0)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = node.module or ""
        level = node.level  # 0 = absolute, 1 = single dot, 2 = double dot, etc.

        if level > 0:
            raw_text = "from " + "." * level + (module_name or "") + " import " + ", ".join(
                a.name for a in node.names
            )
            self._record_relative_import(
                raw_text, module_name, level, node.lineno or 0, node.names
            )
        else:
            imported = ", ".join(a.name for a in node.names)
            raw_text = f"from {module_name} import {imported}"
            self._record_import(raw_text, module_name, node.lineno or 0, level=0)
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Internal resolution
    # ------------------------------------------------------------------

    def _record_import(
        self, raw_text: str, module_name: str, line_number: int, level: int
    ) -> None:
        raw_dep = RawDependency(raw_text=raw_text, kind=DependencyKind.IMPORT, line_number=line_number)
        self.raw_deps.append(raw_dep)

        resolved = self._resolve_module(module_name)
        self.resolved_deps.append(resolved)

    def _record_relative_import(
        self,
        raw_text: str,
        module_name: str,
        level: int,
        line_number: int,
        aliases: list[ast.alias],
    ) -> None:
        raw_dep = RawDependency(
            raw_text=raw_text, kind=DependencyKind.IMPORT, line_number=line_number
        )
        self.raw_deps.append(raw_dep)

        resolved = self._resolve_relative(module_name, level, aliases)
        self.resolved_deps.append(resolved)

    def _resolve_module(self, module_name: str) -> ResolvedDependency:
        """Resolve an absolute module name to a project file or classify as external."""
        from deppulse.models import normalize_path_to_posix

        if module_name in _STDLIB_MODULES:
            raw_dep = self.raw_deps[-1] if self.raw_deps else RawDependency(
                raw_text=module_name, kind=DependencyKind.IMPORT, line_number=0
            )
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=True,
                is_unresolved=False,
            )

        parts = module_name.split(".")
        if not parts:
            return self._unresolved_result(module_name, "empty module name")

        # Try package/__init__.py first, then package/module.py
        candidates = [
            "/".join(parts) + "/__init__.py",
            "/".join(parts) + ".py",
        ]

        for candidate in candidates:
            if self.file_index:
                if candidate in self.file_index:
                    raw_dep = self._get_last_raw()
                    return ResolvedDependency(
                        raw=raw_dep,
                        normalized_path=candidate,
                        is_external=False,
                        is_stdlib=False,
                        is_unresolved=False,
                    )
            else:
                direct = self.project_root / candidate
                if direct.exists():
                    raw_dep = self._get_last_raw()
                    return ResolvedDependency(
                        raw=raw_dep,
                        normalized_path=candidate,
                        is_external=False,
                        is_stdlib=False,
                        is_unresolved=False,
                    )

        return self._unresolved_result(module_name, f"no project file found for {module_name}")

    def _resolve_relative(
        self,
        module_name: str,
        level: int,
        aliases: list[ast.alias],
    ) -> ResolvedDependency:
        """Resolve a relative import (from . import x / from .. import x)."""
        from deppulse.models import normalize_path_to_posix

        # Compute the directory of the source file
        source_dir = Path(self.source_file).parent

        # Walk up `level - 1` directories from the source file's directory
        current = source_dir
        for _ in range(level - 1):
            current = current.parent

        if module_name:
            parts = module_name.split(".")
        else:
            parts = []

        # Determine the candidate package/module path
        candidate_rel: Path = current
        if parts:
            candidate_rel = current / "/".join(parts)

        if self.file_index:
            # Try exact path
            parts_posix = candidate_rel.relative_to(self.project_root)
            candidate_posix = "/".join(parts_posix.parts)

            candidates = [
                candidate_posix + "/__init__.py",
                candidate_posix + ".py",
            ]
            for c in candidates:
                if c in self.file_index:
                    raw_dep = self._get_last_raw()
                    return ResolvedDependency(
                        raw=raw_dep,
                        normalized_path=c,
                        is_external=False,
                        is_stdlib=False,
                        is_unresolved=False,
                    )

            # No match found
            return self._unresolved_result(
                module_name, f"relative import unresolved: {module_name} (level={level})"
            )
        else:
            # No file index: do direct filesystem resolution
            init_path = candidate_rel / "__init__.py"
            mod_path = candidate_rel.with_suffix(".py")

            if init_path.exists():
                raw_dep = self._get_last_raw()
                rel = normalize_path_to_posix(str(init_path), str(self.project_root))
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=rel,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                )
            if mod_path.exists():
                raw_dep = self._get_last_raw()
                rel = normalize_path_to_posix(str(mod_path), str(self.project_root))
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=rel,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                )

            return self._unresolved_result(
                module_name,
                f"relative import unresolved: {module_name} (level={level})",
            )

    def _unresolved_result(self, module_name: str, note: str) -> ResolvedDependency:
        raw_dep = self._get_last_raw()
        return ResolvedDependency(
            raw=raw_dep,
            normalized_path=None,
            is_external=True,
            is_stdlib=module_name in _STDLIB_MODULES,
            is_unresolved=True,
            resolution_note=note,
        )

    def _get_last_raw(self) -> RawDependency:
        return self.raw_deps[-1] if self.raw_deps else RawDependency(
            raw_text="", kind=DependencyKind.IMPORT, line_number=0
        )
