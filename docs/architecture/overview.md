# Architecture Overview

## Pipeline

DepPulse uses a multi-pass architecture to analyze source code dependencies:

```
File → Scanner.scan() → ScanResult
                              ↓
              build_unified_ir(scan_results) → UnifiedIR
                              ↓
              orchestrator._build_graph() → nx.DiGraph
                              ↓
        Analyzer | Risk | TestSelector | CycleDetector
```

## Components

### `DependencyOrchestrator` (`core/orchestrator.py`)

The main orchestration class. Responsible for:
- Walking the project tree and building the file index
- Dispatching files to language-specific scanners
- Building the unified IR
- Constructing the networkx DiGraph

### `UnifiedIR` (`core/ir.py`)

The unified intermediate representation for a project scan. Contains:
- `FileNode` — a source file with its extracted symbols
- `SymDef` — a resolved symbol definition
- `ImportEdge` — a dependency from one file to another
- `CallEdge` — a call relationship between two symbols

### Language Scanners (`scanners/*.py`)

Each language has its own scanner implementing the `BaseScanner` interface:

| Scanner | Parser |
|---------|--------|
| `PythonScanner` | `ast` module |
| `JavaScanner` | `javalang` |
| `KotlinScanner` | tree-sitter |
| `CppScanner` | tree-sitter |
| `JavaScriptScanner` | tree-sitter |
| `TypeScriptScanner` | tree-sitter |

### `PathResolver` (`core/path_resolver.py`)

Shared path resolution utility used by all scanners. Supports:
- Python package resolution
- Java/Kotlin FQN resolution
- C++ include resolution
- JavaScript/TypeScript module resolution with tsconfig paths

### `ImpactAnalyzer` (`core/analyzer.py`)

Analyzes the impact of changed files by traversing the dependency graph.

### `RiskScorer` (`core/risk.py`)

Computes a 4-factor risk score (0-100) for changed files.

### `TestSelector` (`core/test_selector.py`)

Selects tests based on changed symbols using the unified IR and graph traversal.

### `SnapshotManager` (`core/snapshot.py`)

Saves, loads, and compares dependency graph snapshots for trend monitoring.
