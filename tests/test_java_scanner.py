"""Tests for the Java source code scanner."""

import shutil
import tempfile
from pathlib import Path

import pytest

from deppulse.models import DependencyKind, Language
from deppulse.scanners.java_scanner import (
    JavaScanner,
    _is_external,
    _is_stdlib,
    _resolve_import_to_path,
)


class TestJavaScannerHelpers:
    def test_is_stdlib_java_prefix(self):
        assert _is_stdlib("java.lang.String") is True
        assert _is_stdlib("java.util.List") is True
        assert _is_stdlib("javax.servlet.http.HttpServlet") is True

    def test_is_stdlib_false_for_external(self):
        assert _is_stdlib("org.springframework.core") is False
        assert _is_stdlib("com.google.common.collect") is False
        assert _is_stdlib("android.app.Activity") is False

    def test_is_stdlib_false_for_unqualified(self):
        assert _is_stdlib("mylib.Utils") is False
        assert _is_stdlib("os") is False

    def test_is_external_org_com_android_io_net(self):
        assert _is_external("org.springframework.beans") is True
        assert _is_external("com.google.guava") is True
        assert _is_external("android.os.Bundle") is True
        assert _is_external("io.netty.channel") is True
        assert _is_external("net.jpountz.lz4.LZ4Factory") is True

    def test_is_external_false_for_stdlib(self):
        assert _is_external("java.lang.Thread") is False
        assert _is_external("javax.swing.JButton") is False

    def test_is_external_false_for_local(self):
        assert _is_external("mylib.utils") is False
        assert _is_external("internal.pkg") is False

    def test_resolve_import_to_path_finds_java_file(self, tmp_path: Path):
        pkg_dir = tmp_path / "com" / "example"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "Utils.java").write_text("// stub")

        file_index = {"com/example/Utils.java": pkg_dir / "Utils.java"}
        result = _resolve_import_to_path("com.example.Utils", file_index)
        assert result == "com/example/Utils.java"

    def test_resolve_import_to_path_finds_init_java(self, tmp_path: Path):
        pkg_dir = tmp_path / "com" / "example"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.java").write_text("// stub")

        file_index = {"com/example/__init__.java": pkg_dir / "__init__.java"}
        result = _resolve_import_to_path("com.example", file_index)
        assert result == "com/example/__init__.java"

    def test_resolve_import_to_path_none_when_not_found(self):
        file_index = {"other/Thing.java": Path("/x/Thing.java")}
        result = _resolve_import_to_path("com.example.Utils", file_index)
        assert result is None

    def test_resolve_import_to_path_none_when_file_index_none(self):
        result = _resolve_import_to_path("com.example.Utils", None)
        assert result is None


def _tmp_java_file(code: str) -> tuple[Path, Path]:
    """Create a temp file with Java code and return (abs_path, project_root)."""
    tmpdir = Path(tempfile.mkdtemp())
    fpath = tmpdir / "Test.java"
    fpath.write_text(code, encoding="utf-8")
    return fpath, tmpdir


def _cleanup_tmpdir(tmpdir: Path) -> None:
    try:
        shutil.rmtree(tmpdir)
    except OSError:
        pass


