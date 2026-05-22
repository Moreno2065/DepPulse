"""C/C++ source code scanner using regex-based include directive extraction."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from deppulse.models import (
    DependencyKind,
    Language,
    RawDependency,
    ResolvedDependency,
    ScanResult,
)
from deppulse.scanners.base import BaseScanner


# Supported C/C++ file extensions.
CPP_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"})

# Common include directories to search when resolving local includes.
_COMMON_INCLUDE_DIRS = frozenset({"include", "src", "lib", "inc"})

# Regex: matches #include "..." or #include <...>, optionally with a space after hash.
# Handles: #include "foo.h", # include "foo.h", #  include <foo.h>
_RE_INCLUDE = re.compile(
    r"^[ \t]*#[ \t]*include[ \t]*([<\"][^>\"]+[>\"])",
    re.MULTILINE,
)

# Regex: removes // single-line comments.
_RE_SINGLELINE_COMMENT = re.compile(r"//.*$", re.MULTILINE)

# Regex: removes /* ... */ block comments (non-nested).
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Remove C++ // and /* */ comments from source text before scanning."""
    text = _RE_SINGLELINE_COMMENT.sub("", text)
    text = _RE_BLOCK_COMMENT.sub("", text)
    return text


def _find_comment_and_string_spans(text: str) -> list[tuple[int, int, str]]:
    """
    Return all (start, end, type) spans for comments and string literals in C++ source.
    type is one of "line_comment", "block_comment", "single_string", "double_string", "raw_string".
    Spans are non-overlapping and sorted.
    """
    spans: list[tuple[int, int, str]] = []

    # Line comments: //
    for m in _RE_SINGLELINE_COMMENT.finditer(text):
        spans.append((m.start(), m.end(), "line_comment"))

    # Block comments: /* */
    for m in _RE_BLOCK_COMMENT.finditer(text):
        spans.append((m.start(), m.end(), "block_comment"))

    # Double-quoted strings (track escape sequences)
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    spans.append((i, j + 1, "double_string"))
                    j += 1
                    break
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            i = j
        else:
            i += 1

    # Single-quoted strings
    i = 0
    while i < n:
        if text[i] == "'":
            j = i + 1
            while j < n and text[j] != "'":
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j < n:
                spans.append((i, j + 1, "single_string"))
                j += 1
            i = j
        else:
            i += 1

    # Raw strings: R"delim(...)delim"
    i = 0
    while i < n:
        if text[i] == "R" and i + 2 < n and text[i + 1] == '"':
            j = i + 2
            delim_start = ""
            while j < n and text[j] != "(":
                delim_start += text[j]
                j += 1
            if j < n and text[j] == "(":
                close = f')"{delim_start}"'
                close_pos = text.find(close, j)
                if close_pos >= 0:
                    spans.append((i, close_pos + len(close), "raw_string"))
                    i = close_pos + len(close)
                    continue
        i += 1

    # Sort and merge overlapping spans (prefer comments over strings)
    spans.sort(key=lambda s: s[0])
    merged: list[tuple[int, int, str]] = []
    for start, end, stype in spans:
        if merged and start <= merged[-1][1]:
            # Overlap: keep the one that is a comment (comment takes precedence)
            if "comment" not in stype and "comment" in merged[-1][2]:
                continue  # discard string span that overlaps a comment
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2])
        else:
            merged.append((start, end, stype))
    return merged


def _is_in_span(pos: int, spans: list[tuple[int, int, str]]) -> bool:
    import bisect

    class SpanSentinel:
        def __init__(self, idx: int): self.idx = idx
        def __lt__(self, other: tuple[int, int, str]) -> bool: return self.idx < other[0]

    i = bisect.bisect_right(spans, SpanSentinel(pos)) - 1
    if i >= 0:
        s, e, _ = spans[i]
        return s <= pos < e
    return False


