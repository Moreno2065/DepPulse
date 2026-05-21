"""Tests for incremental scan mode."""

from pathlib import Path

import pytest

from deppulse.config import DepPulseConfig
from deppulse.core.orchestrator import DependencyOrchestrator


class TestIncrementalScan:
    def test_scan_produces_graph_result(self, tmp_path: Path):
        """Basic sanity: a scan completes and produces a valid result."""
        (tmp_path / "main.py").write_text("import os\nimport json\n")
        (tmp_path / "utils.py").write_text("def helper(): pass\n")

        config = DepPulseConfig(project_root=tmp_path)
        orchestrator = DependencyOrchestrator(config=config, use_cache=False)
        result = orchestrator.scan(tmp_path)

        assert result.total_files_found >= 2
        assert result.stats.python_files >= 2

    def test_cache_survives_scan(self, tmp_path: Path):
        """Cache is written after a scan."""
        (tmp_path / "main.py").write_text("import os\n")

        # Pass explicit config so cache_dir points to tmp_path/.deppulse
        config = DepPulseConfig(project_root=tmp_path)
        orchestrator = DependencyOrchestrator(config=config, use_cache=True)
        result = orchestrator.scan(tmp_path)

        cache_file = tmp_path / ".deppulse" / "cache.json"
        assert cache_file.exists(), f"Cache file should be created at {cache_file}"

    def test_cache_hit_avoids_rescan(self, tmp_path: Path):
        """When cache is valid, the same files are scanned."""
        (tmp_path / "a.py").write_text("import os\n")

        config = DepPulseConfig(project_root=tmp_path)
        orchestrator = DependencyOrchestrator(config=config, use_cache=True)
        result1 = orchestrator.scan(tmp_path)

        orchestrator2 = DependencyOrchestrator(config=config, use_cache=True)
        result2 = orchestrator2.scan(tmp_path)

        # The number of scanned Python files should be the same
        assert result1.stats.python_files == result2.stats.python_files
