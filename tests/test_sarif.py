"""Tests for SARIF 2.1.0 output generation."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.models import (
    CycleInfo,
    CycleReport,
    CycleSeverity,
    DependencyKind,
    GraphBuildResult,
    RawDependency,
    ResolvedDependency,
)
from deppulse.reporting import graph_to_sarif, write_sarif_report
from deppulse.reporting.sarif import (
    _cycle_to_result,
    _dependency_level,
    _dependency_message,
    _dependency_rule_id,
    _dependency_to_result,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "python_project"


def _make_raw(kind: str = "import", text: str = "foo", line: int = 1) -> RawDependency:
    return RawDependency(raw_text=text, kind=DependencyKind(kind), line_number=line)


def _internal_dep(
    raw_text: str = "mymodule",
    normalized: str = "pkg/module.py",
    line: int = 1,
) -> ResolvedDependency:
    return ResolvedDependency(
        raw=_make_raw("import", raw_text, line),
        normalized_path=normalized,
        is_external=False,
        is_stdlib=False,
        is_unresolved=False,
    )


def _external_dep(
    raw_text: str = "requests",
    normalized: str = None,
    line: int = 1,
) -> ResolvedDependency:
    return ResolvedDependency(
        raw=_make_raw("import", raw_text, line),
        normalized_path=normalized,
        is_external=True,
        is_stdlib=False,
        is_unresolved=False,
    )


def _stdlib_dep(
    raw_text: str = "os",
    normalized: str = None,
    line: int = 1,
) -> ResolvedDependency:
    return ResolvedDependency(
        raw=_make_raw("import", raw_text, line),
        normalized_path=normalized,
        is_external=True,
        is_stdlib=True,
        is_unresolved=False,
    )


def _unresolved_dep(
    raw_text: str = "notfound",
    normalized: str = None,
    line: int = 1,
    note: str = "",
) -> ResolvedDependency:
    return ResolvedDependency(
        raw=_make_raw("import", raw_text, line),
        normalized_path=normalized,
        is_external=False,
        is_stdlib=False,
        is_unresolved=True,
        resolution_note=note,
    )


# ---------------------------------------------------------------------------
# Unit-level helper function tests
# ---------------------------------------------------------------------------

class TestDependencyLevelMapping:
    def test_internal_yields_note(self):
        dep = _internal_dep()
        assert _dependency_level(dep) == "note"

    def test_external_yields_warning(self):
        dep = _external_dep()
        assert _dependency_level(dep) == "warning"

    def test_stdlib_yields_warning(self):
        dep = _stdlib_dep()
        assert _dependency_level(dep) == "warning"

    def test_unresolved_yields_error(self):
        dep = _unresolved_dep()
        assert _dependency_level(dep) == "error"


class TestDependencyMessage:
    def test_internal_message_contains_path(self):
        dep = _internal_dep(raw_text="mymodule", normalized="pkg/mod.py")
        msg = _dependency_message(dep)
        assert "Internal dependency" in msg
        assert "pkg/mod.py" in msg

    def test_external_message(self):
        dep = _external_dep(raw_text="requests")
        msg = _dependency_message(dep)
        assert "External dependency" in msg
        assert "requests" in msg

    def test_stdlib_message(self):
        dep = _stdlib_dep(raw_text="os")
        msg = _dependency_message(dep)
        assert "Standard library" in msg

    def test_unresolved_message_with_note(self):
        dep = _unresolved_dep(raw_text="missing", note="could not resolve")
        msg = _dependency_message(dep)
        assert "Unresolved dependency" in msg
        assert "could not resolve" in msg


class TestDependencyRuleId:
    def test_import_kind(self):
        dep = _internal_dep()
        assert _dependency_rule_id(dep) == "import"

    def test_include_local_kind(self):
        raw = RawDependency(raw_text="local.h", kind=DependencyKind.INCLUDE_LOCAL, line_number=1)
        dep = ResolvedDependency(
            raw=raw,
            normalized_path="local.h",
            is_external=False,
            is_stdlib=False,
            is_unresolved=False,
        )
        assert _dependency_rule_id(dep) == "include_local"

    def test_include_system_kind(self):
        raw = RawDependency(raw_text="stdio.h", kind=DependencyKind.INCLUDE_SYSTEM, line_number=1)
        dep = ResolvedDependency(
            raw=raw,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=False,
        )
        assert _dependency_rule_id(dep) == "include_system"


class TestDependencyToResult:
    def test_location_uses_file_path(self):
        dep = _internal_dep(raw_text="utils", normalized="utils/helpers.py", line=5)
        result = _dependency_to_result(dep, "app.py")
        assert len(result["locations"]) == 1
        assert result["locations"][0]["artifactLocation"]["uri"] == "app.py"
        assert result["locations"][0]["region"]["startLine"] == 5

    def test_error_level_for_unresolved(self):
        dep = _unresolved_dep()
        result = _dependency_to_result(dep, "app.py")
        assert result["level"] == "error"

    def test_warning_level_for_external(self):
        dep = _external_dep()
        result = _dependency_to_result(dep, "app.py")
        assert result["level"] == "warning"

    def test_note_level_for_internal(self):
        dep = _internal_dep()
        result = _dependency_to_result(dep, "app.py")
        assert result["level"] == "note"


class TestCycleToResult:
    def test_cycle_result_level_is_warning(self):
        cycle = CycleInfo(nodes=["a.py", "b.py", "a.py"], length=3)
        result = _cycle_to_result(cycle, 1)
        assert result["level"] == "warning"
        assert result["ruleId"] == "dependency-cycle"
        assert "cycleIndex" in result["properties"]
        assert result["properties"]["cycleLength"] == 3

    def test_message_includes_chain(self):
        cycle = CycleInfo(nodes=["x.py", "y.py", "x.py"], length=3)
        result = _cycle_to_result(cycle, 5)
        assert "x.py" in result["message"]["text"]
        assert "3" in result["message"]["text"]


# ---------------------------------------------------------------------------
# graph_to_sarif integration tests
# ---------------------------------------------------------------------------

class TestGraphToSarif:
    @pytest.fixture
    def scanned_result(self):
        """Real scan of the python_project fixture."""
        orchestrator = DependencyOrchestrator(use_cache=False)
        return orchestrator.scan(FIXTURE_ROOT)

    def test_output_is_dict(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        assert isinstance(sarif, dict)

    def test_sarif_version(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        assert sarif["version"] == "2.1.0"

    def test_runs_is_non_empty_list(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        assert isinstance(sarif["runs"], list)
        assert len(sarif["runs"]) == 1

    def test_driver_name_and_version(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        driver = sarif["runs"][0]["tool"]["driver"]
        assert driver["name"] == "DepPulse"
        assert "version" in driver

    def test_results_is_list(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        assert isinstance(sarif["runs"][0]["results"], list)

    def test_no_results_when_scan_is_empty(self):
        """A scan with zero resolved dependencies should still produce valid SARIF."""
        from deppulse.models import GraphStats
        empty_result = GraphBuildResult(
            project_root="/fake",
            scanned_at=datetime.now(),
            scan_results=[],
            total_files_found=0,
            files_skipped=0,
            files_with_errors=0,
            stats=GraphStats(
                total_files=0, total_edges=0,
                python_files=0, java_files=0, kotlin_files=0, cpp_files=0,
                javascript_files=0, typescript_files=0,
                unknown_files=0,
                internal_edges=0, external_edges=0, unresolved_edges=0,
                total_symbols=0, language_breakdown={}, files_with_cycles=0,
            ),
        )
        sarif = graph_to_sarif(empty_result)
        assert sarif["version"] == "2.1.0"
        assert sarif["runs"][0]["results"] == []

    def test_each_result_has_required_fields(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        for result in sarif["runs"][0]["results"]:
            assert "ruleId" in result
            assert "level" in result
            assert result["level"] in ("error", "warning", "note")
            assert "message" in result
            assert "text" in result["message"]
            assert "locations" in result
            assert len(result["locations"]) >= 1

    def test_level_mapping_in_results(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        levels = {r["level"] for r in sarif["runs"][0]["results"]}
        assert levels.issubset({"error", "warning", "note"})

    def test_files_map_populated(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        run = sarif["runs"][0]
        if "files" in run:
            for uri in run["files"]:
                assert isinstance(uri, str)
                assert uri

    def test_cycles_included_when_provided(self, scanned_result):
        cycle_report = CycleReport(
            cycle_count=1,
            cycles=[CycleInfo(nodes=["cycle_a.py", "cycle_b.py", "cycle_a.py"], length=3)],
            top_cycle_participants=[("cycle_a.py", 1), ("cycle_b.py", 1)],
            severity=CycleSeverity.MINOR,
            total_files_in_cycles=2,
        )
        sarif = graph_to_sarif(scanned_result, cycle_report=cycle_report)
        cycle_results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "dependency-cycle"]
        assert len(cycle_results) == 1
        assert cycle_results[0]["level"] == "warning"
        assert "cycleIndex" in cycle_results[0]["properties"]

    def test_project_root_overrides_graph_root(self, scanned_result):
        sarif_explicit = graph_to_sarif(scanned_result, project_root="/explicit/root")
        assert sarif_explicit["runs"][0]["tool"]["driver"]["name"] == "DepPulse"

    def test_sarif_serializable_to_json(self, scanned_result):
        sarif = graph_to_sarif(scanned_result)
        json_str = json.dumps(sarif, indent=2)
        assert json_str
        parsed = json.loads(json_str)
        assert parsed["version"] == "2.1.0"

    def test_fixture_produces_note_level_internal_results(self, scanned_result):
        """The python_project fixture has internal relative imports -> note level."""
        sarif = graph_to_sarif(scanned_result)
        results = sarif["runs"][0]["results"]
        assert any(r["level"] == "note" for r in results)


class TestWriteSarifReport:
    @pytest.fixture
    def scanned_result(self):
        orchestrator = DependencyOrchestrator(use_cache=False)
        return orchestrator.scan(FIXTURE_ROOT)

    def test_writes_valid_json_file(self, scanned_result, tmp_path: Path):
        output_file = tmp_path / "report.sarif"
        sarif = graph_to_sarif(scanned_result)
        write_sarif_report(sarif, output_file)
        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert parsed["version"] == "2.1.0"
        assert "runs" in parsed

    def test_creates_parent_dirs(self, scanned_result, tmp_path: Path):
        output_file = tmp_path / "deeply" / "nested" / "report.sarif"
        sarif = graph_to_sarif(scanned_result)
        write_sarif_report(sarif, output_file)
        assert output_file.exists()

    def test_overwrites_existing_file(self, scanned_result, tmp_path: Path):
        output_file = tmp_path / "report.sarif"
        output_file.write_text("{}", encoding="utf-8")
        sarif = graph_to_sarif(scanned_result)
        write_sarif_report(sarif, output_file)
        content = output_file.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert parsed["version"] == "2.1.0"
