# Quick Start

## Basic scan

```bash
deppulse scan /path/to/project
```

This walks the project tree, scans supported files, builds a dependency graph, and prints a summary table.

## Report output

```bash
# JSON report
deppulse report /path/to/project --format json --output report.json

# Markdown report
deppulse report /path/to/project --format markdown --output report.md

# SARIF (for CI integration)
deppulse report /path/to/project --format sarif --output report.sarif
```

## Change impact analysis

```bash
# Analyze impact of uncommitted changes
deppulse impact /path/to/project --diff

# Analyze specific files
deppulse impact /path/to/project --files src/foo.py src/bar.py
```

## Test selection

```bash
# Select tests for uncommitted changes
deppulse tests /path/to/project --diff

# Limit to 20 tests max
deppulse tests /path/to/project --diff --max-blast 20
```

## Snapshot and trend monitoring

```bash
# Save a snapshot
deppulse snapshot save /path/to/project --tag v1.0.0

# Compare two snapshots
deppulse snapshot diff /path/to/project --from v1.0.0 --to v1.1.0

# Check trends since last snapshot
deppulse snapshot trend /path/to/project --since v1.0.0
```

## Cycle detection

```bash
deppulse cycles /path/to/project
```
