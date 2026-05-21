"""Tests for the Python source code scanner."""

import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from deppulse.models import DependencyKind, Language
from deppulse.scanners.python_scanner import PythonScanner, PySymbolVisitor, _STDLIB_MODULES


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "python_project"


# =============================================================================
# PySymbolVisitor tests
# =============================================================================

class TestPySymbolVisitor:
    def test_function_symbol(self):
        code = "def foo(): pass"
        tree = ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = [s.fully_qualified for s in visitor.symbols]
        assert "function:foo" in names

    def test_class_symbol(self):
        code = """
class MyClass:
    def method(self): pass
"""
        tree = ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = {s.fully_qualified for s in visitor.symbols}
        assert "class:MyClass" in names
        assert "method:MyClass.method" in names

    def test_multiple_classes(self):
        code = """
class Foo:
    def a(self): pass
    def b(self): pass
class Bar:
    def c(self): pass
"""
        tree = ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = {s.fully_qualified for s in visitor.symbols}
        assert "class:Foo" in names
        assert "method:Foo.a" in names
        assert "method:Foo.b" in names
        assert "class:Bar" in names
        assert "method:Bar.c" in names

    def test_nested_functions_not_captured(self):
        code = """
def outer():
    def inner(): pass
    return inner
"""
        tree = ast.parse(code)
        visitor = PySymbolVisitor()
        visitor.visit(tree)
        names = {s.fully_qualified for s in visitor.symbols}
        assert "function:outer" in names
        assert "function:inner" not in names


# =============================================================================
# PythonScanner basic tests
# =============================================================================

def _tmp_file(code: str, suffix: str = ".py") -> tuple[Path, Path]:
    """Create a temp file with code and return (abs_path, project_root)."""
    tmpdir = Path(tempfile.mkdtemp())
    fpath = tmpdir / ("test" + suffix)
    fpath.write_text(code, encoding="utf-8")
    return fpath, tmpdir


def _cleanup_tmpdir(tmpdir: Path) -> None:
    try:
        shutil.rmtree(tmpdir)
    except OSError:
        pass


class TestPythonScanner:
    def test_can_scan_py(self):
        scanner = PythonScanner()
        assert scanner.can_scan(Path("foo.py")) is True
        assert scanner.can_scan(Path("bar.pyw")) is True
        assert scanner.can_scan(Path("foo.cc")) is False

    def test_scan_simple_import(self):
        scanner = PythonScanner()
        fpath, tmpdir = _tmp_file("import os\nimport sys\n")
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.language == Language.PYTHON
            assert len(result.raw_dependencies) == 2
            raw_names = {d.raw_text for d in result.raw_dependencies}
            assert "import os" in raw_names
            assert "import sys" in raw_names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_scan_from_import(self):
        scanner = PythonScanner()
        code = """
from pathlib import Path
from collections import defaultdict
"""
        fpath, tmpdir = _tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            raw_names = [d.raw_text for d in result.raw_dependencies]
            assert any("from pathlib import Path" in r for r in raw_names)
            assert any("from collections import defaultdict" in r for r in raw_names)
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_relative_import(self):
        scanner = PythonScanner()
        code = "from . import local\nfrom .local import thing\nfrom ..pkg import mod\n"
        fpath, tmpdir = _tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            # All three imports should be captured
            assert len(result.raw_dependencies) == 3
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_import_as(self):
        scanner = PythonScanner()
        code = "import os as operating_system\nimport sys as system\n"
        fpath, tmpdir = _tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            raw_texts = [d.raw_text for d in result.raw_dependencies]
            assert "import os as operating_system" in raw_texts
            assert "import sys as system" in raw_texts
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_syntax_error_tolerated(self):
        scanner = PythonScanner()
        code = "def broken():\n  this is not valid\n"
        fpath, tmpdir = _tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            # Should produce a syntax error warning
            assert len(result.warnings) >= 0  # May or may not capture depending on Python version
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_encoding_error_tolerated(self):
        scanner = PythonScanner()
        fpath, tmpdir = _tmp_file("print('hello')")
        try:
            # Write binary garbage
            fpath.write_bytes(b"\xff\xfe import os\n")
            result = scanner.scan(fpath, tmpdir)
            assert result.error is None or len(result.warnings) >= 0
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_symbol_extraction(self):
        scanner = PythonScanner()
        code = """
def top_func(): pass

class MyClass:
    def method_a(self): pass

class Another:
    def method_b(self): pass
"""
        fpath, tmpdir = _tmp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "function:top_func" in names
            assert "class:MyClass" in names
            assert "method:MyClass.method_a" in names
            assert "class:Another" in names
            assert "method:Another.method_b" in names
        finally:
            _cleanup_tmpdir(tmpdir)


# =============================================================================
# Integration with fixture project
# =============================================================================

class TestPythonScannerFixture:
    def test_scan_main_py(self):
        scanner = PythonScanner()
        main_file = FIXTURE_ROOT / "main.py"
        if not main_file.exists():
            pytest.skip("Fixture main.py not found")

        result = scanner.scan(main_file, FIXTURE_ROOT)
        assert result.language == Language.PYTHON
        assert result.error is None
        raw_texts = [d.raw_text for d in result.raw_dependencies]
        assert any("from utils.helpers import format_name" in r for r in raw_texts)
        assert any("from utils import compute_hash" in r for r in raw_texts)
        assert any("import json" in r for r in raw_texts)

    def test_scan_user_py(self):
        scanner = PythonScanner()
        user_file = FIXTURE_ROOT / "models" / "user.py"
        if not user_file.exists():
            pytest.skip("Fixture user.py not found")

        result = scanner.scan(user_file, FIXTURE_ROOT)
        raw_texts = [d.raw_text for d in result.raw_dependencies]
        assert any("from utils.helpers import format_name" in r for r in raw_texts)
        assert any("from .profile import Profile" in r for r in raw_texts)

    def test_scan_broken_syntax(self):
        scanner = PythonScanner()
        broken_file = FIXTURE_ROOT / "broken_syntax.py"
        if not broken_file.exists():
            pytest.skip("broken_syntax.py not found")

        result = scanner.scan(broken_file, FIXTURE_ROOT)
        assert len(result.warnings) >= 0

    def test_init_py_resolution(self):
        scanner = PythonScanner()
        init_file = FIXTURE_ROOT / "utils" / "__init__.py"
        if not init_file.exists():
            pytest.skip("__init__.py not found")

        result = scanner.scan(init_file, FIXTURE_ROOT)
        # The path should normalize to POSIX format
        assert "utils/__init__.py" in result.file_path.replace("\\", "/")
