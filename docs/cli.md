# CLI Reference

## Global options

| Option | Description |
|--------|-------------|
| `--project-root`, `-r` | Project root directory (default: current directory) |
| `--config` | Path to `deppulse.json` config file |
| `--verbose`, `-v` | Enable verbose output |

## Commands

### `scan`

Scan the project and build the dependency graph.

```bash
deppulse scan [PROJECT_ROOT] [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--show-table` | Display dependency edges as a table |
| `--format json` | Output JSON |
| `--output FILE` | Write output to file |
| `--no-cache` | Disable caching |
| `--max-file-size KB` | Skip files larger than KB (default: 5000) |

### `impact`

Analyze change impact.

```bash
deppulse impact [PROJECT_ROOT] [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--files FILE [FILE ...]` | Files to analyze |
| `--diff` | Analyze uncommitted changes |
| `--max-chains N` | Maximum impact chains to report (default: 50) |

### `risk`

Compute risk score for changed files.

```bash
deppulse risk [PROJECT_ROOT] [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--files FILE [FILE ...]` | Files to analyze |
| `--diff` | Compute risk for uncommitted changes |

### `tests`

Select tests to run based on changed files.

```bash
deppulse tests [PROJECT_ROOT] [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--files FILE [FILE ...]` | Changed source files |
| `--diff` | Use uncommitted changes |
| `--max-blast N` | Maximum tests to select (default: 50) |

### `cycles`

Detect dependency cycles.

```bash
deppulse cycles [PROJECT_ROOT] [options]
```

### `report`

Generate an audit report.

```bash
deppulse report [PROJECT_ROOT] [options]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--format FORMAT` | Output format: `json`, `markdown`, `sarif` |
| `--output FILE` | Write output to file |

### `snapshot`

Manage dependency graph snapshots.

```bash
deppulse snapshot <subcommand> [options]
```

**Subcommands:**

- `save` — Save a snapshot
- `list` — List saved snapshots
- `diff` — Compare two snapshots
- `trend` — Check trends since a snapshot

### `doctor`

Run diagnostics.

```bash
deppulse doctor [PROJECT_ROOT]
```
