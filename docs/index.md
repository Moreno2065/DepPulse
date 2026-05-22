# DepPulse

DepPulse is a local source-code **dependency topology** and **change-impact auditing** CLI tool.

## Features

- **Multi-language support**: Python, Java, Kotlin, C/C++, JavaScript, TypeScript
- **Dependency graph**: Build a directed graph of file-level dependencies across all supported languages
- **Change impact analysis**: Understand what code is affected by a given change
- **Risk scoring**: 4-factor model for assessing change risk
- **Test selection**: Intelligent test selection based on dependency analysis
- **Cycle detection**: Find and report dependency cycles
- **Trend monitoring**: Track dependency graph evolution over time with snapshots

## Quick start

```bash
# Install
pip install -e .

# Scan a project
deppulse scan /path/to/project

# Show table of dependencies
deppulse scan /path/to/project --show-table

# Check for cycles
deppulse cycles /path/to/project

# Analyze impact of changes
deppulse impact /path/to/project --files src/utils/foo.py

# Select tests to run
deppulse tests /path/to/project --diff
```

## Architecture

DepPulse uses a multi-pass pipeline:

```
File → Scanner → ScanResult → UnifiedIR → nx.DiGraph → Analyzer/Risk/TestSelect
```

- **Pass 1** — Parse: language-specific scanners extract raw imports, calls, and symbol definitions
- **Pass 2** — Resolve: `PathResolver` maps import specifiers to project files or external packages
- **Pass 3** — Analyze: unified IR feeds into risk scoring, test selection, and cycle detection

## License

MIT