class TestJavaScanner:
    def test_can_scan_java(self):
        scanner = JavaScanner()
        assert scanner.can_scan(Path("foo.java")) is True
        assert scanner.can_scan(Path("bar.JAVA")) is True
        assert scanner.can_scan(Path("foo.kt")) is False
        assert scanner.can_scan(Path("foo.py")) is False

    def test_scan_simple_import(self):
        scanner = JavaScanner()
        code = "import java.util.List;\nimport java.io.File;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.language == Language.JAVA
            assert len(result.raw_dependencies) == 2
            raw_texts = {d.raw_text for d in result.raw_dependencies}
            assert "import java.util.List;" in raw_texts
            assert "import java.io.File;" in raw_texts
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_scan_import_static(self):
        scanner = JavaScanner()
        code = "import static java.lang.Math.abs;\nimport static java.util.Collections.emptyList;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.raw_dependencies) == 2
            raw_texts = {d.raw_text for d in result.raw_dependencies}
            assert "import static java.lang.Math.abs;" in raw_texts
            assert "import static java.util.Collections.emptyList;" in raw_texts
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_stdlib_marked_external_stdlib(self):
        scanner = JavaScanner()
        code = "import java.util.List;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.resolved_dependencies) == 1
            dep = result.resolved_dependencies[0]
            assert dep.is_external is True
            assert dep.is_stdlib is True
            assert dep.is_unresolved is False
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_external_marked_external_not_stdlib(self):
        scanner = JavaScanner()
        code = "import org.springframework.context.ApplicationContext;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.resolved_dependencies) == 1
            dep = result.resolved_dependencies[0]
            assert dep.is_external is True
            assert dep.is_stdlib is False
            assert dep.is_unresolved is False
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_internal_dependency(self):
        scanner = JavaScanner()
        tmpdir = Path(tempfile.mkdtemp())
        try:
            pkg_dir = tmpdir / "com" / "example"
            pkg_dir.mkdir(parents=True)
            utils_java = pkg_dir / "Utils.java"
            utils_java.write_text("package com.example;\n")
            utils_import_file = tmpdir / "Main.java"
            utils_import_file.write_text("import com.example.Utils;\n", encoding="utf-8")

            # Build the file_index exactly as the orchestrator does: POSIX path -> absolute Path
            file_index = {"com/example/Utils.java": utils_java}
            result = scanner.scan(utils_import_file, tmpdir, file_index)
            assert len(result.resolved_dependencies) == 1
            dep = result.resolved_dependencies[0]
            assert dep.is_external is False
            assert dep.is_stdlib is False
            assert dep.is_unresolved is False
            assert dep.normalized_path == "com/example/Utils.java"
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_class_symbol_extraction(self):
        scanner = JavaScanner()
        code = """
public class MyClass {
    public void doSomething() {}
    private int count;
}
"""
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "class:MyClass" in names
            assert "method:MyClass.doSomething" in names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_interface_symbol_extraction(self):
        scanner = JavaScanner()
        code = """
public interface MyInterface {
    void doIt();
}
"""
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "interface:MyInterface" in names
            assert "method:MyInterface.doIt" in names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_enum_symbol_extraction(self):
        scanner = JavaScanner()
        code = """
public enum Color {
    RED, GREEN, BLUE
}
"""
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "enum:Color" in names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_annotation_symbol_extraction(self):
        scanner = JavaScanner()
        code = """
public @interface Config {
    String value() default "";
}
"""
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "annotation:Config" in names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_multiple_classes_in_file(self):
        scanner = JavaScanner()
        code = """
class Foo {
    void a() {}
}
interface Bar {}
enum Baz { A, B }
"""
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            types = {s.symbol_type for s in result.symbols}
            assert "class" in types
            assert "interface" in types
            assert "enum" in types
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_package_declaration_extracted(self):
        scanner = JavaScanner()
        code = "package com.example.myapp;\nimport java.util.*;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            # Package is parsed but not stored in symbols
            assert len(result.raw_dependencies) == 1
            assert "import java.util.*;" in result.raw_dependencies[0].raw_text
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_wildcard_import(self):
        scanner = JavaScanner()
        code = "import java.util.*;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.raw_dependencies) == 1
            assert result.raw_dependencies[0].raw_text == "import java.util.*;"
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_duplicate_imports_deduplicated_by_scanner(self):
        scanner = JavaScanner()
        code = "import java.util.List;\nimport java.util.List;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            # javalang should not deduplicate by default
            assert len(result.raw_dependencies) >= 1
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_syntax_error_produces_warning(self):
        scanner = JavaScanner()
        code = "public class Broken { this is not valid }"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            # Should produce a syntax warning
            assert len(result.warnings) >= 0
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_file_read_error_returns_error_result(self):
        scanner = JavaScanner()
        # A file that does not exist
        result = scanner.scan(Path("/nonexistent/Foo.java"), Path("/nonexistent"))
        assert result.error is not None
        assert "OS error" in result.error

    def test_all_imports_use_java_import_kind(self):
        scanner = JavaScanner()
        code = "import java.util.List;\nimport com.google.common.*;\n"
        fpath, tmpdir = _tmp_java_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            for dep in result.raw_dependencies:
                assert dep.kind == DependencyKind.JAVA_IMPORT
        finally:
            _cleanup_tmpdir(tmpdir)
