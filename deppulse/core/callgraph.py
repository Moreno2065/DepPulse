"""Symbol-level call graph builder.

Builds a call graph on top of the file-level dependency graph, connecting
individual symbols (functions, methods, classes) with directed call edges.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from deppulse.models import (
    CallGraphResult,
    CallGraphStats,
    ConfidenceLevel,
    ConfidenceSource,
    ExtractedSymbol,
    Language,
    ScanResult,
    Symbol,
    SymbolCall,
    SymbolType,
)

# ---------------------------------------------------------------------------
# Symbol building helpers
# ---------------------------------------------------------------------------


def _extracted_symbol_to_symbol(ext: ExtractedSymbol, file_path: str, line_number: int = 0) -> Symbol:
    """Convert an ExtractedSymbol from a scanner into a full Symbol model."""
    type_map = {
        "function": SymbolType.FUNCTION,
        "class": SymbolType.CLASS,
        "method": SymbolType.METHOD,
        "property": SymbolType.PROPERTY,
        "constructor": SymbolType.CONSTRUCTOR,
        "interface": SymbolType.INTERFACE,
        "enum": SymbolType.ENUM,
        "annotation": SymbolType.ANNOTATION,
    }
    symbol_type = type_map.get(ext.symbol_type, SymbolType.UNKNOWN)
    return Symbol(
        file_path=file_path,
        name=ext.name,
        fully_qualified=ext.fully_qualified,
        symbol_type=symbol_type,
        language=Language.UNKNOWN,  # updated by caller
        line_number=line_number,
    )


def _build_symbol_index(scan_results: list[ScanResult]) -> dict[str, list[Symbol]]:
    """Build a mapping from normalized file path to list of Symbols."""
    index: dict[str, list[Symbol]] = {}

    def _resolve_lang(suffix: str) -> Language:
        from deppulse.models import get_language_from_suffix
        return get_language_from_suffix(suffix)

    for result in scan_results:
        if result.language == Language.UNKNOWN:
            continue
        symbols = [
            _extracted_symbol_to_symbol(ext, result.file_path)
            for ext in result.symbols
        ]
        # Set language on each symbol
        lang = _resolve_lang(result.suffix)
        for sym in symbols:
            sym.language = lang

        if symbols:
            index[result.file_path] = symbols

    return index


# ---------------------------------------------------------------------------
# Python call resolver
# ---------------------------------------------------------------------------


class _PyCallVisitor(ast.NodeVisitor):
    """Collect Python calls together with their lexical caller identity.

    Nested functions, lambdas, and dynamically-dispatched receivers are not
    guessed: the current scanner only indexes top-level functions and methods.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ast.expr, int]] = []
        self._owner: str | None = None
        self._class_name: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._owner is not None:
            return
        previous = self._class_name
        self._class_name = node.name
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(item)
        self._class_name = previous

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self._owner is not None:
            return
        self._owner = (
            f"method:{self._class_name}.{node.name}" if self._class_name
            else f"function:{node.name}"
        )
        for item in node.body:
            self.visit(item)
        self._owner = None

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        if self._owner is not None:
            self.calls.append((self._owner, node.func, node.lineno))
        self.generic_visit(node)


