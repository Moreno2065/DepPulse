"""Tests for the Kotlin source code scanner."""

import shutil
import tempfile
from pathlib import Path

import pytest

from deppulse.models import DependencyKind, Language
from deppulse.scanners.kotlin_scanner import (
    KotlinScanner,
    _extract_symbols_regex,
    _is_external,
    _is_stdlib,
    _resolve_import_to_path,
)


class TestKotlinScannerHelpers:
    def test_is_stdlib_kotlin_java_javax(self):
        assert _is_stdlib("kotlin.collections.List") is True
        assert _is_stdlib("kotlin.io.println") is True
        assert _is_stdlib("java.util.Map") is True
        assert _is_stdlib("javax.swing.JFrame") is True

    def test_is_stdlib_false_for_external(self):
        assert _is_stdlib("org.jetbrains.kotlin") is False
        assert _is_stdlib("com.google.gson") is False

    def test_is_stdlib_false_for_local(self):
        assert _is_stdlib("com.example.utils") is False
        assert _is_stdlib("myapp") is False

    def test_is_external_org_com_android_io_net(self):
        assert _is_external("org.jetbrains.kotlin") is True
        assert _is_external("com.google.gson") is True
        assert _is_external("android.os.Bundle") is True
        assert _is_external("io.ktor.core") is True
        assert _is_external("net.jpountz.lz4") is True

    def test_is_external_false_for_stdlib(self):
        assert _is_external("kotlin.text") is False
        assert _is_external("java.lang.Thread") is False

    def test_resolve_import_to_path_finds_kt_file(self, tmp_path: Path):
        pkg_dir = tmp_path / "com" / "example"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "Utils.kt").write_text("// stub")

        file_index = {"com/example/Utils.kt": pkg_dir / "Utils.kt"}
        result = _resolve_import_to_path("com.example.Utils", file_index)
        assert result == "com/example/Utils.kt"

    def test_resolve_import_to_path_finds_kts_file(self, tmp_path: Path):
        pkg_dir = tmp_path / "com" / "example"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "Utils.kts").write_text("// stub")

        file_index = {"com/example/Utils.kts": pkg_dir / "Utils.kts"}
        result = _resolve_import_to_path("com.example.Utils", file_index)
        assert result == "com/example/Utils.kts"

    def test_resolve_import_to_path_finds_init_kt(self, tmp_path: Path):
        pkg_dir = tmp_path / "com" / "example"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.kt").write_text("// stub")

        file_index = {"com/example/__init__.kt": pkg_dir / "__init__.kt"}
        result = _resolve_import_to_path("com.example", file_index)
        assert result == "com/example/__init__.kt"

    def test_resolve_import_to_path_none_when_not_found(self):
        file_index = {"other/Thing.kt": Path("/x/Thing.kt")}
        result = _resolve_import_to_path("com.example.Utils", file_index)
        assert result is None

    def test_resolve_import_to_path_none_when_file_index_none(self):
        result = _resolve_import_to_path("com.example.Utils", None)
        assert result is None


class TestExtractSymbolsRegex:
    def test_simple_function(self):
        code = "fun hello() { println(\"hi\") }"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "function:hello" in names

    def test_class_symbol(self):
        code = "class MyService { fun run() {} }"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:MyService" in names

    def test_method_inside_class(self):
        code = "class MyService {\n    fun run() {}\n}"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:MyService" in names
        assert "method:MyService.run" in names

    def test_interface_symbol(self):
        code = "interface Callback { fun invoke() }"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:Callback" in names

    def test_object_symbol(self):
        code = "object Logger { fun log(msg: String) {} }"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:Logger" in names

    def test_annotation_class(self):
        code = "annotation class Config(val key: String)"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:Config" in names

    def test_property_top_level(self):
        code = "val PI = 3.14\nfun main() {}"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "property:PI" in names
        assert "function:main" in names

    def test_property_inside_class(self):
        code = "class Person {\n    val name: String = \"\"\n    fun greet() {}\n}"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:Person" in names
        assert "property:Person.name" in names
        assert "method:Person.greet" in names

    def test_multiple_classes(self):
        code = "class Foo {\n    fun a() {}\n}\nclass Bar {\n    fun b() {}\n}"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:Foo" in names
        assert "method:Foo.a" in names
        assert "class:Bar" in names
        assert "method:Bar.b" in names

    def test_nested_class_simple(self):
        code = "class Outer {\n    class Inner {\n        fun innerMethod() {}\n    }\n}"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "class:Outer" in names
        assert "class:Inner" in names
        assert "method:Inner.innerMethod" in names

    def test_comments_ignored(self):
        code = "// fun commentedOut() {}\nfun actual() {}"
        symbols = _extract_symbols_regex(code)
        names = {s.fully_qualified for s in symbols}
        assert "function:actual" in names


def _tmp_kotlin_file(code: str, suffix: str = ".kt") -> tuple[Path, Path]:
    """Create a temp file with Kotlin code and return (abs_path, project_root)."""
    tmpdir = Path(tempfile.mkdtemp())
    fpath = tmpdir / ("Test" + suffix)
    fpath.write_text(code, encoding="utf-8")
    return fpath, tmpdir


