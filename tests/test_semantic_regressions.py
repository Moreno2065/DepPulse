"""Semantic regression tests for the dependency analysis core."""
import networkx as nx

from deppulse.config import DepPulseConfig
from deppulse.core.analyzer import ImpactAnalyzer
from deppulse.core.callgraph import CallGraphBuilder, callgraph_to_dot, callgraph_to_json, callgraph_to_mermaid
from deppulse.core.test_selector import TestSelector, convention_test_path
from deppulse.scanners.python_scanner import PythonScanner


def scan(tmp_path, sources):
    for name, body in sources.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return [PythonScanner().scan(tmp_path / name, tmp_path) for name in sources]


def edges(graph):
    return {(e.caller.file_path, e.caller.fully_qualified,
             e.callee.file_path, e.callee.fully_qualified): e for e in graph.edges}


def test_python_callers_methods_and_source_lines(tmp_path):
    results = scan(tmp_path, {"app.py":
        "def target():\n    pass\n\ndef caller():\n    target()\n\n"
        "class Worker:\n    def run(self):\n        self.finish()\n"
        "    def finish(self):\n        pass\n"})
    names = [s.fully_qualified for s in results[0].symbols]
    assert names.count("method:Worker.run") == 1
    assert "function:run" not in names
    assert "function:finish" not in names
    graph = CallGraphBuilder(results, str(tmp_path)).build()
    found = edges(graph)
    assert set(found) == {
        ("app.py", "function:caller", "app.py", "function:target"),
        ("app.py", "method:Worker.run", "app.py", "method:Worker.finish"),
    }
    assert found[("app.py", "function:caller", "app.py", "function:target")].call_site == ("app.py", 5)


def test_cross_file_import_alias_and_unknown_receiver(tmp_path):
    results = scan(tmp_path, {
        "utils.py": "def target():\n    pass\n",
        "other.py": "def target():\n    pass\n",
        "app.py": "from utils import target as call_target\n"
                  "def run(obj):\n    call_target()\n    obj.target()\n",
    })
    graph = CallGraphBuilder(results, str(tmp_path)).build()
    assert set(edges(graph)) == {("app.py", "function:run", "utils.py", "function:target")}


def test_duplicate_symbol_names_stay_distinct_in_graph_exports(tmp_path):
    results = scan(tmp_path, {
        "a.py": "def helper():\n    pass\ndef go():\n    helper()\n",
        "b.py": "def helper():\n    pass\ndef go():\n    helper()\n",
    })
    graph = CallGraphBuilder(results, str(tmp_path)).build()
    assert len(edges(graph)) == 2
    assert {x["caller_file"] for x in callgraph_to_json(graph)["edges"]} == {"a.py", "b.py"}
    assert "n1 --> n0" in callgraph_to_mermaid(graph)
    assert "n3 --> n2" in callgraph_to_mermaid(graph)
    assert "n1 -> n0" in callgraph_to_dot(graph)
    assert "n3 -> n2" in callgraph_to_dot(graph)


def test_async_method_and_explicit_module_import(tmp_path):
    results = scan(tmp_path, {
        "utils.py": "def finish():\n    pass\n",
        "app.py": "import utils as alias\n"
                  "class Client:\n"
                  "    async def fetch(self):\n        alias.finish()\n"
                  "    def done(self):\n        pass\n",
    })
    found = edges(CallGraphBuilder(results, str(tmp_path)).build())
    assert ("app.py", "method:Client.fetch", "utils.py", "function:finish") in found


def test_impact_chains_and_radius_are_semantically_correct():
    graph = nx.DiGraph([("web.py", "service.py"), ("service.py", "db.py")])
    analyzer = ImpactAnalyzer(graph)
    impact = analyzer.analyze_file("db.py")
    assert set(impact.affected_files) == {"web.py", "service.py"}
    assert {tuple(c.chain) for c in impact.impact_chains} == {
        ("web.py", "service.py", "db.py"), ("service.py", "db.py")
    }
    assert impact.blast_radius_percent == 100.0
    multi = analyzer.analyze_files(["./db.py"])
    assert multi.blast_radius_percent == 100.0
    assert "./db.py" not in multi.all_affected_files


def test_conventions_only_select_existing_tests_without_cap_overflow(tmp_path):
    assert convention_test_path("src/foo.py") == "tests/test_foo.py"
    assert convention_test_path("deppulse/core/analyzer.py") == "tests/test_analyzer.py"
    assert convention_test_path("src/pkg/foo.py") == "tests/pkg/test_foo.py"
    assert convention_test_path("src/foo.ts") is None
    graph = nx.DiGraph()
    graph.add_nodes_from(["src/foo.py", "src/missing.py", "tests/test_foo.py"])
    selector = TestSelector(graph, DepPulseConfig(project_root=tmp_path))
    selected = selector.select_tests(["src/foo.py", "src/missing.py"], changed_symbols=[], max_blast=1)
    assert selected.selected_tests == ["tests/test_foo.py"]
    assert selected.by_strategy["convention"] == ["tests/test_foo.py"]