def _resolve_python_calls(
    scan_result: ScanResult,
    symbol_index: dict[str, list[Symbol]],
) -> list[SymbolCall]:
    """Resolve local and explicitly imported Python calls conservatively."""
    try:
        content = Path(scan_result.absolute_path).read_text(encoding="utf-8")
        tree = ast.parse(content)
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return []

    local = {sym.fully_qualified: sym for sym in symbol_index.get(scan_result.file_path, [])}
    indexed = {
        path: {sym.fully_qualified: sym for sym in symbols}
        for path, symbols in symbol_index.items()
    }

    def module_file(module: str, level: int = 0) -> str | None:
        if level:
            package = scan_result.file_path.split("/")[:-1]
            if level - 1 > len(package):
                return None
            prefix = package[:len(package) - (level - 1)]
            parts = prefix + (module.split(".") if module else [])
        else:
            parts = module.split(".") if module else []
        if not parts:
            return None
        module_path = "/".join(parts)
        names = (f"{module_path}.py", f"{module_path}/__init__.py")
        if level:
            matches = [path for path in indexed if path in names]
        else:
            matches = [
                path for path in indexed
                if path in names or any(path.endswith("/" + name) for name in names)
            ]
        return matches[0] if len(matches) == 1 else None

    module_aliases: dict[str, str] = {}
    imported_symbols: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = module_file(alias.name)
                if target and (alias.asname or "." not in alias.name):
                    module_aliases[alias.asname or alias.name] = target
        elif isinstance(node, ast.ImportFrom):
            target = module_file(node.module or "", node.level)
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                if target:
                    imported_symbols[name] = (target, alias.name)
                else:
                    nested = module_file(
                        f"{node.module}.{alias.name}" if node.module else alias.name,
                        node.level,
                    )
                    if nested:
                        module_aliases[name] = nested

    visitor = _PyCallVisitor()
    visitor.visit(tree)
    edges: list[SymbolCall] = []
    for owner_fqn, expression, line in visitor.calls:
        caller = local.get(owner_fqn)
        if caller is None:
            continue
        callee: Symbol | None = None
        if isinstance(expression, ast.Name):
            name = expression.id
            callee = local.get(f"function:{name}") or local.get(f"class:{name}")
            if callee is None and name in imported_symbols:
                target_file, original_name = imported_symbols[name]
                target_symbols = indexed[target_file]
                callee = (
                    target_symbols.get(f"function:{original_name}")
                    or target_symbols.get(f"class:{original_name}")
                )
        elif isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
            receiver = expression.value.id
            method = expression.attr
            if receiver in {"self", "cls"} and owner_fqn.startswith("method:"):
                class_name = owner_fqn.removeprefix("method:").split(".", 1)[0]
                callee = local.get(f"method:{class_name}.{method}")
            elif receiver in module_aliases:
                callee = indexed[module_aliases[receiver]].get(f"function:{method}")
            elif local.get(f"class:{receiver}"):
                callee = local.get(f"method:{receiver}.{method}")
            elif receiver in imported_symbols:
                target_file, original_name = imported_symbols[receiver]
                callee = indexed[target_file].get(f"method:{original_name}.{method}")
        if callee is None:
            continue
        edges.append(SymbolCall(
            caller=caller,
            callee=callee,
            call_site=(scan_result.file_path, line),
            is_polymorphic=False,
            is_external=caller.file_path != callee.file_path,
            confidence=ConfidenceLevel.AST,
            confidence_source=ConfidenceSource.STATIC_AST,
        ))
    return edges


# ---------------------------------------------------------------------------
# Java/Kotlin call resolver (approximate via regex)
# ---------------------------------------------------------------------------

_RE_JAVA_METHOD_CALL = re.compile(
    r"\b([A-Z][a-zA-Z0-9_]*)\.([a-z][a-zA-Z0-9_]*)\s*\(",
)
_RE_KOTLIN_METHOD_CALL = re.compile(
    r"\b([a-z][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
)


def _resolve_java_calls(
    scan_result: ScanResult,
    symbol_index: dict[str, list[Symbol]],
) -> list[SymbolCall]:
    """
    Approximate call graph for Java using regex method call matching.

    Confidence is HEURISTIC because this is based on pattern matching, not
    semantic analysis. The LSP path (via LSPCallGraphResolver) will upgrade
    these to LSP where available.
    """
    edges: list[SymbolCall] = []

    content = ""
    try:
        content = Path(scan_result.absolute_path).read_text(encoding="utf-8")
    except OSError:
        return edges

    # Build method name -> Symbol map
    method_map: dict[str, list[Symbol]] = {}
    class_map: dict[str, list[Symbol]] = {}
    for sym_list in symbol_index.values():
        for sym in sym_list:
            if sym.symbol_type == SymbolType.METHOD:
                method_map.setdefault(sym.name, []).append(sym)
            elif sym.symbol_type == SymbolType.CLASS:
                class_map.setdefault(sym.name, []).append(sym)

    for match in _RE_JAVA_METHOD_CALL.finditer(content):
        receiver = match.group(1)
        method_name = match.group(2)
        line_number = content[: match.start()].count("\n") + 1

        # Try to find a matching method
        candidates = method_map.get(method_name, [])

        # Also check for constructor calls (new Receiver())
        if method_name[0].isupper():
            candidates.extend(class_map.get(method_name, []))

        for callee in candidates:
            edges.append(
                SymbolCall(
                    caller=Symbol(
                        file_path=scan_result.file_path,
                        name=method_name,
                        fully_qualified=f"method:{receiver}.{method_name}",
                        symbol_type=SymbolType.UNKNOWN,
                        language=Language.JAVA,
                        line_number=line_number,
                    ),
                    callee=callee,
                    call_site=(scan_result.file_path, line_number),
                    is_polymorphic=True,
                    is_external=callee.file_path != scan_result.file_path,
                    confidence=ConfidenceLevel.HEURISTIC,
                    confidence_source=ConfidenceSource.NAME_MATCH,
                )
            )

    return edges


