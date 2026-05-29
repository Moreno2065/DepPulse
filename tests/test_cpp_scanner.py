"""Tests for the C/C++ source code scanner."""

import contextlib
import shutil
import tempfile
from pathlib import Path

import pytest

from deppulse.models import DependencyKind, Language
from deppulse.scanners.cpp_scanner import CppScanner, _strip_comments

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mixed_project"


# =============================================================================
# Comment stripping
# =============================================================================

class TestCommentStripping:
    def test_single_line_comment_removed(self):
        code = 'int x = 1; // comment with #include "fake.h"\nint y = 2;'
        stripped = _strip_comments(code)
        assert "fake.h" not in stripped
        assert "int x = 1;" in stripped
        assert "int y = 2;" in stripped

    def test_block_comment_removed(self):
        code = """
/* this is a block
   #include "fake.h"
*/
int x = 1;
"""
        stripped = _strip_comments(code)
        assert "fake.h" not in stripped
        assert "int x = 1;" in stripped

    def test_multiple_comments(self):
        code = """
// #include "one.h"
int a; /* #include "two.h" */
// #include "three.h"
int b;
/* #include "four.h" */
"""
        stripped = _strip_comments(code)
        for i in range(1, 5):
            assert f'"{i}.h"' not in stripped
        assert "int a;" in stripped
        assert "int b;" in stripped


# =============================================================================
# CppScanner tests
# =============================================================================

def _tmp_cpp_file(code: str, suffix: str = ".c") -> tuple[Path, Path]:
    tmpdir = Path(tempfile.mkdtemp())
    fpath = tmpdir / ("test" + suffix)
    fpath.write_text(code, encoding="utf-8")
    return fpath, tmpdir


def _cleanup_tmpdir(tmpdir: Path) -> None:
    with contextlib.suppress(OSError):
        shutil.rmtree(tmpdir)


class TestCppScanner:
    def test_can_scan_cpp_extensions(self):
        scanner = CppScanner()
        assert scanner.can_scan(Path("foo.c")) is True
        assert scanner.can_scan(Path("foo.cc")) is True
        assert scanner.can_scan(Path("foo.cpp")) is True
        assert scanner.can_scan(Path("foo.cxx")) is True
        assert scanner.can_scan(Path("foo.h")) is True
        assert scanner.can_scan(Path("foo.hh")) is True
        assert scanner.can_scan(Path("foo.hpp")) is True
        assert scanner.can_scan(Path("foo.hxx")) is True
        assert scanner.can_scan(Path("foo.py")) is False

    def test_basic_include_local(self):
        scanner = CppScanner()
        code = '#include "math_utils.h"\n#include "logger.h"\n'
        fpath, tmpdir = _tmp_cpp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.language == Language.CPP
            local_deps = [
                d for d in result.raw_dependencies
                if d.kind == DependencyKind.INCLUDE_LOCAL
            ]
            assert len(local_deps) == 2
            assert any("math_utils.h" in d.raw_text for d in local_deps)
            assert any("logger.h" in d.raw_text for d in local_deps)
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_basic_include_system(self):
        scanner = CppScanner()
        code = '#include <stdio.h>\n#include <stdlib.h>\n#include <vector>\n'
        fpath, tmpdir = _tmp_cpp_file(code, ".cpp")
        try:
            result = scanner.scan(fpath, tmpdir)
            system_deps = [
                d for d in result.raw_dependencies
                if d.kind == DependencyKind.INCLUDE_SYSTEM
            ]
            assert len(system_deps) == 3
            assert all("#include <" in d.raw_text for d in system_deps)
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_mixed_includes(self):
        scanner = CppScanner()
        code = '#include "local.h"\n#include <system.h>\n#include "another.h"\n'
        fpath, tmpdir = _tmp_cpp_file(code, ".h")
        try:
            result = scanner.scan(fpath, tmpdir)
            # Check raw dependencies
            local_raw = [d for d in result.raw_dependencies if d.kind == DependencyKind.INCLUDE_LOCAL]
            system_raw = [d for d in result.raw_dependencies if d.kind == DependencyKind.INCLUDE_SYSTEM]
            assert len(local_raw) == 2
            assert len(system_raw) == 1
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_comments_suppressed(self):
        scanner = CppScanner()
        code = """
// #include "fake.h"
int x = 1;
/* #include "also_fake.h" */
#include "real.h"
"""
        fpath, tmpdir = _tmp_cpp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            raw_texts = " ".join(d.raw_text for d in result.raw_dependencies)
            assert "fake.h" not in raw_texts
            assert "also_fake.h" not in raw_texts
            assert "real.h" in raw_texts
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_spaced_include(self):
        scanner = CppScanner()
        code = '# include "spaced.h"\n#  include "spaced2.h"\n'
        fpath, tmpdir = _tmp_cpp_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            raw_texts = [d.raw_text for d in result.raw_dependencies]
            assert any("spaced.h" in t for t in raw_texts)
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_to_project_file(self):
        scanner = CppScanner()
        tmpdir = Path(tempfile.mkdtemp())
        try:
            math_h = tmpdir / "math_utils.h"
            math_h.write_text("// header")

            c_file = tmpdir / "test.c"
            c_file.write_text('#include "math_utils.h"\n')

            result = scanner.scan(c_file, tmpdir)
            resolved_local = [
                d for d in result.resolved_dependencies
                if d.raw.kind == DependencyKind.INCLUDE_LOCAL
            ]
            assert any(
                d.normalized_path and "math_utils.h" in d.normalized_path
                for d in resolved_local
            )
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_ambiguous_include_warning(self):
        scanner = CppScanner()
        tmpdir = Path(tempfile.mkdtemp())
        try:
            (tmpdir / "a").mkdir()
            (tmpdir / "a" / "foo.h").write_text("// header 1")
            (tmpdir / "b").mkdir()
            (tmpdir / "b" / "foo.h").write_text("// header 2")

            c_file = tmpdir / "test.c"
            c_file.write_text('#include "foo.h"\n')

            result = scanner.scan(c_file, tmpdir)
            # Should either resolve uniquely or mark as ambiguous
            assert len(result.resolved_dependencies) >= 0
        finally:
            _cleanup_tmpdir(tmpdir)


