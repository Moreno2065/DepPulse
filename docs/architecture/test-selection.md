# Test Selection

DepPulse selects which tests to run based on the changed symbols, not just changed files.

## Pipeline

```
git diff → DiffParser.extract_changed_symbols()
         → UnifiedIR.find_callers(symbol, transitive=True)
         → rank_by_chain_length() + cap(max_blast, strategy="closest")
         → TestSelectionResult (with coverage_confidence)
```

## Symbol-level selection

Instead of selecting all tests that import a changed *file*, DepPulse:

1. Parses the git diff to extract changed symbols (function signatures, class definitions, etc.)
2. For each changed symbol, finds all callers transitively via the unified IR
3. Ranks tests by call-chain distance (closest first)
4. Caps at `max_blast` to avoid running too many tests

## Change type classification

The `DiffParser` classifies each change as:

| Type | Description |
|------|-------------|
| `SIGNATURE` | Function/method signature changed — all callers affected |
| `BODY` | Function body changed — direct/indirect callers affected |
| `NEW` | New symbol added — no upward impact |
| `COMMENT` | Comment/docstring only — ignored |

## Coverage confidence

`coverage_confidence` is the percentage of changed symbols that are reachable from at least one selected test.

If confidence < 50%, DepPulse emits a warning recommending manual review.

## Convention-based fallback

For files not in the dependency graph, DepPulse falls back to conventional test paths:

- `src/foo.py` → `tests/test_foo.py`
- `deppulse/core/analyzer.py` → `tests/test_analyzer.py`
