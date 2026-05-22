"""Configuration management for DepPulse."""

from __future__ import annotations

import fnmatch
import json
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Default configuration values
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ruff",
    ".venv",
    "venv",
    "env",
    "ENV",
    "node_modules",
    "build",
    "dist",
    "target",
    ".idea",
    ".vscode",
    ".vs",
    ".eggs",
    "*.egg-info",
})

DEFAULT_IGNORE_FILES: frozenset[str] = frozenset({
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.min.js",
    "*.min.css",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "pip-lock.json",
    "composer.lock",
})

DEFAULT_INCLUDE_DIRS: frozenset[str] = frozenset({"include", "src", "lib", "inc"})

DEFAULT_RISK_THRESHOLDS: dict[str, int] = {
    "high_threshold": 70,
    "medium_threshold": 30,
}

DEFAULT_TEST_PATTERNS: frozenset[str] = frozenset({
    "test_*.py",
    "*_test.py",
    "Test*.java",
    "*Test.java",
    "*Spec.kt",
    "*Tests.kt",
    "*.spec.js",
    "*.spec.ts",
    "*.spec.jsx",
    "*.spec.tsx",
    "*.test.js",
    "*.test.ts",
    "*.test.jsx",
    "*.test.tsx",
})

DEFAULT_TEST_DIRS: frozenset[str] = frozenset({
    "tests",
    "test",
    "spec",
    "src/test",
    "src/tests",
    "__tests__",
})


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class DepPulseConfig:
    """
    Runtime configuration for DepPulse.

    Loaded from deppulse.json at the project root if present,
    otherwise defaults are used.
    """

    project_root: Path
    ignore_dirs: frozenset[str] = field(default_factory=lambda: DEFAULT_IGNORE_DIRS.copy())
    ignore_files: frozenset[str] = field(default_factory=lambda: DEFAULT_IGNORE_FILES.copy())
    include_dirs: frozenset[str] = field(default_factory=lambda: DEFAULT_INCLUDE_DIRS.copy())
    risk_high_threshold: int = 70
    risk_medium_threshold: int = 30
    cache_enabled: bool = True
    cache_dir: Path = field(init=False)
    scanner_timeout_seconds: int = 30
    max_impact_chains: int = 50
    max_file_size_kb: int = 512  # skip files larger than this
    test_patterns: frozenset[str] = field(default_factory=lambda: DEFAULT_TEST_PATTERNS.copy())
    test_dirs: frozenset[str] = field(default_factory=lambda: DEFAULT_TEST_DIRS.copy())
    hotspot_cache_path: Path = field(init=False)
    risk_weights: "RiskWeights | None" = field(default=None, init=False)

    # Internally tracked
    _config_file: Optional[Path] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir = self.project_root / ".deppulse"
        self.hotspot_cache_path = self.project_root / ".deppulse" / "hotspot-cache.json"

    @classmethod
    def from_path(cls, project_root: Path) -> "DepPulseConfig":
        """
        Load configuration from a deppulse.json file at project_root,
        or return defaults.
        """
        config_file = project_root / "deppulse.json"
        instance = cls(project_root=project_root)
        instance._config_file = config_file

        if not config_file.exists():
            return instance

        try:
            data: dict[str, Any] = json.loads(config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            warnings.warn(f"Failed to parse {config_file}, using defaults.")
            return instance

        risk = data.get("risk", {})
        instance.risk_high_threshold = risk.get("high_threshold", DEFAULT_RISK_THRESHOLDS["high_threshold"])
        instance.risk_medium_threshold = risk.get("medium_threshold", DEFAULT_RISK_THRESHOLDS["medium_threshold"])

        ignore_dirs = data.get("ignore_dirs")
        if isinstance(ignore_dirs, list):
            instance.ignore_dirs = frozenset(ignore_dirs)

        ignore_files = data.get("ignore_files")
        if isinstance(ignore_files, list):
            instance.ignore_files = frozenset(ignore_files)

        include_dirs = data.get("include_dirs")
        if isinstance(include_dirs, list):
            instance.include_dirs = frozenset(include_dirs)

        test_patterns = data.get("test_patterns")
        if isinstance(test_patterns, list):
            instance.test_patterns = frozenset(test_patterns)

        test_dirs = data.get("test_dirs")
        if isinstance(test_dirs, list):
            instance.test_dirs = frozenset(test_dirs)

        # Load risk weights from config
        risk_weights = data.get("risk", {}).get("weights", {})
        if risk_weights:
            try:
                from deppulse.core.risk import RiskWeights
                instance.risk_weights = RiskWeights.from_dict(risk_weights)
            except Exception:
                pass

        return instance

    def should_ignore_dir(self, name: str) -> bool:
        """Return True if a directory should be skipped during scanning."""
        if name in self.ignore_dirs:
            return True
        for pattern in self.ignore_dirs:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def should_ignore_file(self, name: str) -> bool:
        """Return True if a file should be skipped during scanning."""
        for pattern in self.ignore_files:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def is_test_file(self, path: str) -> bool:
        """Return True if `path` matches any test pattern or is inside a test directory."""
        name = os.path.basename(path)
        # Strategy 1: filename pattern
        for pattern in self.test_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        # Strategy 2: in a test directory
        parts = PurePosixPath(path.replace("\\", "/")).parts
        for part in parts:
            if part in self.test_dirs:
                return True
        return False