def _resolve_kotlin_calls(
    scan_result: ScanResult,
    symbol_index: dict[str, list[Symbol]],
) -> list[SymbolCall]:
    """
    Approximate call graph for Kotlin using regex method call matching.

    Confidence is HEURISTIC because this is based on pattern matching, not
    semantic analysis. The LSP path (via LSPCallGraphResolver) will upgrade
    these to LSP where available.
    """
    edges: list[SymbolCall] = []

    content = ""
    try:
        content = Path(scan_result.absolute_path).read_text(encoding="utf-8")
    except OSError:
        return edges

    method_map: dict[str, list[Symbol]] = {}
    class_map: dict[str, list[Symbol]] = {}
    for sym_list in symbol_index.values():
        for sym in sym_list:
            if sym.symbol_type in (SymbolType.METHOD, SymbolType.FUNCTION):
                method_map.setdefault(sym.name, []).append(sym)
            elif sym.symbol_type == SymbolType.CLASS:
                class_map.setdefault(sym.name, []).append(sym)

    for match in _RE_KOTLIN_METHOD_CALL.finditer(content):
        receiver = match.group(1)
        member = match.group(2)
        line_number = content[: match.start()].count("\n") + 1

        candidates = method_map.get(member, [])
        if member[0].isupper():
            candidates.extend(class_map.get(member, []))

        for callee in candidates:
            edges.append(
                SymbolCall(
                    caller=Symbol(
                        file_path=scan_result.file_path,
                        name=member,
                        fully_qualified=f"method:{receiver}.{member}",
                        symbol_type=SymbolType.UNKNOWN,
                        language=Language.KOTLIN,
                        line_number=0,
                    ),
                    callee=callee,
                    call_site=(scan_result.file_path, line_number),
                    is_polymorphic=False,
                    is_external=callee.file_path != scan_result.file_path,
                    confidence=ConfidenceLevel.HEURISTIC,
                    confidence_source=ConfidenceSource.NAME_MATCH,
                )
            )

    return edges


# ---------------------------------------------------------------------------
# Main CallGraphBuilder
# ---------------------------------------------------------------------------


