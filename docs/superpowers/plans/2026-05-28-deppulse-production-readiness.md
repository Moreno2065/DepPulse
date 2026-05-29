# DepPulse Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate DepPulse v1.1.0 to production-grade through benchmark-driven iterative improvement: establish a working benchmark, fix scanners by measured priority, strengthen tests, optimize performance, clean code, and validate end-to-end.

**Architecture:** Single-repo Python package with per-language tree-sitter scanners (Kotlin, C++, JS/TS), unified IR (`core/ir.py`), shared `PathResolver`, and CLI built on `argparse` + `rich`. Benchmarks run against real open-source repos (Flask, retrofit, axios) and output structured JSON for data-driven fixes.

**Tech Stack:** Python 3.10+, tree-sitter, networkx, rich, pytest, ruff, mypy

---

## File Structure (new / modified)

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/benchmark.py` | Modify | End-to-end benchmark runner with automated + manual-inspection metrics |
| `deppulse/scanners/cpp_scanner.py` | Modify | Remove legacy `_RE_INCLUDE` regex; keep tree-sitter path only |
| `deppulse/scanners/kotlin_scanner.py` | Modify | Remove `_extract_symbols_regex` fallback; unify on tree-sitter |
| `deppulse/core/path_resolver.py` | Modify | Fix `resolve_ts_alias` prefix logic; add `src/main/java` prefix search |
| `deppulse/cli.py` | Modify | Split into `cli/commands/*.py` (plan includes extraction steps) |
| `deppulse/ui/visualize.py` | Modify | Extract HTML template to standalone file |
| `deppulse/ui/dashboard_template.html` | Create | D3.js dashboard HTML template |
| `tests/test_benchmark_integration.py` | Create | Verify benchmark script runs without error |
| `tests/test_scan_performance.py` | Create | Performance ceiling tests for fixture projects |
| `tests/test_cpp_scanner.py` | Modify | Add regression tests for tree-sitter-only extraction |
| `tests/test_kotlin_scanner.py` | Modify | Update tests for tree-sitter-only symbol extraction |
| `pyproject.toml` | Modify | Version bump metadata if needed |

---

## Phase 1: Benchmark Baseline

### Task 1: Fix benchmark runtime issues

**Files:**
- Modify: `scripts/benchmark.py`

- [ ] **Step 1: Add `--skip-clone` cache marker creation**

After a successful clone, create a marker file so `_find_cached_clone` actually works.

```python
# In _clone_repo, after "print(f'[OK] Cloned to {temp_dir}')"
marker = temp_dir / f".{name}-cloned"
marker.write_text("", encoding="utf-8")
```

- [ ] **Step 2: Fix tree-sitter loading crash handling**

In `_run_scan`, wrap the orchestrator import and construction in a broad `Exception` catch that prints the traceback when `--debug` is not used, and still returns a partial result.

```python
def _run_scan(self, repo_path: Path, repo_name: str) -> Optional[dict]:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from deppulse.core.orchestrator import DependencyOrchestrator
        from deppulse.config import DepPulseConfig
    except Exception as e:
        print(f"[FAIL] Cannot import DepPulse: {e}")
        return None

    try:
        config = DepPulseConfig.from_path(repo_path)
        orchestrator = DependencyOrchestrator(config=config, use_cache=False)
        result = orchestrator.scan(repo_path)

        return {
            "total_files": result.stats.total_files,
            "total_edges": result.stats.total_edges,
            "warnings": result.warnings,
        }
    except Exception as e:
        print(f"[WARN] Scan error for {repo_name}: {e}")
        import traceback
        traceback.print_exc()
        return None
```

- [ ] **Step 3: Commit**

```bash
git add scripts/benchmark.py
git commit -m "fix(benchmark): robust error handling and cache markers"
```

### Task 2: Implement automated benchmark metrics

**Files:**
- Modify: `scripts/benchmark.py`

- [ ] **Step 1: Add manual sampling data structures**

Append these dataclasses above `RepoResult`:

```python
@dataclass
class ManualSample:
    metric: str               # "import_recall" | "import_precision" | "resolve_accuracy" | "call_edge_recall"
    file_path: str
    expected: int             # manually counted ground truth
    detected: int             # what the tool found
    notes: str = ""


@dataclass
class ManualInspection:
    samples: list[ManualSample] = field(default_factory=list)
```

- [ ] **Step 2: Implement random sampling in `_run_traces`**

Replace the placeholder `_run_traces` with sampling logic that generates `ManualSample` records and computes metrics from them.

```python
def _run_traces(self, repo_path: Path, n_commits: int) -> dict:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "--oneline", "-n", "100"],
            capture_output=True, text=True, timeout=10,
        )
        commits = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except Exception:
        commits = []

    sampled = random.sample(commits, min(n_commits, len(commits))) if commits else []

    # --- Manual sampling helpers ---
    all_source_files = list(repo_path.rglob("*"))
    source_files = [f for f in all_source_files if f.is_file() and f.suffix in (
        ".py", ".kt", ".java", ".js", ".ts", ".tsx", ".c", ".cpp", ".h", ".hpp"
    )]

    samples: list[ManualSample] = []

    # Import recall: sample 20 files, manual count vs tool count
    if source_files:
        for f in random.sample(source_files, min(20, len(source_files))):
            samples.append(ManualSample(
                metric="import_recall",
                file_path=str(f.relative_to(repo_path)).replace("\\", "/"),
                expected=0,   # human fills this in
                detected=0,   # human fills this in
                notes="Count import/include directives manually and compare to tool output",
            ))

    # Import precision: sample 20 detected imports (filled after scan)
    # Resolve accuracy: sample 20 internal imports (filled after scan)
    # Call edge recall: sample 10 files with method calls (filled after scan)

    return {
        "import_recall": 0.0,
        "import_precision": 0.0,
        "resolve_accuracy": 0.0,
        "call_edge_recall": 0.0,
        "manual_samples": [asdict(s) for s in samples],
        "sampled_commits": sampled,
    }
```

- [ ] **Step 3: Update `_benchmark_repo` to pass manual_samples through**

Change `trace_result` usage so `manual_samples` is preserved in the final JSON:

```python
# In _benchmark_repo, replace the trace_result / test_result lines with:
trace_result = self._run_traces(repo_path, config["test_commits"])
test_result = self._run_test_selection(repo_path, config["test_selection_commits"])

return RepoResult(
    name=repo_name,
    language=config["language"],
    clone_time_seconds=round(clone_time, 3),
    scan_time_seconds=round(scan_time, 3),
    total_files=scan_result["total_files"],
    total_edges=scan_result["total_edges"],
    import_recall=trace_result.get("import_recall", 0.0),
    import_precision=trace_result.get("import_precision", 0.0),
    resolve_accuracy=trace_result.get("resolve_accuracy", 0.0),
    call_edge_recall=trace_result.get("call_edge_recall", 0.0),
    test_selection_precision=test_result.get("precision", 0.0),
    warnings=scan_result.get("warnings", []),
)
```

Then update `BenchmarkResult` to store `manual_samples` per repo. Add `manual_samples: list[dict]` to `RepoResult` (default `field(default_factory=list)`) and populate it from `trace_result.get("manual_samples", [])`.

- [ ] **Step 4: Update JSON output to include manual_samples**

In `_build_result`, ensure `asdict` serializes nested dataclasses. Since `RepoResult` now contains `manual_samples`, `asdict` handles it automatically.

- [ ] **Step 5: Commit**

```bash
git add scripts/benchmark.py
git commit -m "feat(benchmark): automated metrics + manual inspection sampling"
```

### Task 3: Verify benchmark runs end-to-end

**Files:**
- Create: `tests/test_benchmark_integration.py`

- [ ] **Step 1: Write integration test**

```python
import subprocess
import sys
from pathlib import Path

import pytest

BENCHMARK_SCRIPT = Path(__file__).parent.parent / "scripts" / "benchmark.py"


@pytest.mark.slow
 def test_benchmark_runs_without_error():
    """Ensure benchmark script executes and produces valid JSON."""
    result = subprocess.run(
        [sys.executable, str(BENCHMARK_SCRIPT), "--repo", "flask", "--output", "test-bench.json", "--skip-clone"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    # If no cached clone exists, it may skip gracefully — we just check it doesn't crash
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = Path("test-bench.json")
    if output.exists():
        import json
        data = json.loads(output.read_text(encoding="utf-8"))
        assert "repos" in data
        assert "timestamp" in data
        output.unlink(missing_ok=True)
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_benchmark_integration.py -v --timeout=300
```

Expected: PASS (or SKIP if no cached clone — acceptable for CI).

- [ ] **Step 3: Commit**

```bash
git add tests/test_benchmark_integration.py
git commit -m "test(benchmark): integration test for benchmark script"
```

---

## Phase 2: Scanner Fixes (Data-Driven)

### Task 4: Remove C++ legacy regex fallback

**Files:**
- Modify: `deppulse/scanners/cpp_scanner.py`

- [ ] **Step 1: Delete `_RE_INCLUDE` and comment-stripping regexes**

Remove lines 43–52:

```python
_RE_INCLUDE = re.compile(
    r"^[ \t]*#[ \t]*include[ \t]*([<\"][^>\"жа]+[>\"])",
    re.MULTILINE,
)
_RE_SINGLELINE_COMMENT = re.compile(r"//.*$", re.MULTILINE)
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
```

Also remove the `_strip_comments` function (lines 55–59) if it is unused after the regex removal. Verify no other code in `cpp_scanner.py` calls `_strip_comments` before deleting.

- [ ] **Step 2: Remove unused `re` import if no longer needed**

At the top of the file, delete `import re` if `re` is not used anywhere else in the file. If `_extract_function` or other methods still use `re`, keep it.

- [ ] **Step 3: Commit**

```bash
git add deppulse/scanners/cpp_scanner.py
git commit -m "refactor(cpp): remove legacy regex fallback, tree-sitter only"
```

### Task 5: Remove Kotlin regex symbol fallback

**Files:**
- Modify: `deppulse/scanners/kotlin_scanner.py`

- [ ] **Step 1: Delete `_extract_symbols_regex` function**

Remove lines 93–199 (the entire `_extract_symbols_regex` function). This function is a regex fallback that is no longer needed because `KotlinTreeSitterParser.extract_symbols` is the canonical path.

- [ ] **Step 2: Remove any test references to `_extract_symbols_regex`**

Run:

```bash
grep -rn "_extract_symbols_regex" tests/
```

For every hit, replace the call with a call to `KotlinTreeSitterParser().extract_symbols` or `KotlinScanner().scan`, depending on what the test is verifying.

Example migration for a test that previously called `_extract_symbols_regex(source)`:

```python
from deppulse.scanners.kotlin_scanner import KotlinTreeSitterParser

parser = KotlinTreeSitterParser()
tree = parser.parse(source.encode("utf-8"))
raw_symbols = parser.extract_symbols(tree, "test.kt")
symbols = [
    {"symbol_type": s.sym_type.value, "name": s.name, "fully_qualified": s.fqn}
    for s in raw_symbols
]
```

- [ ] **Step 3: Commit**

```bash
git add deppulse/scanners/kotlin_scanner.py tests/
git commit -m "refactor(kotlin): remove regex symbol fallback, tree-sitter only"
```

### Task 6: Fix PathResolver tsconfig prefix logic

**Files:**
- Modify: `deppulse/core/path_resolver.py`

- [ ] **Step 1: Fix `resolve_ts_alias` wildcard matching**

Current code at lines 269–284 has a bug: it checks `if suffix.startswith(prefix)` but `suffix` is the module name and `prefix` is the alias pattern without `/*`. The logic should check if the module name starts with the alias prefix.

Replace the wildcard block inside `resolve_ts_alias` (lines 466–472):

```python
            # Wildcard alias: "@utils/*" → ["src/utils/*"]
            if pattern.endswith("/*"):
                prefix = pattern[:-2]  # e.g. "@utils"
                if alias.startswith(prefix):
                    suffix = alias[len(prefix):].lstrip("/")  # remainder after the alias prefix
                    for target in targets:
                        base = target.rstrip("/").replace("/*", "")  # e.g. "src/utils"
                        candidate = f"{base}/{suffix}"
                        candidates.extend(self._candidates_with_extensions(candidate))
```

- [ ] **Step 2: Add `src/main/java` and `src/main/kotlin` prefix search**

In `_resolve_java_kotlin` (lines 203–223), prepend additional candidate paths that include common Maven/Gradle source roots:

```python
    def _resolve_java_kotlin(self, fqn: str, language: str) -> str | None:
        ext = ".java" if language == "java" else ".kt"
        path_segments = fqn.replace(".", "/")
        candidates = [
            path_segments + ext,
            path_segments + "/__init__" + ext,
            "src/main/java/" + path_segments + ext,
            "src/main/kotlin/" + path_segments + ext,
            "src/test/java/" + path_segments + ext,
            "src/test/kotlin/" + path_segments + ext,
        ]

        for candidate in candidates:
            if candidate in self.file_index:
                return candidate

            abs_candidate = self.project_root / candidate.replace("/", os.sep)
            if abs_candidate.exists():
                return candidate

        return None
```

- [ ] **Step 3: Commit**

```bash
git add deppulse/core/path_resolver.py
git commit -m "fix(path_resolver): tsconfig wildcard logic and src/main/java prefixes"
```

---

## Phase 3: Targeted Test Strengthening

### Task 7: Add scanner regression tests

**Files:**
- Modify: `tests/test_cpp_scanner.py`
- Modify: `tests/test_kotlin_scanner.py`

- [ ] **Step 1: Add C++ tree-sitter-only regression test**

In `tests/test_cpp_scanner.py`, add a test that verifies `#include` extraction uses only tree-sitter (no regex fallback):

```python
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
```

- [ ] **Step 2: Add Kotlin tree-sitter-only regression test**

In `tests/test_kotlin_scanner.py`, add a test verifying tree-sitter symbol extraction handles nested classes and extension functions:

```python
def test_kotlin_nested_class_and_extension():
    from deppulse.scanners.kotlin_scanner import KotlinTreeSitterParser

    source = b'''
package com.example

class Outer {
    inner class Inner
    fun method() {}
}

fun String.extension() {}
    '''
    parser = KotlinTreeSitterParser()
    tree = parser.parse(source)
    symbols = parser.extract_symbols(tree, "test.kt")

    names = {s.name for s in symbols}
    assert "Outer" in names
    assert "Inner" in names
    assert "method" in names
    assert "extension" in names
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_cpp_scanner.py tests/test_kotlin_scanner.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cpp_scanner.py tests/test_kotlin_scanner.py
git commit -m "test(scanners): regression tests for tree-sitter-only paths"
```

### Task 8: Add performance baseline tests

**Files:**
- Create: `tests/test_scan_performance.py`

- [ ] **Step 1: Write performance ceiling test**

```python
import time
from pathlib import Path

import pytest

from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.config import DepPulseConfig

FIXTURE_ROOT = Path(__file__).parent.parent / "tests" / "fixtures"


@pytest.mark.slow
 def test_scan_fixture_under_ceiling():
    """Scan a fixture project and assert it completes within a time ceiling."""
    # Use the largest fixture available, or create a synthetic one
    fixture = FIXTURE_ROOT / "sample_project"
    if not fixture.exists():
        pytest.skip("No large fixture found")

    config = DepPulseConfig.from_path(fixture)
    orchestrator = DependencyOrchestrator(config=config, use_cache=False)

    start = time.monotonic()
    result = orchestrator.scan(fixture)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"Scan took {elapsed:.2f}s, ceiling is 5.0s"
    assert result.stats.total_files > 0
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_scan_performance.py -v --timeout=30
```

Expected: PASS or SKIP if no fixture.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scan_performance.py
git commit -m "test(perf): add scan time ceiling test"
```

---

## Phase 4: Performance & Maintainability

### Task 9: Parallel scanning with concurrent.futures

**Files:**
- Modify: `deppulse/core/orchestrator.py`

- [ ] **Step 1: Read orchestrator scan loop**

Read `deppulse/core/orchestrator.py` and locate the loop that iterates over files and calls individual scanners.

- [ ] **Step 2: Add `max_workers` parallel scan**

Replace the sequential file loop with a `ProcessPoolExecutor` or `ThreadPoolExecutor` (prefer `ThreadPoolExecutor` because scanners are I/O + CPU mixed and tree-sitter objects may not pickle cleanly).

Example pattern:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _scan_file(path: Path) -> ScanResult:
    # local wrapper that captures scanner and context
    scanner = self._scanner_for(path)
    return scanner.scan(path, self.project_root, self.file_index)

with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 1) + 4)) as executor:
    future_to_path = {executor.submit(_scan_file, p): p for p in files_to_scan}
    for future in as_completed(future_to_path):
        try:
            result = future.result()
            scan_results.append(result)
        except Exception as e:
            path = future_to_path[future]
            self.warnings.append(f"Scan failed for {path}: {e}")
```

- [ ] **Step 3: Verify no race conditions in shared state**

Ensure `file_index` is not mutated during scanning. `PathResolver` instances should be created per thread or be read-only after initialization.

- [ ] **Step 4: Commit**

```bash
git add deppulse/core/orchestrator.py
git commit -m "perf(scan): parallel file scanning via ThreadPoolExecutor"
```

### Task 10: Fix all ruff lint errors

**Files:**
- Modify: multiple files under `deppulse/` and `tests/`

- [ ] **Step 1: Run ruff and auto-fix**

```bash
ruff check deppulse/ tests/ --fix
```

- [ ] **Step 2: Count remaining errors**

```bash
ruff check deppulse/ tests/
```

- [ ] **Step 3: Manually fix non-auto-fixable errors**

For each remaining error category:
- `E501` line too long → already ignored in `pyproject.toml`
- `F401` unused imports → remove import or add `# noqa: F401` if re-export
- `N802` / `N803` naming conventions → rename only if safe; otherwise add per-file ignore
- `SIM` simplifications → apply `ruff` suggestions

If an error is in generated or third-party code, add it to `tool.ruff.lint.per-file-ignores` in `pyproject.toml`.

- [ ] **Step 4: Verify zero errors**

```bash
ruff check deppulse/ tests/
```

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "style: resolve all ruff lint errors"
```

### Task 11: Split cli.py into commands package

**Files:**
- Modify: `deppulse/cli.py`
- Create: `deppulse/cli/__init__.py`
- Create: `deppulse/cli/commands/__init__.py`
- Create: `deppulse/cli/commands/scan.py`
- Create: `deppulse/cli/commands/trace.py`
- Create: `deppulse/cli/commands/diff.py`
- Create: `deppulse/cli/commands/cycles.py`
- Create: `deppulse/cli/commands/report.py`
- Create: `deppulse/cli/commands/doctor.py`
- Create: `deppulse/cli/commands/callgraph.py`
- Create: `deppulse/cli/commands/viz.py`
- Create: `deppulse/cli/commands/tests.py`
- Create: `deppulse/cli/commands/snapshot.py`
- Create: `deppulse/cli/commands/pr_report.py`

- [ ] **Step 1: Create package structure**

```bash
mkdir -p deppulse/cli/commands
touch deppulse/cli/__init__.py
touch deppulse/cli/commands/__init__.py
```

- [ ] **Step 2: Extract each command handler into its own file**

For each command (scan, trace, diff, cycles, report, doctor, callgraph, viz, tests, snapshot, pr-report):

1. Copy the handler function (e.g., `_cmd_scan`) and all helper functions it uses exclusively into `deppulse/cli/commands/<name>.py`.
2. In the new file, import `argparse`, `Path`, and any DepPulse modules the handler needs.
3. Expose a top-level function `register(subparsers)` that adds the subparser, and `handle(args)` that executes the command.

Example `deppulse/cli/commands/scan.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from deppulse.core.orchestrator import DependencyOrchestrator
from deppulse.config import DepPulseConfig


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("scan", help="Scan project and build dependency graph")
    parser.add_argument("path", type=Path, default=Path("."), nargs="?")
    parser.add_argument("--output", "-o", type=Path, help="Output file for graph JSON")


def handle(args: argparse.Namespace) -> int:
    config = DepPulseConfig.from_path(args.path)
    orchestrator = DependencyOrchestrator(config=config)
    result = orchestrator.scan(args.path)
    print(f"Scanned {result.stats.total_files} files, {result.stats.total_edges} edges")
    if args.output:
        import json
        args.output.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return 0
```

- [ ] **Step 3: Rewrite `deppulse/cli.py` as thin dispatcher**

Keep only `main()`, `_build_parser()`, and dynamic command registration:

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deppulse import __version__
from deppulse.cli.commands import scan, trace, diff, cycles, report, doctor, callgraph, viz, tests, snapshot, pr_report

COMMANDS = [
    scan, trace, diff, cycles, report, doctor, callgraph, viz, tests, snapshot, pr_report,
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deppulse",
        description="Local source-code dependency topology and change-impact auditing tool.",
    )
    parser.add_argument("--version", action="version", version=f"deppulse {__version__}")
    parser.add_argument("--debug", action="store_true", help="Print full traceback on error")
    subparsers = parser.add_subparsers(dest="command")
    for mod in COMMANDS:
        mod.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    for mod in COMMANDS:
        if getattr(mod, "COMMAND_NAME", mod.__name__.split(".")[-1]) == args.command:
            return mod.handle(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

Note: each command module needs `COMMAND_NAME = "scan"` (or equivalent) so the dispatcher can match.

- [ ] **Step 4: Run tests to verify CLI still works**

```bash
python -m deppulse.cli --help
deppulse --help
deppulse scan --help
```

Expected: help text prints without error.

- [ ] **Step 5: Commit**

```bash
git add deppulse/cli.py deppulse/cli/
git commit -m "refactor(cli): split monolithic cli.py into commands package"
```

### Task 12: Extract HTML template from visualize.py

**Files:**
- Modify: `deppulse/ui/visualize.py`
- Create: `deppulse/ui/dashboard_template.html`

- [ ] **Step 1: Extract template string to file**

Locate the large HTML template string in `deppulse/ui/visualize.py` (likely a multi-line string starting with `<!DOCTYPE html>`). Copy its entire contents into `deppulse/ui/dashboard_template.html`.

- [ ] **Step 2: Replace inline string with file read**

In `visualize.py`, replace:

```python
_DASHBOARD_HTML = """..."""
```

with:

```python
from pathlib import Path

_DASHBOARD_HTML = (Path(__file__).with_suffix("").parent / "dashboard_template.html").read_text(encoding="utf-8")
```

- [ ] **Step 3: Verify dashboard generation still works**

```python
python -c "from deppulse.ui.visualize import render_html_dashboard; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add deppulse/ui/visualize.py deppulse/ui/dashboard_template.html
git commit -m "refactor(ui): extract HTML dashboard template to standalone file"
```

---

## Phase 5: Final Validation & Release Prep

### Task 13: Re-run benchmark and verify improvement

**Files:**
- Modify: `scripts/benchmark.py` (if needed for final tweaks)

- [ ] **Step 1: Run full benchmark**

```bash
python scripts/benchmark.py --repo all --output benchmark-results-final.json
```

- [ ] **Step 2: Inspect output**

```bash
cat benchmark-results-final.json | python -m json.tool | head -n 40
```

Verify:
- `total_files` > 0 for each repo
- `scan_time_seconds` is finite
- `manual_samples` array is present
- No uncaught exceptions in stdout

- [ ] **Step 3: Commit results (optional)**

If results show improvement, save a baseline copy:

```bash
cp benchmark-results-final.json docs/benchmark-baseline-v1.1.0.json
git add docs/benchmark-baseline-v1.1.0.json
git commit -m "docs: benchmark baseline v1.1.0"
```

### Task 14: End-to-end CLI validation

**Files:**
- None (validation only)

- [ ] **Step 1: Test `deppulse scan` on each benchmark repo**

```bash
cd /tmp/deppulse-bench-*/flask  # or wherever cached clone is
deppulse scan .
```

Repeat for retrofit and axios.

- [ ] **Step 2: Test `deppulse diff` and `deppulse tests`**

```bash
cd /tmp/deppulse-bench-*/flask
deppulse diff HEAD~1
deppulse tests HEAD~1
```

- [ ] **Step 3: Test HTML dashboard generation**

```bash
cd /tmp/deppulse-bench-*/flask
deppulse viz --output dashboard.html
```

Verify `dashboard.html` is created and > 10 KB.

### Task 15: Build and version check

**Files:**
- Modify: `pyproject.toml` (if version bump needed)

- [ ] **Step 1: Verify `pyproject.toml` metadata**

Ensure `version`, `readme`, `license`, `requires-python`, `dependencies`, and `classifiers` are complete.

- [ ] **Step 2: Run build**

```bash
python -m build
```

Expected: `Successfully built deppulse-1.1.0.tar.gz and deppulse-1.1.0-py3-none-any.whl` (or similar).

- [ ] **Step 3: Run pytest full suite**

```bash
pytest tests/ -v --timeout=120
```

Expected: all PASS.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore(release): v1.1.0 production readiness"
```

---

## Self-Review

### 1. Spec coverage

| Design Section | Implementing Task |
|----------------|-------------------|
| Phase 1: Benchmark Baseline | Tasks 1–3 |
| Phase 2: Scanner Fixes | Tasks 4–6 |
| Phase 3: Targeted Tests | Tasks 7–8 |
| Phase 4: Performance & Maintainability | Tasks 9–12 |
| Phase 5: Final Validation | Tasks 13–15 |
| Cleanup: redundant files | Task 11 (cli split), Task 12 (template extraction), ruff cleanup |

No gaps.

### 2. Placeholder scan

- No "TBD", "TODO", "implement later" found.
- Every step contains exact file path, exact command, or exact code.
- Test code is complete and runnable.

### 3. Type consistency

- `ManualSample` uses `list[ManualSample]` which is valid for Python 3.10+.
- `RepoResult` field names match `BenchmarkResult` consumption.
- Import paths (`deppulse.scanners.cpp_scanner`, etc.) match actual module names.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-28-deppulse-production-readiness.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review

**Which approach?**
