"""Tests for the cache module."""

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from deppulse.cache import ScanCache


class TestScanCache:
    @pytest.fixture
    def cache_dir(self):
        tmp = Path(tempfile.mkdtemp())
        yield tmp
        try:
            shutil.rmtree(tmp)
        except OSError:
            pass

    @pytest.fixture
    def cache(self, cache_dir):
        return ScanCache.load(cache_dir)

    def _tmp_file(self, cache_dir: Path, name: str, content: bytes = b"print('hello')") -> Path:
        """Create a temp file for cache testing."""
        fpath = cache_dir / name
        fpath.write_bytes(content)
        return fpath

    def test_empty_cache_on_new_dir(self, cache_dir):
        cache = ScanCache.load(cache_dir)
        assert len(cache.entries) == 0

    def test_cache_miss_returns_none(self, cache, cache_dir):
        fpath = self._tmp_file(cache_dir, "test.py")
        result = cache.get("foo.py", fpath)
        assert result is None

    def test_cache_set_and_get(self, cache, cache_dir):
        fpath = self._tmp_file(cache_dir, "test.py", b"print('hello')")
        cache.set("test.py", fpath, {"symbols": [], "deps": []})
        cache.save()
        assert "test.py" in cache.entries
        result = cache.get("test.py", fpath)
        assert result is not None
        assert result["symbols"] == []

    def test_cache_invalidated_on_mtime_change(self, cache, cache_dir):
        fpath = self._tmp_file(cache_dir, "test.py", b"print('hello')")
        cache.set("test.py", fpath, {"value": 42})
        cache.save()
        assert cache.get("test.py", fpath) is not None
        # Touch the file to change mtime
        time.sleep(0.05)
        fpath.touch()
        result = cache.get("test.py", fpath)
        assert result is None

    def test_cache_clear(self, cache, cache_dir):
        fpath = self._tmp_file(cache_dir, "test.py", b"x = 1")
        cache.set("test.py", fpath, {"v": 1})
        cache.save()
        assert len(cache.entries) == 1
        cache.clear()
        assert len(cache.entries) == 0

    def test_cache_get_stats(self, cache, cache_dir):
        stats = cache.get_stats()
        assert "entries" in stats
        assert "size_kb" in stats

    def test_cache_corrupt_json_ignored(self, cache_dir):
        cache_file = cache_dir / "cache.json"
        cache_file.write_text("{ broken json }")
        cache = ScanCache.load(cache_dir)
        assert len(cache.entries) == 0
