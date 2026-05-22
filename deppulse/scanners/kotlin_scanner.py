"""Kotlin source code scanner using regex-based extraction (javalang does not support Kotlin)."""

from __future__ import annotations

import re
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


_RE_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*$", re.MULTILINE)
_RE_IMPORT = re.compile(r"^\s*import\s+([\w.*]+)\s*$", re.MULTILINE)

# Kotlin declaration patterns
_RE_CLASS = re.compile(
    r"^\s*(?:class|interface|object|annotation\s+class)\s+(\w+)",
    re.MULTILINE,
)
_RE_FUNC = re.compile(
    r"\bfun\s+(\w+)\s*(?:[<({]|$)",
    re.MULTILINE,
)
_RE_PROPERTY = re.compile(
    r"^\s*(?:val|var)\s+(\w+)",
    re.MULTILINE,
)


def _is_stdlib(module: str) -> bool:
    """Return True if the import looks like a Kotlin/Java standard library."""
    return module.startswith("kotlin.") or module.startswith("java.") or module.startswith("javax.")


def _is_external(module: str) -> bool:
    """Return True if the import looks like an external/third-party library."""
    prefixes = ("org.", "com.", "android.", "io.", "net.")
    return module.startswith(prefixes)


def _resolve_import_to_path(module: str, file_index: dict[str, Path]) -> Optional[str]:
    """
    Convert a fully-qualified Kotlin module name to a project-relative path.

    e.g. com.example.utils -> com/example/utils.kt or com/example/utils/__init__.kt
    """
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


def _is_comment_or_string_line(line: str, line_start: int, comment_ranges: list[tuple[int, int]]) -> bool:
    """Check if a character position is inside a comment or string literal."""
    for start, end in comment_ranges:
        if start <= line_start < end:
            return True
    return False


def _extract_symbols_regex(content: str) -> list[ExtractedSymbol]:
    """
    Extract Kotlin symbol declarations using regex.
    Tracks class context via brace counting.

    When inside a class body (represented by class_name_stack containing entries),
    any function/property is considered a class member.
    """
    symbols: list[ExtractedSymbol] = []
    content = _STRIP_BLOCK_COMMENTS_RE.sub("", content)
    lines = content.split("\n")

    DEPTH_MARKER = object()
    class_name_stack: list[object] = []

    def current_class() -> str | None:
        for name in reversed(class_name_stack):
            if name is not DEPTH_MARKER:
                return name  # type: ignore[return-value]
        return None

    def in_class_body() -> bool:
        return len(class_name_stack) > 0

    def process_line(stripped: str) -> None:
        nonlocal class_name_stack

        # --- Phase 1: depth changes ---
        closes = stripped.count("}")
        for _ in range(closes):
            if class_name_stack:
                class_name_stack.pop()

        # --- Phase 2: declarations ---
        cc = current_class()
        inside_class = in_class_body()

        # Class/interface/object/annotation class
        class_match = _RE_CLASS.search(stripped)
        if class_match:
            class_name = class_match.group(1)
            symbols.append(ExtractedSymbol(
                symbol_type="class", name=class_name,
                fully_qualified=f"class:{class_name}",
            ))
            class_name_stack.append(class_name)

        # Function
        func_match = _RE_FUNC.search(stripped)
        if func_match:
            func_name = func_match.group(1)
            if inside_class:
                # We're inside a class body; use the enclosing class name if known
                fully_qualified = f"method:{cc}.{func_name}" if cc else f"function:{func_name}"
                symbol_type = "method"
            else:
                fully_qualified = f"function:{func_name}"
                symbol_type = "function"
            symbols.append(ExtractedSymbol(
                symbol_type=symbol_type, name=func_name,
                fully_qualified=fully_qualified,
            ))

        # Property
        prop_match = _RE_PROPERTY.search(stripped)
        if prop_match:
            prop_name = prop_match.group(1)
            if inside_class:
                fully_qualified = f"property:{cc}.{prop_name}" if cc else f"property:{prop_name}"
            else:
                fully_qualified = f"property:{prop_name}"
            symbols.append(ExtractedSymbol(
                symbol_type="property", name=prop_name,
                fully_qualified=fully_qualified,
            ))

        # --- Phase 3: opening braces increase depth ---
        opens = stripped.count("{")
        for _ in range(opens):
            class_name_stack.append(DEPTH_MARKER)

    for line in lines:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("//"):
            process_line(stripped)

    return symbols


_STRIP_BLOCK_COMMENTS_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


class KotlinScanner(BaseScanner):
    """
    Scanner for Kotlin source files using regex-based extraction.

    Since javalang does not support Kotlin, this scanner uses regex patterns
    for package, import, and declaration extraction.

    Extracts:
    - Package declaration
    - Import statements
    - Class, interface, object, function, and property declarations
    """

    name = "kotlin"

    KOTLIN_SUFFIXES = frozenset({".kt", ".kts"})

    def can_scan(self, path: Path) -> bool:
        return path.suffix in self.KOTLIN_SUFFIXES

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
                language=Language.KOTLIN,
                suffix=suffix,
                size_bytes=0,
                error=f"OS error reading file: {e}",
            )

        # Extract package
        package_match = _RE_PACKAGE.search(content)
        package_name: Optional[str] = package_match.group(1) if package_match else None

        # Extract imports
        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []

        for match in _RE_IMPORT.finditer(content):
            module = match.group(1)
            line_number = content[: match.start()].count("\n") + 1
            raw_text = f"import {module}"
            raw_dep = RawDependency(
                raw_text=raw_text,
                kind=DependencyKind.KOTLIN_IMPORT,
                line_number=line_number,
            )
            raw_deps.append(raw_dep)

            resolved = self._resolve_import(module, file_index, raw_dep)
            resolved_deps.append(resolved)

        # Extract symbols
        symbols: list[ExtractedSymbol] = []
        try:
            symbols = _extract_symbols_regex(content)
        except Exception:
            pass  # Symbol extraction is best-effort

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.KOTLIN,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=symbols,
            warnings=[],
        )

    def _resolve_import(
        self,
        module: str,
        file_index: dict[str, Path],
        raw_dep: RawDependency,
    ) -> ResolvedDependency:
        """Resolve a Kotlin import to a project file or classify as external/stdlib."""
        # Try to resolve to a project file first
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

        # Check stdlib
        if _is_stdlib(module):
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=True,
                is_unresolved=False,
            )

        # Check external (third-party)
        if _is_external(module):
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
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
            resolution_note=f"no project file found for {module}",
        )
