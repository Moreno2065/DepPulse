# Configuration

## `deppulse.json`

Place a `deppulse.json` file in your project root to customize behavior.

```json
{
  "project_root": ".",
  "ignore_dirs": ["node_modules", ".git", "__pycache__", ".pytest_cache"],
  "ignore_files": ["*.min.js", "*.bundle.js", "setup.py"],
  "risk": {
    "weights": {
      "impact_radius_weight": 0.30,
      "change_nature_weight": 0.25,
      "historical_hotspot_weight": 0.25,
      "coupling_risk_weight": 0.20
    }
  },
  "scanners": {
    "max_file_size_kb": 5000,
    "kotlin": {
      "package_roots": ["src/main/kotlin", "src"]
    },
    "java": {
      "package_roots": ["src/main/java", "src"]
    }
  }
}
```

## Configuration options

### General

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `project_root` | string | `"."` | Root directory of the project |
| `ignore_dirs` | list[string] | See defaults | Directory names to skip |
| `ignore_files` | list[string] | `[]` | File patterns to skip (fnmatch) |
| `max_file_size_kb` | int | `5000` | Skip files larger than this |

### Risk model weights

| Key | Default | Description |
|-----|---------|-------------|
| `risk.weights.impact_radius_weight` | `0.30` | Weight for blast radius factor |
| `risk.weights.change_nature_weight` | `0.25` | Weight for change nature factor |
| `risk.weights.historical_hotspot_weight` | `0.25` | Weight for historical hotspot factor |
| `risk.weights.coupling_risk_weight` | `0.20` | Weight for coupling risk factor |

### Scanner options

| Key | Type | Description |
|-----|------|-------------|
| `scanners.max_file_size_kb` | int | Max file size in KB |
| `scanners.kotlin.package_roots` | list[string] | Java/Kotlin package roots |
| `scanners.java.package_roots` | list[string] | Java package roots |

## Environment variables

- `DEPPULSE_CACHE_DIR` — Override the cache directory (default: `.deppulse/cache`)
- `DEPPULSE_SNAPSHOT_DIR` — Override the snapshot directory (default: `.deppulse/snapshots`)