def _cleanup_tmpdir(tmpdir: Path) -> None:
    try:
        shutil.rmtree(tmpdir)
    except OSError:
        pass


class TestKotlinScanner:
    def test_can_scan_kt(self):
        scanner = KotlinScanner()
        assert scanner.can_scan(Path("foo.kt")) is True
        assert scanner.can_scan(Path("bar.kts")) is True
        assert scanner.can_scan(Path("foo.java")) is False
        assert scanner.can_scan(Path("foo.py")) is False

    def test_scan_simple_import(self):
        scanner = KotlinScanner()
        code = "import java.util.List\nimport kotlin.jvm.JvmStatic\n"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.language == Language.KOTLIN
            assert len(result.raw_dependencies) == 2
            raw_texts = {d.raw_text for d in result.raw_dependencies}
            assert "import java.util.List" in raw_texts
            assert "import kotlin.jvm.JvmStatic" in raw_texts
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_stdlib_marked_external_stdlib(self):
        scanner = KotlinScanner()
        code = "import java.util.List\nimport kotlin.collections.map\n"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.resolved_dependencies) == 2
            kotlin_dep = next(d for d in result.resolved_dependencies if "kotlin" in d.raw.raw_text)
            java_dep = next(d for d in result.resolved_dependencies if "java" in d.raw.raw_text)
            assert kotlin_dep.is_stdlib is True
            assert java_dep.is_stdlib is True
            assert kotlin_dep.is_external is True
            assert java_dep.is_external is True
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_external_marked_external_not_stdlib(self):
        scanner = KotlinScanner()
        code = "import com.google.gson.Gson\nimport org.jetbrains.kotlin.gradle.tasks.Task\n"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.resolved_dependencies) == 2
            for dep in result.resolved_dependencies:
                assert dep.is_external is True
                assert dep.is_stdlib is False
                assert dep.is_unresolved is False
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_resolved_internal_dependency(self):
        scanner = KotlinScanner()
        tmpdir = Path(tempfile.mkdtemp())
        try:
            pkg_dir = tmpdir / "com" / "example"
            pkg_dir.mkdir(parents=True)
            utils_java = pkg_dir / "Utils.kt"
            utils_java.write_text("package com.example\n")
            utils_import_file = tmpdir / "Main.kt"
            utils_import_file.write_text("import com.example.Utils\n", encoding="utf-8")

            # Build file_index: POSIX path -> absolute path
            file_index = {"com/example/Utils.kt": utils_java}
            result = scanner.scan(utils_import_file, tmpdir, file_index)
            assert len(result.resolved_dependencies) == 1
            dep = result.resolved_dependencies[0]
            assert dep.is_external is False
            assert dep.is_stdlib is False
            assert dep.is_unresolved is False
            assert dep.normalized_path == "com/example/Utils.kt"
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_wildcard_import(self):
        scanner = KotlinScanner()
        code = "import java.util.*\nimport com.google.common.*\n"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.raw_dependencies) == 2
            raw_texts = {d.raw_text for d in result.raw_dependencies}
            assert "import java.util.*" in raw_texts
            assert "import com.google.common.*" in raw_texts
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_all_imports_use_kotlin_import_kind(self):
        scanner = KotlinScanner()
        code = "import java.util.List\nimport com.google.common.*\n"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            for dep in result.raw_dependencies:
                assert dep.kind == DependencyKind.KOTLIN_IMPORT
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_kts_suffix_detected(self):
        scanner = KotlinScanner()
        code = "import java.io.File\n"
        fpath, tmpdir = _tmp_kotlin_file(code, suffix=".kts")
        try:
            result = scanner.scan(fpath, tmpdir)
            assert result.language == Language.KOTLIN
            assert result.suffix == ".kts"
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_file_read_error_returns_error_result(self):
        scanner = KotlinScanner()
        result = scanner.scan(Path("/nonexistent/Foo.kt"), Path("/nonexistent"))
        assert result.error is not None
        assert "OS error" in result.error

    def test_symbol_extraction_class_and_functions(self):
        scanner = KotlinScanner()
        code = "class UserService {\n    fun findById(id: Long) = 42\n    fun findAll() = emptyList<Any>()\n}\nfun standalone() {}"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "class:UserService" in names
            assert "method:UserService.findById" in names
            assert "method:UserService.findAll" in names
            assert "function:standalone" in names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_annotation_class_detection(self):
        scanner = KotlinScanner()
        code = "annotation class Fancy(val value: String)"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            names = {s.fully_qualified for s in result.symbols}
            assert "class:Fancy" in names
        finally:
            _cleanup_tmpdir(tmpdir)

    def test_no_package_declaration_handled(self):
        scanner = KotlinScanner()
        code = "import java.util.List\n"
        fpath, tmpdir = _tmp_kotlin_file(code)
        try:
            result = scanner.scan(fpath, tmpdir)
            assert len(result.raw_dependencies) == 1
        finally:
            _cleanup_tmpdir(tmpdir)
