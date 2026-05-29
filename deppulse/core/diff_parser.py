"""DiffParser: parse git diff output to extract changed symbols with line-level precision.

Used by the test selector and risk model to determine exactly which symbols
were changed and the nature of those changes.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ChangeType(str, Enum):
    """The nature of a change to a symbol."""

    SIGNATURE = "signature"   # function/method signature changed → all callers affected
    BODY = "body"             # function body changed → direct/indirect callers affected
    NEW = "new"              # new symbol added → no upward impact
    DELETED = "deleted"      # symbol removed → callers broken
    COMMENT = "comment"      # comment/docstring only → ignored


@dataclass
class ChangedSymbol:
    """
    A symbol that was changed, with line-level precision.
    """

    file_path: str                     # project-relative POSIX path
    symbol_name: str                   # simple symbol name, e.g. "processData"
    fqn: str                           # fully-qualified name, e.g. "method:Utils.processData"
    change_type: ChangeType
    line_range: tuple[int, int]        # (start_line, end_line) 1-indexed
    old_signature: str | None = None  # old function signature text
    new_signature: str | None = None  # new function signature text
    language: str = "unknown"


@dataclass
class FileDiff:
    """
    The diff for a single file: its changed line ranges and parsed symbols.
    """

    file_path: str
    language: str = "unknown"
    changed_lines: list[tuple[int, int]] = field(default_factory=list)  # (start, end)
    changed_symbols: list[ChangedSymbol] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DiffParser
# ---------------------------------------------------------------------------


class DiffParser:
    """
    Parse git diff output to extract file-level and symbol-level changes.

    Usage
    -----
    ```python
    diff_text = subprocess.run(["git", "diff", "--unified=0"], capture_output=True).stdout
    parser = DiffParser(project_root=Path("/path/to/project"))
    file_diffs = parser.parse(diff_text)
    ```

    The parser uses heuristics based on language patterns to identify whether
    a changed line range affects a function signature vs. a function body.
    """

    def __init__(
        self,
        project_root: Path | None = None,
        file_index: dict[str, Path] | None = None,
    ) -> None:
        self.project_root = project_root
        self.file_index = file_index or {}

    def parse(self, diff_output: str) -> list[FileDiff]:
        """
        Parse git diff output and return per-file diff information.

        Parameters
        ----------
        diff_output : str
            Output of `git diff --unified=0` or similar.

        Returns
        -------
        list[FileDiff]
            One entry per file that has changes.
        """
        if not diff_output.strip():
            return []

        file_diffs: list[FileDiff] = []
        current_file: FileDiff | None = None

        lines = diff_output.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i]

            # New file header
            if line.startswith("diff --git"):
                if current_file and current_file.changed_lines:
                    file_diffs.append(current_file)
                current_file = None

            # File path in diff header
            elif line.startswith("--- "):
                path_str = line[4:].strip()
                if path_str.startswith("a/"):
                    path_str = path_str[2:]
                if path_str == "/dev/null":
                    path_str = ""
                if path_str and current_file is None:
                    current_file = FileDiff(file_path=path_str)
                    current_file.language = self._detect_language(path_str)

            # Hunk header: @@ -start,len +start,len @@
            elif line.startswith("@@") and current_file is not None:
                match = _RE_HUNK.match(line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2))
                    new_start = int(match.group(3))
                    new_count = int(match.group(4))

                    # Record the changed line range
                    current_file.changed_lines.append((
                        new_start,
                        new_start + new_count - 1,
                    ))

                    # Try to extract changed symbols
                    symbols = self._extract_changed_symbols_from_hunk(
                        current_file, lines, i + 1, new_start, old_start, old_count
                    )
                    current_file.changed_symbols.extend(symbols)

            i += 1

        # Don't forget the last file
        if current_file and current_file.changed_lines:
            file_diffs.append(current_file)

        return file_diffs

    def parse_git_command(
        self,
        project_root: Path,
        ref: str = "HEAD",
        staged: bool = False,
    ) -> list[FileDiff]:
        """
        Run `git diff` and parse the output directly.

        Parameters
        ----------
        project_root : Path
            Root of the git repository.
        ref : str
            Git ref to compare against (e.g. "HEAD", "main", "HEAD~5").
        staged : bool
            If True, diff staged changes (--cached).
        """
        args = ["git", "-C", str(project_root), "diff", "--unified=0"]
        if staged:
            args.append("--cached")
        else:
            args.extend([ref, "HEAD"])

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return self.parse(result.stdout)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def parse_unified_diff(
        self,
        diff_output: str,
        ref: str = "HEAD",
    ) -> list[FileDiff]:
        """
        Parse git diff output (alias for parse()).

        Added for API clarity — some callers use this name.
        """
        return self.parse(diff_output)

    # ------------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------------

    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        suffix = Path(file_path).suffix.lower()
        lang_map = {
            ".py": "python",
            ".java": "java",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".c": "cpp",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".cxx": "cpp",
            ".h": "cpp",
            ".hpp": "cpp",
            ".hxx": "cpp",
            ".js": "javascript",
            ".jsx": "javascript",
            ".mjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
        }
        return lang_map.get(suffix, "unknown")

    def _extract_changed_symbols_from_hunk(
        self,
        file_diff: FileDiff,
        lines: list[str],
        start_idx: int,
        new_start: int,
        old_start: int,
        old_count: int,
    ) -> list[ChangedSymbol]:
        """
        Extract symbol changes from a hunk.

        For each hunk, we look at added/changed lines and try to identify
        whether they affect a function signature vs. body.
        """
        symbols: list[ChangedSymbol] = []
        i = start_idx
        current_line = new_start
        pending_additions: list[tuple[int, str]] = []

        while i < len(lines):
            line = lines[i]
            i += 1

            # End of hunk
            if line.startswith("@@") or line.startswith("diff --git") or line.startswith("---"):
                break

            if line.startswith("+") and not line.startswith("+++"):
                content = line[1:]
                stripped = content.strip()
                if stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
                    pending_additions.append((current_line, content))

            if line.startswith(" ") or line.startswith("+"):
                current_line += 1
            elif line.startswith("-"):
                pass

        # Analyze pending additions for symbol changes
        for line_no, content in pending_additions:
            symbol = self._classify_change_line(
                file_diff.file_path, content, line_no, file_diff.language
            )
            if symbol:
                symbols.append(symbol)

        return symbols

    def _classify_change_line(
        self,
        file_path: str,
        content: str,
        line_no: int,
        language: str,
    ) -> ChangedSymbol | None:
        """Classify a single changed line as a symbol change."""
        stripped = content.strip()

        if language == "python":
            return self._classify_python_line(file_path, stripped, line_no)
        elif language in ("java", "kotlin"):
            return self._classify_java_kotlin_line(file_path, stripped, line_no, language)
        elif language in ("javascript", "typescript"):
            return self._classify_js_ts_line(file_path, stripped, line_no, language)
        elif language == "cpp":
            return self._classify_cpp_line(file_path, stripped, line_no)

        return None

    def _classify_python_line(
        self,
        file_path: str,
        content: str,
        line_no: int,
    ) -> ChangedSymbol | None:
        """Classify a Python changed line."""
        # Function definition
        match = re.match(r"^(?:async\s+)?def\s+(\w+)\s*\(", content)
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"function:{match.group(1)}",
                change_type=ChangeType.SIGNATURE,
                line_range=(line_no, line_no),
                new_signature=content,
                language="python",
            )

        # Class definition
        match = re.match(r"^class\s+(\w+)", content)
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"class:{match.group(1)}",
                change_type=ChangeType.BODY,
                line_range=(line_no, line_no),
                language="python",
            )

        # Decorator (marks next function as changed)
        if content.startswith("@"):
            return ChangedSymbol(
                file_path=file_path,
                symbol_name="",
                fqn="",
                change_type=ChangeType.BODY,
                line_range=(line_no, line_no),
                language="python",
            )

        return None

    def _classify_java_kotlin_line(
        self,
        file_path: str,
        content: str,
        line_no: int,
        language: str,
    ) -> ChangedSymbol | None:
        """Classify a Java/Kotlin changed line."""
        stripped = content.strip()

        # Method/function declaration (signature change)
        # Matches: public/private/protected [static] [final] ReturnType name(...)
        match = re.match(
            r"(?:public|private|protected|internal)?\s*"
            r"(?:static\s+)?"
            r"(?:final\s+)?"
            r"(\w+)\s+"           # return type / fun keyword
            r"(\w+)\s*\(",         # function name + (
            stripped,
        )
        if match:
            type_or_fun = match.group(1)
            name = match.group(2)

            # "fun" keyword in Kotlin
            if type_or_fun == "fun":
                return ChangedSymbol(
                    file_path=file_path,
                    symbol_name=name,
                    fqn=f"method:{name}",
                    change_type=ChangeType.SIGNATURE,
                    line_range=(line_no, line_no),
                    new_signature=stripped,
                    language=language,
                )

            # Java/Kotlin method
            if name[0].islower():  # methods start lowercase
                return ChangedSymbol(
                    file_path=file_path,
                    symbol_name=name,
                    fqn=f"method:{name}",
                    change_type=ChangeType.SIGNATURE,
                    line_range=(line_no, line_no),
                    new_signature=stripped,
                    language=language,
                )

        # Class declaration
        match = re.match(
            r"(?:public|private|protected)?\s*"
            r"(?:abstract\s+|sealed\s+|data\s+)?"
            r"(?:class|interface|enum|annotation)\s+(\w+)",
            stripped,
        )
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"class:{match.group(1)}",
                change_type=ChangeType.BODY,
                line_range=(line_no, line_no),
                language=language,
            )

        return None

    def _classify_js_ts_line(
        self,
        file_path: str,
        content: str,
        line_no: int,
        language: str,
    ) -> ChangedSymbol | None:
        """Classify a JS/TS changed line."""
        stripped = content.strip()

        # Function declaration: function name(...)
        match = re.match(r"function\s+(\w+)\s*\(", stripped)
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"function:{match.group(1)}",
                change_type=ChangeType.SIGNATURE,
                line_range=(line_no, line_no),
                new_signature=stripped,
                language=language,
            )

        # Arrow function / const/let/var: const name = (...
        match = re.match(r"(?:const|let|var)\s+(\w+)\s*=", stripped)
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"function:{match.group(1)}",
                change_type=ChangeType.BODY,
                line_range=(line_no, line_no),
                language=language,
            )

        # Class declaration
        match = re.match(r"class\s+(\w+)", stripped)
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"class:{match.group(1)}",
                change_type=ChangeType.BODY,
                line_range=(line_no, line_no),
                language=language,
            )

        # Interface/type declaration
        match = re.match(r"(?:interface|type)\s+(\w+)", stripped)
        if match:
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=match.group(1),
                fqn=f"type:{match.group(1)}",
                change_type=ChangeType.BODY,
                line_range=(line_no, line_no),
                language=language,
            )

        return None

    def _classify_cpp_line(
        self,
        file_path: str,
        content: str,
        line_no: int,
    ) -> ChangedSymbol | None:
        """Classify a C++ changed line."""
        stripped = content.strip()

        # Function definition: ReturnType name(...) { or ReturnType name(...)
        match = re.match(r"(\w+)\s+(\w+)\s*\([^)]*\)\s*(?:const)?\s*\{?", stripped)
        if match:
            ret_type = match.group(1)
            name = match.group(2)
            # Skip keywords
            if ret_type in ("if", "for", "while", "switch", "return", "class", "struct", "namespace"):
                return None
            return ChangedSymbol(
                file_path=file_path,
                symbol_name=name,
                fqn=f"function:{name}",
                change_type=ChangeType.SIGNATURE,
                line_range=(line_no, line_no),
                new_signature=stripped,
                language="cpp",
            )

        return None


_RE_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
