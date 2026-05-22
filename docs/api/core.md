# deppulse.core

Core analysis components.

::: deppulse.core
    options:
      show_root_heading: true
      show_source: false
```

## Key components

### `DependencyOrchestrator`

Located in `deppulse/core/orchestrator.py`.

The main orchestration class that walks a project tree, dispatches to scanners, builds the dependency graph, and produces a `GraphBuildResult`.

### `UnifiedIR`

Located in `deppulse/core/ir.py`.

The unified intermediate representation for a project scan. Built from scan results and used to derive the networkx DiGraph.

### `PathResolver`

Located in `deppulse/core/path_resolver.py`.

Shared path resolution utility for all language scanners. Resolves Python packages, Java/Kotlin FQNs, C++ includes, and JavaScript/TypeScript modules.

### `ImpactAnalyzer`

Located in `deppulse/core/analyzer.py`.

Analyzes the propagation of changes through the dependency graph.

### `RiskScorer`

Located in `deppulse/core/risk.py`.

Computes a 4-factor risk score (0-100) for changed files.

### `TestSelector`

Located in `deppulse/core/test_selector.py`.

Selects which tests to run based on changed symbols.

### `SnapshotManager`

Located in `deppulse/core/snapshot.py`.

Saves, loads, and compares dependency graph snapshots.

### `DiffParser`

Located in `deppulse/core/diff_parser.py`.

Parses git diff output to extract changed symbols with line-level precision.
