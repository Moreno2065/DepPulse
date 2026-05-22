# Unified IR

The Unified Intermediate Representation (IR) is the central data structure in DepPulse v1.0+. It replaces the dual file-level dependency graph and symbol-level call graph with a single coherent structure consumed by all downstream modules.

## Structure

```python
@dataclass
class UnifiedIR:
    project_root: str
    scanned_at: datetime
    file_nodes: list[FileNode] = field(default_factory=list)
    sym_defs: list[SymDef] = field(default_factory=list)
    import_edges: list[ImportEdge] = field(default_factory=list)
    call_edges: list[CallEdge] = field(default_factory=list)
```

## Node types

### `FileNode`

Represents a source file in the IR.

```python
@dataclass
class FileNode:
    path: str              # project-relative POSIX path
    language: str          # e.g. "python", "java"
    suffix: str            # e.g. ".py", ".kt"
    symbols: list[RawSymbol]
    size_bytes: int
    warnings: list[str]
    error: str | None
```

### `SymDef`

A resolved symbol definition.

```python
@dataclass
class SymDef:
    name: str              # simple name, e.g. "process"
    fqn: str               # fully-qualified, e.g. "com.example.utils:process"
    sym_type: SymType
    file_path: str
    line_range: LineRange
    visibility: Visibility
    language: str
    owner: str | None
```

## Edge types

### `ImportEdge`

Represents a dependency from one file to another via an import/include directive.

```python
@dataclass
class ImportEdge:
    from_file: str         # project-relative path of the importing file
    to_file: str | None   # project-relative path of the imported file
    specifier: str         # raw import text, e.g. "com.example.Utils"
    import_kind: ImportKind
    line: int
    is_external: bool
    is_stdlib: bool
    is_unresolved: bool
    resolution_note: str
```

### `CallEdge`

Represents a call relationship from one symbol to another.

```python
@dataclass
class CallEdge:
    caller: SymDef
    callee: SymDef
    line: int
    is_polymorphic: bool
    is_external: bool
    call_site_file: str
```

## Building the IR

The IR is built automatically by `DependencyOrchestrator.scan()` via `build_unified_ir()`:

```python
ir = build_unified_ir(scan_results, str(project_root))
```

## Derived graph

The `nx.DiGraph` is derived from the IR:

```python
graph: nx.DiGraph = ir.to_dependency_graph()
```

This makes the IR the single source of truth while maintaining backward compatibility with the existing graph-based API.

## Index lookups

After calling `ir.build_indices()`, the following fast lookups are available:

- `ir.get_file(path)` — get a FileNode by path
- `ir.find_symdefs(name)` — find all symbol definitions by simple name
- `ir.find_symdef(fqn)` — find a symbol by fully-qualified name
- `ir.find_symdefs_in_file(file_path)` — find all symbols in a file
- `ir.find_callers(callee, transitive=True)` — find all callers of a symbol
