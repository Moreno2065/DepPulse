"""Tests for the symbol-level call graph builder."""

from datetime import datetime
from pathlib import Path

import pytest

from deppulse.core.callgraph import (
    CallGraphBuilder,
    _build_symbol_index,
    _escape_mermaid,
    _escape_dot,
    callgraph_to_json,
    callgraph_to_mermaid,
    callgraph_to_dot,
)
from deppulse.models import (
    ExtractedSymbol,
    GraphBuildResult,
    GraphStats,
    Language,
    ScanResult,
    Symbol,
    SymbolCall,
    SymbolType,
)


def _make_scan_result(
    file_path: str,
    language: Language,
    suffix: str,
    symbols: list[ExtractedSymbol] = None,
    absolute_path: str = None,
) -> ScanResult:
    if symbols is None:
        symbols = []
    return ScanResult(
        file_path=file_path,
        absolute_path=absolute_path or f"/fake/{file_path}",
        language=language,
        suffix=suffix,
        size_bytes=100,
        symbols=symbols,
    )


class TestBuildSymbolIndex:
    def test_empty_results(self):
        result = _build_symbol_index([])
        assert result == {}

    def test_single_file_single_symbol(self):
        scan_results = [
            _make_scan_result(
                "foo.py",
                Language.PYTHON,
                ".py",
                [ExtractedSymbol(symbol_type="function", name="bar", fully_qualified="function:bar")],
            ),
        ]
        index = _build_symbol_index(scan_results)
        assert "foo.py" in index
        assert len(index["foo.py"]) == 1
        sym = index["foo.py"][0]
        assert sym.name == "bar"
        assert sym.file_path == "foo.py"
        assert sym.language == Language.PYTHON

    def test_multiple_files_multiple_symbols(self):
        scan_results = [
            _make_scan_result(
                "utils.py",
                Language.PYTHON,
                ".py",
                [
                    ExtractedSymbol(symbol_type="function", name="helper", fully_qualified="function:helper"),
                    ExtractedSymbol(symbol_type="class", name="Helper", fully_qualified="class:Helper"),
                ],
            ),
            _make_scan_result(
                "main.py",
                Language.PYTHON,
                ".py",
                [ExtractedSymbol(symbol_type="function", name="main", fully_qualified="function:main")],
            ),
        ]
        index = _build_symbol_index(scan_results)
        assert len(index) == 2
        assert len(index["utils.py"]) == 2
        assert len(index["main.py"]) == 1


class TestCallGraphBuilder:
    def test_empty_scan_results(self):
        builder = CallGraphBuilder(scan_results=[], project_root="/fake")
        cg = builder.build()
        assert len(cg.nodes) == 0
        assert len(cg.edges) == 0
        assert cg.stats.total_symbols == 0
        assert cg.stats.total_calls == 0
        assert cg.stats.max_call_depth == 0

    def test_stats_aggregates_by_language(self):
        scan_results = [
            _make_scan_result(
                "foo.py",
                Language.PYTHON,
                ".py",
                [ExtractedSymbol(symbol_type="function", name="foo", fully_qualified="function:foo")],
            ),
            _make_scan_result(
                "Bar.java",
                Language.JAVA,
                ".java",
                [ExtractedSymbol(symbol_type="method", name="bar", fully_qualified="method:Bar.bar")],
            ),
            _make_scan_result(
                "Utils.kt",
                Language.KOTLIN,
                ".kt",
                [ExtractedSymbol(symbol_type="function", name="util", fully_qualified="function:util")],
            ),
        ]
        builder = CallGraphBuilder(scan_results=scan_results, project_root="/fake")
        cg = builder.build()
        assert cg.stats.python_symbols == 1
        assert cg.stats.java_symbols == 1
        assert cg.stats.kotlin_symbols == 1
        assert cg.stats.total_symbols == 3


class TestCallGraphOutput:
    def test_to_json_structure(self):
        scan_results = [
            _make_scan_result(
                "foo.py",
                Language.PYTHON,
                ".py",
                [ExtractedSymbol(symbol_type="function", name="foo", fully_qualified="function:foo")],
            ),
        ]
        builder = CallGraphBuilder(scan_results=scan_results, project_root="/fake")
        cg = builder.build()
        data = callgraph_to_json(cg)

        assert "project_root" in data
        assert "scanned_at" in data
        assert "stats" in data
        assert "nodes" in data
        assert "edges" in data
        assert data["stats"]["total_symbols"] == 1

    def test_to_mermaid_produces_flowchart(self):
        scan_results = [
            _make_scan_result(
                "main.py",
                Language.PYTHON,
                ".py",
                [
                    ExtractedSymbol(symbol_type="function", name="main", fully_qualified="function:main"),
                    ExtractedSymbol(symbol_type="function", name="helper", fully_qualified="function:helper"),
                ],
            ),
        ]
        builder = CallGraphBuilder(scan_results=scan_results, project_root="/fake")
        cg = builder.build()
        output = callgraph_to_mermaid(cg)
        assert "flowchart" in output
        assert "subgraph" in output

    def test_to_dot_produces_digraph(self):
        scan_results = [
            _make_scan_result(
                "foo.py",
                Language.PYTHON,
                ".py",
                [ExtractedSymbol(symbol_type="function", name="foo", fully_qualified="function:foo")],
            ),
        ]
        builder = CallGraphBuilder(scan_results=scan_results, project_root="/fake")
        cg = builder.build()
        output = callgraph_to_dot(cg)
        assert "digraph" in output
        assert "{" in output


class TestEscaping:
    def test_escape_mermaid_quotes(self):
        assert _escape_mermaid('foo"bar') == 'foo\\"bar'
        assert _escape_mermaid("a\\b") == "a\\\\b"

    def test_escape_dot_quotes(self):
        assert _escape_dot('foo"bar') == 'foo\\"bar'
        assert _escape_dot("line1\nline2") == "line1\\nline2"
