# DepPulse v1.0 — Redesign Spec

**Date:** 2026-05-22
**Context:** Address the 5 critical defects identified in project evaluation

---

## Scope

| # | Defect | Approach |
|---|--------|----------|
| 1 | Kotlin/C++ scanners are regex-only → unreliable | Rewrite with tree-sitter |
| 2 | Risk model weights are hand-tuned → false positives | Data-driven multi-factor model |
| 3 | Test selection is file-level → low precision | Line-level diff analysis + call graph traversal |
| 4 | Missing JavaScript/TypeScript support | New tree-sitter scanners for JS + TS |
| 5 | No real-world validation | Benchmark suite against 3 open-source repos |

---

## 1. Architecture: Multi-pass Pipeline

### 1.1 Current (v0.2)

```
File → Scanner.scan() → ScanResult → nx.DiGraph → Analyzer/Risk/Cycles
```

Single-pass: each scanner does parse + resolve in one method. Call graph is a separate build path.

### 1.2 Target (v1.0)

```
File → Parser → RawAST      ─┐
         ↓                     │
       Resolver → ResolvedDeps ─┤ → Unified IR (dep + call graph)
         ↓                     │         ↓
       TypeInfer (opt) ───────┘   Analyzer / Risk / TestSelect / Cycles
```

Three-pass architecture:
- **Pass 1 — Parse:** tree-sitter extracts raw imports, calls, symbol definitions with line/column spans
- **Pass 2 — Resolve:** maps import specifiers to project files or external packages via shared `PathResolver`
- **Pass 3 — Infer (optional):** type inference for Java/Kotlin/TypeScript to resolve polymorphic calls (Phase 2)

### 1.3 Unified IR

Replaces separate file-level dependency graph and symbol-level call graph with a single intermediate representation consumed by all downstream modules.

Built during the orchestrator scan phase: each scanner produces `ParseResult` (raw nodes + edges) → resolver fills in paths → IR assembler merges into one `UnifiedIR` object. The `nx.DiGraph` is then derived from the IR for backward compatibility.

New dataclasses go in `deppulse/core/ir.py`.

```
IR Nodes:
  - FileNode(path, language, symbols[])
  - SymDef(name, fqn, type, file_path, line_range, visibility)

IR Edges:
  - ImportEdge(from_file, to_file, specifier, line, kind)
  - CallEdge(caller: SymDef, callee: SymDef, line, is_polymorphic, is_external)
```

`ScanResult` is still the public output of `BaseScanner.scan()`. The orchestrator internally converts ScanResults → IR → nx.DiGraph. CLI callers don't change.

---

## 2. Scanner Redesign

### 2.1 New Dependencies

| Package | Purpose | Status |
|---------|---------|--------|
| `tree-sitter` | Python binding for tree-sitter | **new** |
| `tree-sitter-kotlin` | Kotlin grammar | **new** |
| `tree-sitter-cpp` | C/C++ grammar | **new** |
| `tree-sitter-typescript` | TypeScript + TSX grammar | **new** |
| `tree-sitter-javascript` | JavaScript + JSX grammar | **new** |
| `tree-sitter-java` | Java grammar | replaces javalang (Phase 2) |
| `tree-sitter-python` | Python grammar | replaces ast (Phase 2) |

### 2.2 Shared Infrastructure

**TreeSitterParser** (abstract base):
```python
class TreeSitterParser(ABC):
    language_name: str
    def parse(self, source: bytes) -> Tree
    def query(self, tree: Tree, pattern: str) -> list[Node]
    def extract_imports(self, tree, file_path) -> list[RawImport]
    def extract_symbols(self, tree, file_path) -> list[SymDef]
    def extract_calls(self, tree, file_path) -> list[RawCall]
```

**PathResolver** (shared utility):
```python
class PathResolver:
    file_index: dict[str, Path]
    project_root: Path

    def resolve_relative(from_file, specifier) -> str | None
    def resolve_absolute(module_path) -> str | None
    def resolve_ts_alias(alias) -> list[str]           # tsconfig paths
    def resolve_java_package(class_fqn) -> str | None   # package root mapping
    def is_external(path) -> bool
    def is_stdlib(module, language) -> bool
```

Package root discovery:
- **Java/Kotlin:** walk `src/main/java`, `src/main/kotlin` (configurable in `deppulse.json`). The first directory above the package structure is the package root.
- **JS/TS:** use `package.json` `"exports"` and `"main"` fields for module entry points. TS `tsconfig.json` `"paths"` for alias resolution.
- **JS stdlib:** built-in Node.js modules list (`fs`, `path`, `http`, etc.) + browser globals.