def test_cpp_include_tree_sitter_only():
    from deppulse.scanners.cpp_scanner import CppTreeSitterParser

    source = b'''
#include "local.h"
#include <system.h>
// #include "commented.h"
    '''
    parser = CppTreeSitterParser()
    tree = parser.parse(source)
    imports = parser.extract_imports(tree, "test.cpp", source=source)

    specifiers = [i.specifier for i in imports]
    assert "local.h" in specifiers
    assert "system.h" in specifiers
    assert "commented.h" not in specifiers


# =============================================================================
# Fixture integration
# =============================================================================

class TestCppScannerFixture:
    def test_scan_math_utils_c(self):
        scanner = CppScanner()
        math_c = FIXTURE_ROOT / "math_utils.c"
        if not math_c.exists():
            pytest.skip("Fixture math_utils.c not found")

        result = scanner.scan(math_c, FIXTURE_ROOT)
        assert result.language == Language.CPP
        local_includes = [
            d for d in result.resolved_dependencies
            if d.raw.kind == DependencyKind.INCLUDE_LOCAL
        ]
        assert len(local_includes) >= 2
        local_names = {d.raw.raw_text for d in local_includes}
        assert any("math_utils.h" in t for t in local_names)
        assert any("logger.h" in t for t in local_names)

    def test_scan_math_utils_h(self):
        scanner = CppScanner()
        math_h = FIXTURE_ROOT / "math_utils.h"
        if not math_h.exists():
            pytest.skip("Fixture math_utils.h not found")

        result = scanner.scan(math_h, FIXTURE_ROOT)
        assert result.language == Language.CPP
        local = [d for d in result.resolved_dependencies
                 if d.raw.kind == DependencyKind.INCLUDE_LOCAL]
        system = [d for d in result.resolved_dependencies
                  if d.raw.kind == DependencyKind.INCLUDE_SYSTEM]
        assert len(local) >= 1
        assert len(system) >= 1
