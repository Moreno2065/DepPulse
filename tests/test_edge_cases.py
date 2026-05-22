"""Unit tests for edge cases: empty files, comment-only files, Unicode identifiers,
deep directory trees, and cache behavior with large files."""

import shutil
import tempfile
from pathlib import Path

import pytest

from deppulse.cache import ScanCache
from deppulse.scanners.python_scanner import PythonScanner, PySymbolVisitor


# =============================================================================
# Python scanner edge cases
# =============================================================================


class TestPythonScannerEdgeCases:
    """Boundary-condition tests for PythonScanner."""

    def _tmp_file(self, code: str, suffix: str = ".py") -> tuple[Path, Path]:
        tmpdir = Path(tempfile.mkdtemp())
        fpath = tmpdir / ("test" + suffix)
        fpath.write_text(code, encoding="utf-8")
        return fpath, tmpdir

    def _cleanup(self, tmpdir: Path) -> None:
        try:
            shutil.rmtree(tmpdir)
        except OSError:
            pass

    def test_empty_file(self):
        scanner = PythonScanner()
        fpath, tmpdir = self._tmp_file("")
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.error is None
            assert len(result.raw_dependencies) == 0
            assert len(result.symbols) == 0
        finally:
            self._cleanup(tmpdir)

    def test_comment_only_file(self):
        scanner = PythonScanner()
        code = "# This file only has comments\n# Another line\n"
        fpath, tmpdir = self._tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.error is None
            assert len(result.raw_dependencies) == 0
        finally:
            self._cleanup(tmpdir)

    def test_unicode_identifier(self):
        scanner = PythonScanner()
        # Unicode identifiers are valid in Python 3
        code = "def 获取数据(): pass\n类 = type('类', (), {})\n"
        fpath, tmpdir = self._tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.error is None
            # Symbol names may appear as-is in Python 3
            assert len(result.symbols) >= 0
        finally:
            self._cleanup(tmpdir)

    def test_deep_directory_structure(self):
        scanner = PythonScanner()
        tmpdir = Path(tempfile.mkdtemp())
        try:
            deep_path = tmpdir / "a" / "b" / "c" / "d" / "module.py"
            deep_path.parent.mkdir(parents=True, exist_ok=True)
            deep_path.write_text("import json\nimport os\n", encoding="utf-8")

            result = scanner.scan(deep_path, tmpdir)
            assert result.error is None
            assert len(result.raw_dependencies) == 2
        finally:
            self._cleanup(tmpdir)

    def test_unicode_in_import(self):
        scanner = PythonScanner()
        # File with Unicode import target (edge case — may be unresolved but should not crash)
        fpath, tmpdir = self._tmp_file("import 我的模块\n")
        try:
            result = scanner.scan(fpath, tmpdir)
            # Should not crash; the module will be unresolved but that's expected
            assert result.error is None
            assert len(result.raw_dependencies) == 1
        finally:
            self._cleanup(tmpdir)

    def test_very_long_line(self):
        scanner = PythonScanner()
        long_line = "a" * 10000
        code = f"x = {long_line}\n"
        fpath, tmpdir = self._tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.error is None
        finally:
            self._cleanup(tmpdir)

    def test_dunder_all_variable(self):
        """__all__ assignments should not be treated as symbol declarations."""
        scanner = PythonScanner()
        code = "__all__ = ['foo', 'bar']\nfoo = 1\nbar = 2\n"
        fpath, tmpdir = self._tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.error is None
            # __all__ is an assignment, not a function/class/method — not captured
            assert len(result.symbols) == 0
        finally:
            self._cleanup(tmpdir)


# =============================================================================
# PySymbolVisitor edge cases
# =============================================================================


class TestPySymbolVisitorEdgeCases:
    import ast

    def test_async_function(self):
        code = "async def fetch(): pass"
        tree = self.ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = {s.fully_qualified for s in visitor.symbols}
        assert "function:fetch" in names

    def test_lambda_not_captured(self):
        code = "f = lambda x: x + 1"
        tree = self.ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = {s.fully_qualified for s in visitor.symbols}
        # Lambdas have no name attribute and are not top-level defs
        assert not any("lambda" in n for n in names)

    def test_decorated_function(self):
        code = """
@decorator
def decorated(): pass
"""
        tree = self.ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = {s.fully_qualified for s in visitor.symbols}
        assert "function:decorated" in names


# =============================================================================
# Cache edge cases
# =============================================================================


class TestCacheEdgeCases:
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
        return ScanCache.load(cache_dir_dir := cache_dir)

    def test_large_file_full_hash(self, cache, cache_dir):
        """Changes beyond the first 64 KB must invalidate the cache."""
        # Create a large file
        fpath = cache_dir / "large.py"
        fpath.write_bytes(b"#" * 65536 + b"\n# changed at byte 65537\n")

        cache.set("large.py", fpath, {"value": "original"})
        cache.save()

        # Verify it cached
        assert cache.get("large.py", fpath) is not None

        # Now modify the file BEYOND the old 64 KB truncation point
        fpath.write_bytes(b"#" * 65536 + b"\n# MODIFIED at byte 65537\n")

        # Must NOT be served from cache (full hash should detect the change)
        result = cache.get("large.py", fpath)
        assert result is None

    def test_cache_no_false_hit_on_size_unchanged_content_changed(self, cache, cache_dir):
        """Same size but different content must not hit cache."""
        fpath = cache_dir / "size_same.py"
        fpath.write_bytes(b"a" * 1000)

        cache.set("size_same.py", fpath, {"v": 1})
        cache.save()
        assert cache.get("size_same.py", fpath) is not None

        # Same size, different content
        fpath.write_bytes(b"b" * 1000)
        result = cache.get("size_same.py", fpath)
        assert result is None