**TypeInferrer** (optional, Phase 2):
```python
class TypeInferrer(ABC):
    def infer_receiver_type(call_node, scope) -> str | None
    def resolve_override(method, hierarchy) -> list[SymDef]
```

### 2.3 Language Matrix

| Language | Parser (Phase 1) | Resolver | TypeInfer | Effort |
|----------|------------------|----------|-----------|--------|
| Python | Keep `ast` module | package→path, relative imports | N/A (dynamic) | Refactor to Parser/Resolver interface |
| Java | Keep `javalang` | package root→file, wildcard expansion | Phase 2: override detection | Refactor + enhance Resolver |
| Kotlin | **tree-sitter-kotlin** (rewrite) | Same as Java + companion object, extension functions | Phase 2: override/open | Full rewrite |
| C/C++ | **tree-sitter-cpp** (rewrite) | #include local vs system, conditional preprocessing | N/A | Full rewrite |
| JavaScript | **tree-sitter-javascript** (new) | ESM specifiers, CJS require(), package.json exports | N/A | New |
| TypeScript | **tree-sitter-typescript** (new) | Same as JS + tsconfig paths, .d.ts | Phase 2: type-annotated inference | New |

### 2.4 Backward Compatibility

`BaseScanner` interface preserved. Internal implementation delegates to Parser/Resolver:
```python
class BaseScanner(ABC):
    name: str
    def can_scan(self, path: Path) -> bool    # unchanged
    def scan(self, file_path, project_root, file_index) -> ScanResult  # delegate internally
    def parse_file(self, file_path) -> ParseResult  # new: for callgraph
    @property
    def parser(self) -> TreeSitterParser       # new
    @property
    def resolver(self) -> PathResolver          # new
```

`ScanResult` still produced — callers (orchestrator, CLI) don't change.

---

## 3. Risk Model Redesign

### 3.1 Current (v0.2)

| Component | Weight |
|-----------|--------|
| blast_radius_percent | 50% |
| dependent_ratio | 20% |
| centrality_score | 15% |
| core_path_score | 10% |
| cycle_penalty | 5% |

Problems: weights hand-tuned, blast_radius dominates, no change nature or git history considered.

### 3.2 Target (v1.0): 4-factor model

| Factor | Weight | Sub-factors |
|--------|--------|-------------|
| Impact Radius | 30% | blast_pct (α=0.6) + avg_in_degree_norm (1-α) |
| Change Nature | 25% | file_count (0.15), line_count (0.25), API change (0.35), core_path (0.25) |
| Historical Hotspot | 25% | bug_fix_rate (0.4), churn_frequency (0.3), co_change_risk (0.3) |
| Coupling Risk | 20% | betweenness_centrality (0.4), cycle_participation (0.3), fan_ratio (0.3) |

### 3.3 Change Nature Detail (new)

**API change detection:** parse file at HEAD and HEAD~1, compare AST for function signatures, class methods, interface definitions. Only within changed line ranges.

Change types:
- `signature` — function/method signature changed → all callers affected
- `body` — function body changed → direct/indirect callers affected
- `new` — new symbol added → no upward impact
- `comment` — comment/docstring only → ignored

### 3.4 Historical Hotspot Detail (new)

Computed from `git log`:
```
bug_fix_rate = commits_with_fix_keywords / total_commits_for_file
churn_frequency = min(commits_last_90d / project_avg_commits, 3.0)
co_change_risk = P(this_file_changes | known_hotspot_file_changes)
```

Hotspot data stored in `.deppulse/hotspot-cache.json`, refreshed on each analysis.

Known hotspot files: files with `bug_fix_rate > project_avg_rate * 2`. The co-change matrix is built from the last 90 days of commits across all project files.

### 3.5 Weight Calibration

**Phase 1:** All weights configurable in `deppulse.json` under `risk.weights.*`.

**Phase 2:** Calibration script runs on 3 open-source repos:
1. Sample 100 historical commits
2. Use proxy labels: commit followed by revert/fix within 7 days → HIGH risk
3. Logistic regression to fit optimal weight values
4. Update defaults

### 3.6 Output

`RiskReport` unchanged: score (0-100), level (LOW/MEDIUM/HIGH), component breakdown with explanations. Components reduced from 5 to 4.

---

## 4. Test Selection

### 4.1 Current (v0.2)

```
changed files → reverse BFS on dependency graph → filter is_test_file → fallback-all if > max_blast
```

