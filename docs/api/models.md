# deppulse.models

All strongly-typed dataclasses for structured data in DepPulse.

::: deppulse.models
    options:
      show_root_heading: true
      show_source: false
```

## Key models

### Graph building

- `ScanResult` — Result of scanning a single file
- `GraphBuildResult` — Complete result of scanning a project
- `GraphStats` — Summary statistics about the dependency graph
- `NodeMetadata` — Metadata stored on each graph node
- `EdgeMetadata` — Metadata stored on each directed edge

### Analysis

- `ImpactReport` — Full impact analysis report
- `PerFileImpact` — Impact report for a single changed file
- `ImpactChain` — A single path from an affected file back to the source

### Risk

- `RiskReport` — Detailed risk assessment
- `RiskComponent` — One component of the risk score with weight and contribution
- `RiskLevel` — Risk severity classification (LOW / MEDIUM / HIGH)

### Cycles

- `CycleReport` — Report of all detected dependency cycles
- `CycleInfo` — Details of a single dependency cycle

### Test selection

- `TestSelectionResult` — Result of test selection

### Snapshots

- `SnapshotMeta` — Metadata for a saved snapshot
- `SnapshotDiff` — Delta between two snapshots
- `TrendAlert` — Alert raised during snapshot comparison
- `FileMetrics` — Per-file metrics captured in a snapshot
