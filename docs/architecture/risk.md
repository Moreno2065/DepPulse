# Risk Model

DepPulse uses a transparent, explainable 4-factor risk model. The score ranges from 0 to 100.

## Score thresholds

| Score | Level | Interpretation |
|-------|-------|----------------|
| 0–30 | **LOW** | Low-impact change, safe to merge |
| 30–70 | **MEDIUM** | Moderate impact, review recommended |
| 70–100 | **HIGH** | High-impact change, careful review required |

## 4-factor model

| Factor | Weight | Sub-factors |
|--------|--------|-------------|
| **Impact Radius** | 30% | blast_pct (α=0.6) + avg_in_degree_norm |
| **Change Nature** | 25% | file_count, line_count, API change, core_path |
| **Historical Hotspot** | 25% | bug_fix_rate, churn_frequency, co_change_risk |
| **Coupling Risk** | 20% | betweenness, cycle_participation, fan_ratio |

## Factor details

### Impact Radius (30%)

How widely the change propagates through the dependency graph. A file that many others depend on is high-risk.

- **blast_pct**: percentage of project files in the affected set
- **avg_in_degree_norm**: normalized maximum in-degree of changed files

### Change Nature (25%)

What kind of change it is. API signature changes are higher-risk than body-only changes.

- **file_count**: how many files changed (cap at 20)
- **line_count**: total lines changed (cap at 500)
- **api_change**: proportion of signature vs. body changes
- **core_path**: whether changed files are in core directories (`core/`, `base/`, `shared/`, etc.)

### Historical Hotspot (25%)

Whether the file has a history of frequent bug fixes or churn.

Computed from `git log`:
- **bug_fix_rate**: commits with fix keywords / total commits for file
- **churn_frequency**: commits in last 90 days / project average
- **co_change_risk**: probability of co-change with known hotspots

### Coupling Risk (20%)

How entangled the file is in the dependency graph.

- **betweenness_centrality**: how central the file is in the graph
- **cycle_participation**: whether the file is in dependency cycles
- **fan_ratio**: out_degree / (in_degree + out_degree)

## Customizing weights

All weights are configurable in `deppulse.json`:

```json
{
  "risk": {
    "weights": {
      "impact_radius_weight": 0.30,
      "change_nature_weight": 0.25,
      "historical_hotspot_weight": 0.25,
      "coupling_risk_weight": 0.20
    }
  }
}
```