### 4.2 Target (v1.0): Line-level

```
git diff → DiffParser.extract_changed_symbols()
         → UnifiedIR.find_callers(symbol, transitive=True)
         → rank_by_chain_length() + cap(max_blast, strategy="closest")
         → TestSelectionResult (with coverage_confidence)
```

### 4.3 DiffParser (new)

Input: `git diff --unified=0` output. Output: `ChangedSymbol` list.

```python
class ChangedSymbol:
    file_path: str
    symbol_name: str
    change_type: "signature" | "body" | "new"
    line_range: (int, int)
    old_signature: str | None
    new_signature: str | None
```

### 4.4 Rank & Cap

No more `fallback_all`. Instead:
1. Sort tests by call-chain distance (ascending)
2. Direct tests (test imports changed file) prioritized over transitive
3. Cap at `max_blast`, keep the closest ones
4. Output `coverage_confidence`: % of changed symbols that are reachable from selected tests
5. If confidence < 50%, emit warning suggesting manual review

---

## 5. Real-world Validation

### 5.1 Benchmark Repos

| Repo | Language | Size | Validates |
|------|----------|------|-----------|
| Flask | Python | ~100 files | Python scanner accuracy |
| retrofit | Kotlin | ~200 files | Kotlin scanner vs. old regex |
| axios | JS/TS | ~50 files | JS/TS new scanner correctness |

### 5.2 Metrics

| Metric | Method |
|--------|--------|
| Import recall | detected imports / actual imports (manual inspection of 20 random files) |
| Import precision | correctly resolved imports / detected imports |
| Resolve accuracy | correctly resolved to project file / total internal imports |
| Call edge recall | detected calls / actual calls (manual sampling of 10 files) |
| Graph build time | wall-clock seconds for scan + graph build |
| Test select precision | returns only tests that actually cover changed code (manual 5 commits) |

### 5.3 Automation

`scripts/benchmark.py`:
1. Shallow-clone target repo
2. Run `deppulse scan` → JSON
3. Run `deppulse trace` for 10 random recent commits
4. Run `deppulse tests` for 5 commits with known test coverage
5. Manual sampling for precision/recall
6. Output `BENCHMARK.md` + `benchmark-results.json`

### 5.4 Expansion (Phase 3+)

Add `deppulse benchmark` subcommand that runs against user's own project and outputs comparative stats.

---

## 6. Implementation Phases

### Phase 1 (current scope)
- [x] Spec written & approved
- [ ] `PathResolver` shared utility
- [ ] `TreeSitterParser` base class
- [ ] Kotlin scanner rewrite (tree-sitter-kotlin)
- [ ] C/C++ scanner rewrite (tree-sitter-cpp)
- [ ] JavaScript scanner (tree-sitter-javascript)
- [ ] TypeScript scanner (tree-sitter-typescript)
- [ ] Refactor Python/Java scanners to Parser/Resolver interface
- [ ] Unified IR dataclasses
- [ ] Risk model: 4 factors + configurable weights + Change Nature + Historical Hotspot
- [ ] DiffParser + line-level test selection
- [ ] SnapshotManager adapts to UnifiedIR (new schema_version 2.0, backward compat)
- [ ] Benchmark script + 3 repos
- [ ] Update README, pyproject.toml dependencies

### Phase 2 (future)
- [ ] Migrate Python from `ast` to tree-sitter-python
- [ ] Migrate Java from `javalang` to tree-sitter-java
- [ ] TypeInferrer for Java/Kotlin/TypeScript
- [ ] Risk weight calibration via logistic regression

### Phase 3+ (future)
- [ ] `deppulse benchmark` subcommand
- [ ] CI benchmark regression job
- [ ] More languages (Go, Rust, C#)

---

---

## 7. Snapshot & Trend Monitoring

`SnapshotManager` adapts to the new pipeline:
- `save()` receives `UnifiedIR` instead of `GraphBuildResult`. Internally serializes IR nodes/edges with per-file metrics.
- `diff()` and `check_trends()` work unchanged — they only compare aggregate stats (file count, edge count, cycle count, per-file in-degree).
- Snapshot JSON schema adds a `schema_version: "2.0"` field. Old v1.0 snapshots are still readable via a compatibility path.

---

## 8. Non-goals

- Coverage data integration (too heavy for this stage)
- ML-based risk prediction
- Language-agnostic type inference engine
- Web-based UI dashboard improvements (current HTML+D3 is sufficient)
- Package manager-level dependency resolution (Maven/Gradle/npm graphs)
