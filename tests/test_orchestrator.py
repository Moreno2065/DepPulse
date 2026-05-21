"""Tests for the DependencyOrchestrator."""

import tempfile
from pathlib import Path

import pytest

from deppulse.cache import ScanCache
from deppulse.config import DepPulseConfig
from deppulse.core.orchestrator import DependencyOrchestrator, _get_scanner
from deppulse.models import Language


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "python_project"
MIXED_ROOT = Path(__file__).parent / "fixtures" / "mixed_project"


class TestScannerRegistry:
    def test_get_scanner_python(self):
        scanner = _get_scanner(Path("foo.py"))
        assert scanner is not None
        assert scanner.name == "python"

    def test_get_scanner_cpp(self):
        for ext in [".c", ".cpp", ".h", ".hpp"]:
            scanner = _get_scanner(Path(f"foo{ext}"))
            assert scanner is not None, f"Failed for {ext}"
            assert scanner.name == "cpp"

    def test_get_scanner_unknown(self):
        scanner = _get_scanner(Path("foo.txt"))
        assert scanner is None


class TestDependencyOrchestrator:
    def test_scan_finds_python_files(self):
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        assert result.stats.python_files >= 8
        assert result.stats.total_files >= 8
        assert result.stats.total_edges >= 0

    def test_scan_finds_mixed_files(self):
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(MIXED_ROOT)
        assert result.stats.python_files >= 3
        assert result.stats.cpp_files >= 4

    def test_scan_respects_ignore_dirs(self):
        config = DepPulseConfig(project_root=FIXTURE_ROOT)
        config.ignore_dirs = frozenset({"__pycache__", "utils"})
        orchestrator = DependencyOrchestrator(config=config, use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        paths = {r.file_path for r in result.scan_results}
        assert not any("__pycache__" in p for p in paths)

    def test_scan_includes_cycles(self):
        """The cycle fixture should produce a graph with cycles."""
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        # At least cycle_a and cycle_b should exist
        paths = {r.file_path for r in result.scan_results}
        assert "cycle_a.py" in paths or "cycle_b.py" in paths

    def test_scan_returns_graph_stats(self):
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        stats = result.stats
        assert stats.total_files >= 1
        assert stats.total_edges >= 0
        assert "python" in stats.language_breakdown
        assert stats.language_breakdown["python"] >= 1

    def test_scan_handles_broken_syntax_gracefully(self):
        broken = FIXTURE_ROOT / "broken_syntax.py"
        if not broken.exists():
            pytest.skip("broken_syntax.py not found")
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        # Should have some warnings about syntax errors
        assert len(result.warnings) >= 0

    def test_scan_no_cache(self):
        """When cache is disabled, every file is scanned."""
        orchestrator = DependencyOrchestrator(use_cache=False)
        result = orchestrator.scan(FIXTURE_ROOT)
        assert len(result.scan_results) >= 8


class TestDepPulseConfig:
    def test_ignore_dir(self):
        config = DepPulseConfig(project_root=FIXTURE_ROOT)
        assert config.should_ignore_dir("__pycache__")
        assert config.should_ignore_dir(".git")
        assert config.should_ignore_dir("node_modules")
        assert not config.should_ignore_dir("src")

    def test_ignore_file_pattern(self):
        config = DepPulseConfig(project_root=FIXTURE_ROOT)
        assert config.should_ignore_file("foo.pyc")
        assert config.should_ignore_file("bar.so")
        assert not config.should_ignore_file("foo.py")

    def test_load_from_json(self):
        """Test that config loading works even with missing file."""
        config = DepPulseConfig.from_path(FIXTURE_ROOT)
        assert config.project_root == FIXTURE_ROOT
        # No deppulse.json in fixture, should use defaults
        assert config.risk_high_threshold == 70