class CppScanner(BaseScanner):
    """
    Scanner for C/C++ source files using regex-based include directive extraction.

    Extracts:
    - #include "local.h"  -> treated as local/project candidate
    - #include <system.h>  -> treated as external/system

    Limitations:
    - Does not perform macro expansion or preprocessor preprocessing.
    - Does not resolve headers through compiler include paths (uses filesystem search).
    - Ambiguous includes (multiple files with the same basename) are left unresolved
      with a warning rather than guessed.
    """

    name = "cpp"

    def can_scan(self, path: Path) -> bool:
        return path.suffix.lower() in CPP_EXTENSIONS

    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] = {},
    ) -> ScanResult:
        from deppulse.models import normalize_path_to_posix

        rel_posix = normalize_path_to_posix(str(file_path), str(project_root))
        suffix = file_path.suffix.lower()

        size_bytes = 0
        try:
            size_bytes = file_path.stat().st_size
            raw_content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            return ScanResult(
                file_path=rel_posix,
                absolute_path=str(file_path),
                language=Language.CPP,
                suffix=suffix,
                size_bytes=0,
                error=f"OS error reading file: {e}",
            )

        # Build sorted span list from original content (for binary-search filtering)
        all_spans = _find_comment_and_string_spans(raw_content)

        raw_deps: list[RawDependency] = []
        resolved_deps: list[ResolvedDependency] = []
        warnings: list[str] = []

        for match in _RE_INCLUDE.finditer(raw_content):
            match_start = match.start()
            match_end = match.end()

            # Skip if the # is inside a comment or the filename is inside a string
            if _is_in_span(match_start, all_spans):
                continue

            raw_text = match.group(0).strip()
            is_quoted = match.group(1)[0] == '"'
            include_text = match.group(1)[1:-1].strip()
            line_number = raw_content[:match_start].count("\n") + 1

            kind = DependencyKind.INCLUDE_LOCAL if is_quoted else DependencyKind.INCLUDE_SYSTEM
            raw_dep = RawDependency(
                raw_text=raw_text,
                kind=kind,
                line_number=line_number,
            )
            raw_deps.append(raw_dep)

            if is_quoted:
                resolved = self._resolve_local_include(
                    include_text, file_path, project_root, file_index
                )
                resolved_deps.append(resolved)
                if resolved.is_unresolved and "multiple" in resolved.resolution_note:
                    warnings.append(
                        f"Line {line_number}: ambiguous include '{include_text}': "
                        f"{resolved.resolution_note}"
                    )
            else:
                # Angle-bracket includes are external/system by default
                resolved_deps.append(
                    ResolvedDependency(
                        raw=raw_dep,
                        normalized_path=None,
                        is_external=True,
                        is_stdlib=False,
                        is_unresolved=False,
                    )
                )

        return ScanResult(
            file_path=rel_posix,
            absolute_path=str(file_path),
            language=Language.CPP,
            suffix=suffix,
            size_bytes=size_bytes,
            raw_dependencies=raw_deps,
            resolved_dependencies=resolved_deps,
            symbols=[],  # C++ symbol extraction not implemented yet
            warnings=warnings,
        )

    def _resolve_local_include(
        self,
        include_text: str,
        source_file: Path,
        project_root: Path,
        file_index: Optional[dict[str, Path]],
    ) -> ResolvedDependency:
        """
        Resolve a quote-include (e.g. "utils/helper.h") to a project-relative path.

        Search order:
        1. Relative to the source file's directory.
        2. Relative to the project root.
        3. Inside common include directories (include/, src/, lib/, inc/).
        4. Global basename search across the project (warns on ambiguity).
        """
        from deppulse.models import normalize_path_to_posix

        # Normalize path separators
        include_normalized = include_text.replace("\\", "/")
        raw_dep = RawDependency(
            raw_text=include_text,
            kind=DependencyKind.INCLUDE_LOCAL,
            line_number=0,
        )

        # Strategy 1: relative to source file directory
        if "/" in include_normalized or "\\" in include_text:
            rel_path = source_file.parent / include_text
            if rel_path.exists() and rel_path.is_file():
                rel = normalize_path_to_posix(str(rel_path), str(project_root))
                return ResolvedDependency(
                    raw=raw_dep,
                    normalized_path=rel,
                    is_external=False,
                    is_stdlib=False,
                    is_unresolved=False,
                )

        # Strategy 2: relative to project root
        root_path = project_root / include_text
        if root_path.exists() and root_path.is_file():
            rel = normalize_path_to_posix(str(root_path), str(project_root))
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=rel,
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
            )

        # Strategy 3: search in common include directories
        basename = Path(include_normalized).name
        matches: list[str] = []

        if file_index:
            for proj_rel, abs_path in file_index.items():
                if Path(proj_rel).name == basename:
                    matches.append(proj_rel)
        else:
            # Fallback: walk project tree
            for root_dir, _dirs, filenames in os.walk(project_root):
                for fn in filenames:
                    if Path(fn).name == basename:
                        full = Path(root_dir) / fn
                        rel = normalize_path_to_posix(str(full), str(project_root))
                        matches.append(rel)

        if not matches:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=None,
                is_external=True,
                is_stdlib=False,
                is_unresolved=True,
                resolution_note=f"header '{include_text}' not found in project",
            )

        if len(matches) == 1:
            return ResolvedDependency(
                raw=raw_dep,
                normalized_path=matches[0],
                is_external=False,
                is_stdlib=False,
                is_unresolved=False,
            )

        # Multiple matches: ambiguous
        return ResolvedDependency(
            raw=raw_dep,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=True,
            resolution_note=f"multiple matches: {', '.join(matches)}",
        )