@dataclass
class CallGraphBuilder:
    """
    Build a symbol-level call graph from scan results.

    Each node is a Symbol. Edges represent call relationships between symbols.
    Resolution is approximate for Java/Kotlin (regex-based); Python uses AST.
    """

    scan_results: list[ScanResult]
    project_root: str

    _symbol_index: dict[str, list[Symbol]] = field(default_factory=dict, init=False)
    _warnings: list[str] = field(default_factory=list, init=False)

    def build(self) -> CallGraphResult:
        """Build and return the symbol-level call graph."""
        # Phase 1: build symbol index
        self._symbol_index = _build_symbol_index(self.scan_results)

        # Phase 2: resolve call edges per file
        all_edges: list[SymbolCall] = []
        for result in self.scan_results:
            if result.error:
                continue

            if result.language == Language.PYTHON:
                edges = _resolve_python_calls(result, self._symbol_index)
            elif result.language == Language.JAVA:
                edges = _resolve_java_calls(result, self._symbol_index)
            elif result.language == Language.KOTLIN:
                edges = _resolve_kotlin_calls(result, self._symbol_index)
            else:
                edges = []

            all_edges.extend(edges)

        # Phase 3: deduplicate edges (same caller/callee pair)
        seen: set[tuple[str, str]] = set()
        unique_edges: list[SymbolCall] = []
        for edge in all_edges:
            key = (edge.caller.file_path, edge.caller.fully_qualified, edge.callee.file_path, edge.callee.fully_qualified)
            if key not in seen:
                seen.add(key)
                unique_edges.append(edge)

        # Phase 4: compute statistics
        stats = self._compute_stats(unique_edges)

        # Collect all nodes
        all_nodes: list[Symbol] = []
        for sym_list in self._symbol_index.values():
            all_nodes.extend(sym_list)

        return CallGraphResult(
            project_root=self.project_root,
            scanned_at=datetime.now(),
            nodes=all_nodes,
            edges=unique_edges,
            stats=stats,
            warnings=self._warnings,
        )

    def _compute_stats(self, edges: list[SymbolCall]) -> CallGraphStats:
        total_symbols = sum(len(v) for v in self._symbol_index.values())
        total_calls = len(edges)
        external_calls = sum(1 for e in edges if e.is_external)
        polymorphic_calls = sum(1 for e in edges if e.is_polymorphic)

        # Max call depth via BFS from each node
        max_depth = self._max_call_depth(edges)

        counts = {Language.PYTHON: 0, Language.JAVA: 0, Language.KOTLIN: 0, Language.CPP: 0}
        for sym_list in self._symbol_index.values():
            for sym in sym_list:
                if sym.language in counts:
                    counts[sym.language] += 1

        return CallGraphStats(
            total_symbols=total_symbols,
            total_calls=total_calls,
            external_calls=external_calls,
            polymorphic_calls=polymorphic_calls,
            max_call_depth=max_depth,
            python_symbols=counts[Language.PYTHON],
            java_symbols=counts[Language.JAVA],
            kotlin_symbols=counts[Language.KOTLIN],
            cpp_symbols=counts[Language.CPP],
        )

    def _max_call_depth(self, edges: list[SymbolCall]) -> int:
        """Compute the maximum call chain depth via BFS."""
        if not edges:
            return 0

        # Build adjacency list: callee -> callers (reversed for BFS from leaves)
        caller_map: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for edge in edges:
            caller_id = (edge.caller.file_path, edge.caller.fully_qualified)
            callee_id = (edge.callee.file_path, edge.callee.fully_qualified)
            caller_map.setdefault(caller_id, set()).add(callee_id)

        max_depth = 0
        for start_id in caller_map:
            visited = {start_id}
            q = deque([(start_id, 1)])
            while q:
                node, depth = q.popleft()
                max_depth = max(max_depth, depth)
                for neighbor in caller_map.get(node, ()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        q.append((neighbor, depth + 1))

        return max_depth


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def callgraph_to_mermaid(cg_result: CallGraphResult, *, max_nodes: int = 100) -> str:
    """
    Render a call graph as Mermaid flowchart code.

    Groups symbols by file and colors by language.
    Truncates to max_nodes to keep the output manageable.
    """
    lines = ["flowchart TB", "    %% Call graph generated by DepPulse"]

    # Color map by language
    colors = {
        Language.PYTHON: "4B8BBE",
        Language.JAVA: "B07219",
        Language.KOTLIN: "A18B00",
        Language.CPP: "F34B7D",
        Language.UNKNOWN: "AAAAAA",
    }

    # Group nodes by file
    file_groups: dict[str, list[Symbol]] = {}
    for sym in cg_result.nodes:
        file_groups.setdefault(sym.file_path, []).append(sym)

    shown = 0
    for file_path, syms in file_groups.items():
        if shown >= max_nodes:
            break
        subgraph_id = f"sg_{shown}"
        lines.append(f'    subgraph {subgraph_id} ["{_escape_mermaid(file_path)}"]')
        for sym in syms:
            if shown >= max_nodes:
                break
            color = colors.get(sym.language, "AAAAAA")
            node_id = f"n{shown}"
            label = _escape_mermaid(sym.name)
            type_emoji = _symbol_type_emoji(sym.symbol_type)
            lines.append(f'        {node_id}["{type_emoji} {label}"]:::lang_{sym.language.value}')
            shown += 1
        lines.append("    end")

    # Add style for each language
    for lang, color in colors.items():
        lines.append(f"    classDef lang_{lang.value} fill:#{color},color:#fff,stroke:#{color}")

    node_ids = {
        (sym.file_path, sym.fully_qualified): i
        for i, sym in enumerate(cg_result.nodes[:shown])
    }
    for edge in cg_result.edges:
        caller_idx = node_ids.get((edge.caller.file_path, edge.caller.fully_qualified))
        callee_idx = node_ids.get((edge.callee.file_path, edge.callee.fully_qualified))
        if caller_idx is not None and callee_idx is not None:
            edge_label = "virtual" if edge.is_polymorphic else ("ext" if edge.is_external else "")
            if edge_label:
                lines.append(f"    n{caller_idx} -->|\"{edge_label}\"| n{callee_idx}")
            else:
                lines.append(f"    n{caller_idx} --> n{callee_idx}")

    if shown == 0:
        lines.append('    empty["(no symbols found)"]')

    return "\n".join(lines)


def callgraph_to_dot(cg_result: CallGraphResult, *, title: str = "Call Graph") -> str:
    """Render a call graph as Graphviz DOT format."""
    lines = [
        "digraph callgraph {",
        f'    label="{_escape_dot(title)}";',
        "    rankdir=LR;",
        "    node [shape=box, fontname=Helvetica];",
    ]

    colors = {
        Language.PYTHON: "#4B8BBE",
        Language.JAVA: "#B07219",
        Language.KOTLIN: "#A18B00",
        Language.CPP: "#F34B7D",
        Language.UNKNOWN: "#AAAAAA",
    }

    for i, sym in enumerate(cg_result.nodes):
        color = colors.get(sym.language, "#AAAAAA")
        label = _escape_dot(sym.fully_qualified)
        style = "dashed" if sym.symbol_type == SymbolType.METHOD else "solid"
        lines.append(f'    n{i} [label="{label}", color="{color}", style={style}];')

    node_ids = {
        (sym.file_path, sym.fully_qualified): i
        for i, sym in enumerate(cg_result.nodes)
    }
    for edge in cg_result.edges:
        ci = node_ids.get((edge.caller.file_path, edge.caller.fully_qualified))
        ki = node_ids.get((edge.callee.file_path, edge.callee.fully_qualified))
        if ci is not None and ki is not None:
            attrs = []
            if edge.is_polymorphic:
                attrs.append('label="virtual"')
                attrs.append('style="dashed"')
            elif edge.is_external:
                attrs.append('label="ext"')
            attr_str = ", ".join(attrs)
            if attr_str:
                lines.append(f'    n{ci} -> n{ki} [{attr_str}];')
            else:
                lines.append(f"    n{ci} -> n{ki};")

    lines.append("}")
    return "\n".join(lines)


def callgraph_to_json(cg_result: CallGraphResult) -> dict:
    """Serialize a CallGraphResult to a JSON-friendly dict."""
    return {
        "project_root": cg_result.project_root,
        "scanned_at": cg_result.scanned_at.isoformat(),
        "stats": {
            "total_symbols": cg_result.stats.total_symbols,
            "total_calls": cg_result.stats.total_calls,
            "external_calls": cg_result.stats.external_calls,
            "polymorphic_calls": cg_result.stats.polymorphic_calls,
            "max_call_depth": cg_result.stats.max_call_depth,
            "python_symbols": cg_result.stats.python_symbols,
            "java_symbols": cg_result.stats.java_symbols,
            "kotlin_symbols": cg_result.stats.kotlin_symbols,
            "cpp_symbols": cg_result.stats.cpp_symbols,
        },
        "nodes": [
            {
                "file_path": s.file_path,
                "name": s.name,
                "fully_qualified": s.fully_qualified,
                "symbol_type": s.symbol_type.value,
                "language": s.language.value,
                "line_number": s.line_number,
                "signature": s.signature,
            }
            for s in cg_result.nodes
        ],
        "edges": [
            {
                "caller": e.caller.fully_qualified,
                "callee": e.callee.fully_qualified,
                "caller_file": e.caller.file_path,
                "callee_file": e.callee.file_path,
                "call_site": {"file_path": e.call_site[0], "line": e.call_site[1]},
                "is_polymorphic": e.is_polymorphic,
                "is_external": e.is_external,
            }
            for e in cg_result.edges
        ],
        "warnings": cg_result.warnings,
    }


def _escape_mermaid(text: str) -> str:
    """Escape text for safe embedding in Mermaid node labels."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _escape_dot(text: str) -> str:
    """Escape text for safe embedding in DOT labels."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _symbol_type_emoji(st: SymbolType) -> str:
    """Return a short prefix label for a symbol type."""
    labels = {
        SymbolType.FUNCTION: "fn",
        SymbolType.CLASS: "cls",
        SymbolType.METHOD: "mtd",
        SymbolType.PROPERTY: "prop",
        SymbolType.CONSTRUCTOR: "ctor",
        SymbolType.INTERFACE: "iface",
        SymbolType.ENUM: "enum",
        SymbolType.ANNOTATION: "ann",
        SymbolType.UNKNOWN: "?",
    }
    return labels.get(st, "?")
