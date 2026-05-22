# Changelog

## v1.1.0 (2026-05-22)

### Features

- **UnifiedIR integration**: `DependencyOrchestrator.scan()` now builds a `UnifiedIR` from scan results and attaches it to `GraphBuildResult.unified_ir`, enabling symbol-level analysis throughout the pipeline
- **Enhanced PathResolver**: Added `resolve_ts_alias()` for TypeScript path alias resolution via `tsconfig.json` `compilerOptions.paths`, and `resolve_java_package()` as a public wrapper for Java/Kotlin FQN resolution
- **Snapshot v2.1**: `SnapshotManager.save()` now accepts `UnifiedIR` when available, storing the full IR structure (file nodes, edges, symbol count) in the snapshot JSON for richer historical analysis
- **Documentation**: Added comprehensive mkdocs-based API documentation with Material theme

### Bug fixes

- Fixed `NameError` in `build_unified_ir()`: the function now correctly creates a `UnifiedIR` instance before populating it with file nodes and edges (critical bug that would have prevented symbol-level analysis from working)

### Dependencies

- Added `mkdocs`, `mkdocstrings[python]`, and `mkdocs-material` as optional `docs` extras
- Bumped version to `1.1.0`

## v1.0.0 (2026-05-22)

Initial release of DepPulse with:

- Multi-language scanning (Python, Java, Kotlin, C/C++, JavaScript, TypeScript)
- Dependency graph construction via networkx
- Change impact analysis
- 4-factor risk scoring model
- Intelligent test selection
- Dependency cycle detection
- Snapshot and trend monitoring
- JSON, Markdown, and SARIF report output
- Interactive HTML dashboard
