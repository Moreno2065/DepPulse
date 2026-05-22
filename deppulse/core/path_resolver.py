"""PathResolver: shared utility for resolving import specifiers to project file paths.

Used by all scanners to resolve module paths to project-relative POSIX paths.
Handles Python, Java, Kotlin, C++, JavaScript, and TypeScript resolution strategies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Optional


# ---------------------------------------------------------------------------
# Built-in Node.js stdlib modules (abridged — covers common ones)
# ---------------------------------------------------------------------------

_NODE_STDLIB: frozenset[str] = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster",
    "console", "constants", "crypto", "dgram", "dns", "domain",
    "events", "fs", "http", "http2", "https", "inspector", "module",
    "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "trace_events", "tty", "url", "util",
    "v8", "vm", "wasi", "worker_threads", "zlib",
    # Browser globals (partial)
    "window", "document", "navigator", "fetch", "console", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval", "Promise", "Map", "Set",
    "WeakMap", "WeakSet", "Symbol", "Array", "Object", "Function",
    "Boolean", "Number", "String", "Date", "RegExp", "Error", "JSON",
    "Math", "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURI",
    "decodeURI", "encodeURIComponent", "decodeURIComponent",
    # Common packages that are always external
    "react", "react-dom", "vue", "angular", "lodash", "underscore",
    "axios", "fetch", "node-fetch", "jquery", "moment", "dayjs",
})


# ---------------------------------------------------------------------------
# PathResolver
# ---------------------------------------------------------------------------


class PathResolver:
    """
    Shared path resolution utility for all language scanners.

    Maintains a file index (project-relative POSIX path → absolute Path)
    and provides language-specific resolution strategies.

    Parameters
    ----------
    project_root : Path
        Absolute path to the project root.
    file_index : dict[str, Path], optional
        Pre-built mapping of project-relative POSIX path → absolute Path.
        Built automatically from a scan when not provided.
    """

    def __init__(
        self,
        project_root: Path,
        file_index: Optional[dict[str, Path]] = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.file_index: dict[str, Path] = file_index or {}
        self._tsconfig_cache: Optional[dict] = None
        self._tsconfig_paths: Optional[dict[str, list[str]]] = None
        self._package_json_cache: Optional[dict] = None
        self._package_exports: Optional[dict] = None

    # -- Index management --

    def add_file(self, rel_posix: str, abs_path: Path) -> None:
        """Add a file to the resolver's index."""
        self.file_index[rel_posix] = abs_path

    def add_files(self, mapping: dict[str, Path]) -> None:
        """Bulk-add files to the resolver's index."""
        self.file_index.update(mapping)

    # -- Core resolution methods --

    def resolve_relative(
        self,
        from_file: str,
        specifier: str,
    ) -> Optional[str]:
        """
        Resolve a relative import specifier from a source file.

        e.g. from_file="src/utils/helper.py", specifier="../core/base.py"
             → "src/core/base.py"

        Returns a project-relative POSIX path, or None if unresolved.
        """
        from_path = PurePosixPath(from_file)
        source_dir = from_path.parent

        # Normalize: go up levels with "..", then resolve the rest
        parts = specifier.split("/")
        up_count = 0
        rest: list[str] = []

        for part in parts:
            if part == "..":
                up_count += 1
            elif part == ".":
                continue
            else:
                rest.append(part)

        # Walk up from source directory
        current = source_dir
        for _ in range(up_count):
            current = current.parent

        resolved = current.joinpath(*rest) if rest else current

        # Convert to POSIX path string (relative to project root)
        try:
            abs_resolved = (self.project_root / resolved).resolve()
            rel = abs_resolved.relative_to(self.project_root)
            posix = str(PurePosixPath(*rel.parts))
        except ValueError:
            return None

        # Check if the resolved path exists in the file index
        candidates = [
            posix,
            posix + "/__init__.py",
            posix + "/__init__.ts",
            posix + "/__init__.js",
        ]

        # Also try with common extensions
        for ext in (".py", ".kt", ".java", ".ts", ".tsx", ".js", ".jsx"):
            if not posix.endswith(ext):
                candidates.append(posix + ext)

        for candidate in candidates:
            if candidate in self.file_index:
                return candidate

        # Check filesystem directly
        abs_candidate = self.project_root / posix
        if abs_candidate.exists() and abs_candidate.is_file():
            return posix

        return None

    def resolve_absolute(
        self,
        module_path: str,
        language: str,
    ) -> Optional[str]:
        """
        Resolve an absolute module path to a project-relative file path.

        Language-specific strategies:
        - python: package/__init__.py or package/module.py
        - java: com/example/Utils.java or com/example/Utils/__init__.java
        - kotlin: com/example/Utils.kt or com/example/Utils/__init__.kt
        - javascript/typescript: resolve against package.json exports, tsconfig paths

        Returns a project-relative POSIX path, or None if unresolved.
        """
        if language == "python":
            return self._resolve_python(module_path)
        elif language in ("java", "kotlin"):
            return self._resolve_java_kotlin(module_path, language)
        elif language == "javascript":
            return self._resolve_javascript(module_path)
        elif language == "typescript":
            return self._resolve_typescript(module_path)
        elif language == "cpp":
            return self._resolve_cpp(module_path)
        return None

    # -- Python --

    def _resolve_python(self, module_name: str) -> Optional[str]:
        """Resolve a Python module name to a project-relative path."""
        parts = module_name.strip().split(".")
        if not parts or not parts[0]:
            return None

        candidates = [
            "/".join(parts) + "/__init__.py",
            "/".join(parts) + ".py",
        ]

        for candidate in candidates:
            if candidate in self.file_index:
                return candidate
            abs_candidate = self.project_root / candidate.replace("/", os.sep)
            if abs_candidate.exists():
                return candidate

        return None

    # -- Java / Kotlin --

    def _resolve_java_kotlin(self, fqn: str, language: str) -> Optional[str]:
        """
        Resolve a fully-qualified Java/Kotlin class name to a project-relative path.
        Uses package root discovery: walks src/main/java, src/main/kotlin.
        """
        ext = ".java" if language == "java" else ".kt"
        path_segments = fqn.replace(".", "/")
        candidates = [
            path_segments + ext,
            path_segments + "/__init__" + ext,
        ]

        for candidate in candidates:
            if candidate in self.file_index:
                return candidate

            abs_candidate = self.project_root / candidate.replace("/", os.sep)
            if abs_candidate.exists():
                return candidate

        return None

    # -- JavaScript --

    def _resolve_javascript(self, module_name: str) -> Optional[str]:
        """Resolve a JavaScript/Node.js module specifier."""
        # Built-in Node.js stdlib
        root_name = module_name.split("/")[0].split("@")[0]
        if root_name in _NODE_STDLIB:
            return None  # external/stdlib

        # Check package.json exports map if available
        if self._package_exports:
            resolved = self._resolve_package_exports(module_name, self._package_exports)
            if resolved:
                return resolved

        # Try common JS patterns
        candidates = [
            module_name + "/index.js",
            module_name + ".js",
            "node_modules/" + module_name + "/index.js",
            "node_modules/" + module_name + ".js",
        ]

        for candidate in candidates:
            if candidate in self.file_index:
                return candidate

        # Try package.json main field
        if self._package_json_cache:
            main = self._package_json_cache.get("main", "")
            if main:
                main_path = (self.project_root / main).resolve()
                try:
                    rel = main_path.relative_to(self.project_root)
                    return str(PurePosixPath(*rel.parts))
                except ValueError:
                    pass

        return None

    def _resolve_typescript(self, module_name: str) -> Optional[str]:
        """Resolve a TypeScript module specifier using tsconfig paths or JS rules."""
        # Try tsconfig paths aliases first
        if self._tsconfig_paths:
            for pattern, targets in self._tsconfig_paths.items():
                # Simple pattern matching: "src/*" → ["src/"]
                if pattern.endswith("/*"):
                    prefix = pattern[:-2]
                    suffix = module_name
                    for target in targets:
                        if suffix.startswith(prefix):
                            remainder = suffix[len(prefix):]
                            candidate = target.rstrip("/") + remainder
                            if candidate in self.file_index:
                                return candidate
                            # Try with common TS extensions
                            for ext in (".ts", ".tsx", "/index.ts", "/index.tsx"):
                                cand = candidate + ext
                                if cand in self.file_index:
                                    return cand

        # Fall back to JS resolution
        return self._resolve_javascript(module_name)

    # -- C++ --

    def _resolve_cpp(self, include_text: str) -> Optional[str]:
        """Resolve a C++ #include path."""
        # Already resolved by the scanner using filesystem search
        # This is a no-op here — included for completeness
        normalized = include_text.replace("\\", "/")

        if normalized in self.file_index:
            return normalized

        # Try basename search
        basename = PurePosixPath(normalized).name
        for proj_rel in self.file_index:
            if PurePosixPath(proj_rel).name == basename:
                return proj_rel

        return None

    # -- Package resolution helpers --

    def _resolve_package_exports(
        self,
        module_name: str,
        exports: dict,
    ) -> Optional[str]:
        """Resolve using package.json exports field."""
        # Handle conditional exports
        resolved = exports.get(module_name)
        if isinstance(resolved, str):
            return self._resolve_javascript(resolved)

        if isinstance(resolved, dict):
            # Try specific conditions: node, import, default
            for cond in ("node", "import", "default"):
                if cond in resolved:
                    val = resolved[cond]
                    if isinstance(val, str):
                        return self._resolve_javascript(val)

        return None

    def load_tsconfig(self, project_root: Optional[Path] = None) -> None:
        """Load tsconfig.json paths mapping for alias resolution."""
        root = project_root or self.project_root
        tsconfig_path = root / "tsconfig.json"

        if not tsconfig_path.exists():
            self._tsconfig_paths = {}
            return

        try:
            data = json.loads(tsconfig_path.read_text(encoding="utf-8"))
            compiler_opts = data.get("compilerOptions", {})
            paths = compiler_opts.get("paths", {})
            self._tsconfig_paths = paths
        except (json.JSONDecodeError, OSError):
            self._tsconfig_paths = {}

    def load_package_json(self, project_root: Optional[Path] = None) -> None:
        """Load nearest package.json for main/exports resolution."""
        root = project_root or self.project_root
        pkg_path = root / "package.json"

        if not pkg_path.exists():
            self._package_json_cache = {}
            self._package_exports = {}
            return

        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
            self._package_json_cache = data
            self._package_exports = data.get("exports", {})
        except (json.JSONDecodeError, OSError):
            self._package_json_cache = {}
            self._package_exports = {}

    # -- Classification helpers --

    def is_external(self, module_name: str, language: str) -> bool:
        """Return True if the module is an external (third-party) package."""
        if language == "python":
            return not self._is_python_stdlib(module_name) and not self._is_python_project(module_name)
        elif language in ("java", "kotlin"):
            return self._is_java_external(module_name)
        elif language in ("javascript", "typescript"):
            return self._is_js_external(module_name)
        elif language == "cpp":
            return self._is_cpp_external(module_name)
        return True

    def is_stdlib(self, module_name: str, language: str) -> bool:
        """Return True if the module is a standard library."""
        if language == "python":
            return self._is_python_stdlib(module_name)
        elif language in ("java", "kotlin"):
            return self._is_java_stdlib(module_name)
        elif language in ("javascript", "typescript"):
            return self._is_js_stdlib(module_name)
        elif language == "cpp":
            return self._is_cpp_stdlib(module_name)
        return False

    # -- Private classification helpers --

    @staticmethod
    def _is_python_stdlib(module: str) -> bool:
        import sys
        root = module.split(".")[0]
        return root in sys.stdlib_module_names

    def _is_python_project(self, module: str) -> bool:
        return self._resolve_python(module) is not None

    @staticmethod
    def _is_java_stdlib(module: str) -> bool:
        return module.startswith("java.") or module.startswith("javax.")

    @staticmethod
    def _is_java_external(module: str) -> bool:
        prefixes = ("org.", "com.", "android.", "io.", "net.", "uk.", "de.", "fr.", "ru.", "cn.")
        return module.startswith(prefixes)

    @staticmethod
    def _is_js_stdlib(module: str) -> bool:
        root = module.split("/")[0].split("@")[0]
        return root in _NODE_STDLIB

    @staticmethod
    def _is_js_external(module: str) -> bool:
        root = module.split("/")[0].split("@")[0]
        if root in _NODE_STDLIB:
            return False
        # Scoped packages (@scope/name), node_modules prefix
        return module.startswith("@") or root.startswith("node:")

    @staticmethod
    def _is_cpp_stdlib(module: str) -> bool:
        stdlibs = ("cstddef", "cstdint", "cstdlib", "cstring", "cctype",
                   "cerrno", "cfloat", "climits", "clocale", "cmath",
                   "csetjmp", "csignal", "cstdarg", "cstdio", "cstdlib",
                   "cstring", "ctime", "cwchar", "cwctype",
                   "assert.h", "ctype.h", "errno.h", "float.h",
                   "limits.h", "locale.h", "math.h", "setjmp.h",
                   "signal.h", "stdarg.h", "stddef.h", "stdio.h",
                   "stdlib.h", "string.h", "time.h", "wchar.h", "wctype.h",
                   "iostream", "fstream", "sstream", "string",
                   "vector", "list", "deque", "array", "forward_list",
                   "map", "unordered_map", "set", "unordered_set",
                   "stack", "queue", "priority_queue",
                   "memory", "memory_resource", "scoped_allocator",
                   "functional", "algorithm", "iterator", "numeric",
                   "thread", "mutex", "atomic", "future", "condition_variable",
                   "filesystem", "regex", "chrono", "random",
                   "typeindex", "type_traits", "utility", "tuple",
                   "optional", "variant", "any", "initializer_list")
        return any(module.startswith(s) for s in stdlibs)

    @staticmethod
    def _is_cpp_external(module: str) -> bool:
        # Angle-bracket includes with known third-party patterns
        third_party = ("boost/", "fmt/", "spdlog/", "gtest/", "gmock/",
                       "openssl/", "sqlite3/", "curl/", "json/",
                       "nlohmann/", "yaml-cpp/", "catch2/")
        return any(module.startswith(s) for s in third_party)
