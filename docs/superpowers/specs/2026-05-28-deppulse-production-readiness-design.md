# DepPulse 生产级改进设计

**Date:** 2026-05-28
**Status:** Approved
**Approach:** Benchmark-driven iterative improvement

---

## Summary

将 DepPulse v1.1.0 提升至生产级可用，核心策略是**以 benchmark 为牵引**：先建立可重复的基准测试，获取真实项目上的准确率数据，然后按数据优先级修复扫描器，补强测试，优化性能，最终验证发布。

---

## Phase 1: Benchmark Baseline

**Goal:** `python scripts/benchmark.py --repo all` runs end-to-end without errors.

**Current State:**
- Framework exists: clone, scan, trace, test selection entry points are all present
- Missing: `import_recall`, `import_precision`, `resolve_accuracy`, `call_edge_recall` all return `0.0` (marked "requires manual inspection")
- Risk: tree-sitter scanners may crash or be extremely slow on large repos

**Tasks:**
1. Fix benchmark runtime issues (tree-sitter loading, exception handling, `--skip-clone` cache detection)
2. Implement automated metrics (file count, edge count, scan time, warnings)
3. Design semi-automated manual inspection (random sampling + structured recording)
   - Import recall: sample 20 files, manual count vs tool count
   - Import precision: sample 20 detected imports, manual verify
   - Resolve accuracy: sample 20 internal imports, manual verify path
   - Call edge recall: sample 10 files, manual count method calls
4. Output `benchmark-results.json` with actual numbers + `manual_samples` array

**Success Criteria:** `python scripts/benchmark.py --repo flask` completes without error, output contains structured data (even if metrics are 0, with `manual_samples` explaining why).

---

## Phase 2: Scanner Fixes (Data-Driven)

**Goal:** Fix scanners in order of lowest benchmark accuracy.

**Priority (based on code review):**

| Priority | Scanner | Issue | Fix Direction |
|----------|---------|-------|---------------|
| P0 | C++ | `_RE_INCLUDE` regex still in file despite tree-sitter implementation | Remove legacy regex, verify tree-sitter extraction completeness |
| P1 | Kotlin | `extract_symbols` has regex fallback for "backward compatibility" | Remove regex fallback, unify on tree-sitter, update tests |
| P2 | JS/TS | New implementation, lacks large-project validation | Run on axios benchmark, fix edge cases in PathResolver |
| P3 | PathResolver | Generic issues with module resolution | Add patterns found in benchmark (namespace packages, src/main/java prefix, etc.) |

**Success Criteria:** Lowest accuracy scanner improves import recall by >= 20% OR resolve accuracy by >= 15%. `pytest tests/` remains all green after each fix.

---

## Phase 3: Targeted Test Strengthening

**Goal:** Add precise tests for benchmark-discovered blind spots, not blanket coverage.

**Strategy:**
1. Import edge cases from benchmark failures into test fixtures
2. Add performance benchmarks:
   - `test_scan_performance.py`: scan time ceiling for fixture projects
   - `test_memory_usage.py`: memory ceiling for 1000-file synthetic project
3. Regression tests: one test per Phase 2 bug fix
4. Fill gaps: JS/TS scanner tests, benchmark integration tests

**Success Criteria:** New tests cover all Phase 2 bugs. Performance baselines established.

---

## Phase 4: Performance & Maintainability

**Goal:** Faster scans, clean code, zero lint errors.

**Tasks:**
1. Performance optimization
   - Parallel scanning via `concurrent.futures`
   - Tree-sitter parse tree caching
   - Batch `nx.DiGraph.add_edges_from`
2. Code quality
   - Fix 287 ruff errors (197 auto-fixable)
   - mypy type-checking pass
   - Delete redundant files (see Cleanup List below)
3. Module boundary cleanup
   - Split `cli.py` (>1100 lines) into `cli/commands/*.py`
   - Extract HTML template from `ui/visualize.py` to standalone file

**Cleanup List:**
- `redesign.md` — design spec fully implemented in code
- `node_modules/` — mistakenly included in Python project
- `.cursor/`, `.superpowers/` — temporary IDE/plugin directories
- Any other orphaned files discovered during audit

**Success Criteria:** `ruff check deppulse/` returns zero. Large project scan (1000 files) completes in < 10 seconds.

---

## Phase 5: Final Validation & Release Prep

**Goal:** Prove improvement works, gain release confidence.

**Tasks:**
1. Re-run benchmark against Phase 1 baseline, verify all metrics improved
2. End-to-end tests:
   - `deppulse scan` on Flask/retrofit/axios without crash
   - `deppulse diff` / `deppulse tests` functional
   - HTML dashboard generation works
3. Documentation sync:
   - README feature list matches actual capabilities
   - `docs/` scanner status table updated (tree-sitter migration status)
4. PyPI release check:
   - `pyproject.toml` metadata complete
   - `python -m build` succeeds
   - Version bump strategy defined

**Success Criteria:** Benchmark report shows accuracy improvement. CI all green. Documentation has no over-promises.

---

## Cross-Cutting Principles

1. **Data-driven:** Every fix is justified by benchmark numbers. No "feels like it should be better" optimizations.
2. **Measure twice, cut once:** Benchmark before and after every scanner change.
3. **Test as safety net:** New tests are added for every bug found in benchmark, not for coverage percentage.
4. **No unrelated refactoring:** Only clean code that is touched for the current goal. No wholesale rewrites of working modules.
