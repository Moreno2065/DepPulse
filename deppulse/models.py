"""Strongly-typed dataclasses for all structured data in DepPulse."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DependencyKind(str, Enum):
    """The nature of a dependency reference in source code."""

    IMPORT = "import"          # Python: import x / from x import y
    JAVA_IMPORT = "java_import"  # Java: import com.example.Utils;
    KOTLIN_IMPORT = "kotlin_import"  # Kotlin: import com.example.Utils
    JAVASCRIPT_IMPORT = "javascript_import"  # JS: import x from 'y' / require('x')
    INCLUDE_LOCAL = "include_local"   # C/C++: #include "local.h"
    INCLUDE_SYSTEM = "include_system" # C/C++: #include <system>
    UNKNOWN = "unknown"


class Language(str, Enum):
    """Supported programming languages."""

    PYTHON = "python"
    JAVA = "java"
    KOTLIN = "kotlin"
    CPP = "cpp"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Risk severity classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConfidenceLevel(str, Enum):
    """
    Confidence level for a dependency edge or call edge.

    Each edge in the dependency graph and call graph carries a confidence
    annotation. This tells users how much to trust that edge — not all
    edges are equally reliable.

    Levels are ordered from most to least reliable.
    """

    # LSP-verified: returned by a Language Server Protocol tool
    # (e.g. textDocument/references, callHierarchy/incomingCalls).
    # This is the gold standard — the language's own type system confirmed it.
    LSP = "lsp"

    # AST/CST-verified: extracted by a real parser (ast module, tree-sitter,
    # javalang). The edge exists in the concrete syntax tree, which means
    # the dependency is syntactically present in the source.
    AST = "ast"

    # Heuristic: inferred by name matching, pattern analysis, or structural
    # rules. For example, a Java method call matched by name across the index.
    # Useful but noisy — can produce false positives.
    HEURISTIC = "heuristic"

    # Dynamic/runtime: detected at runtime via tracing, profiling, or
    # test coverage data. Confirmed to actually happen, but limited to
    # what the test suite/execution path covered.
    DYNAMIC = "dynamic"

    # Unknown: statically unresolvable. Could not determine the target.
    # This is honest — better than silently dropping it or claiming false certainty.
    UNKNOWN = "unknown"


class ConfidenceSource(str, Enum):
    """
    How the confidence level was determined.
    """

    # The source that produced the edge
    STATIC_AST = "static_ast"          # ast.parse, javalang, tree-sitter
    LSP_REFERENCES = "lsp_references"  # textDocument/references
    LSP_CALL_HIERARCHY = "lsp_call_hierarchy"  # callHierarchy/incomingCalls
    REGEX_PATTERN = "regex_pattern"      # string matching on source text
    NAME_MATCH = "name_match"          # symbol name lookup in index
    DYNAMIC_TRACE = "dynamic_trace"     # runtime call tracing
    RUNTIME_HOOK = "runtime_hook"       # import hook / monkey-patch
    UNRESOLVED = "unresolved"           # target could not be determined


class CycleSeverity(str, Enum):
    """Severity of dependency cycles in a project."""

    NONE = "NONE"
    MINOR = "MINOR"      # few cycles, small files
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"    # many cycles or large core files involved


# ---------------------------------------------------------------------------
# Dependency representations
# ---------------------------------------------------------------------------


@dataclass
class RawDependency:
    """
    A dependency reference extracted directly from source code.

    For Python, this captures the raw import statement text.
    For C/C++, this captures the raw include directive text.
    """

    raw_text: str
    kind: DependencyKind
    line_number: int
    column_offset: int = 0

    def __post_init__(self) -> None:
        self.raw_text = self.raw_text.strip()


@dataclass
class ResolvedDependency:
    """
    A dependency that has been resolved to a concrete project file path,
    or classified as external/stdlib.
    """

    raw: RawDependency
    normalized_path: str | None  # project-relative POSIX path, or None
    is_external: bool               # True if not a local project file
    is_stdlib: bool                 # True if Python stdlib
    is_unresolved: bool            # True if we could not resolve it
    resolution_note: str = ""       # e.g. "ambiguous: found 2 matches"
    # Confidence annotation (added in v1.0)
    confidence: ConfidenceLevel | None = None
    confidence_source: ConfidenceSource | None = None


# ---------------------------------------------------------------------------
# Scan result
# ---------------------------------------------------------------------------


@dataclass
class DynamicImport:
    """
    A dynamic import detected via a call expression (e.g. __import__(...),
    importlib.import_module(...)) that AST cannot resolve to a file path.
    """

    raw_text: str       # e.g. "__import__(os.environ['MOD'])"
    line_number: int
    import_type: str    # e.g. "__import__", "importlib.import_module"


@dataclass
class ExtractedSymbol:
    """A Python symbol (function, class, method) extracted from a module."""

    symbol_type: str      # "function", "class", "method"
    name: str
    fully_qualified: str  # e.g. "function:foo", "class:MyClass", "method:MyClass.method"


@dataclass
class ScanResult:
    """
    Result of scanning a single file.

    Contains all extracted dependencies, symbols, and any warnings
    encountered during scanning.
    """

    file_path: str               # project-relative POSIX path
    absolute_path: str           # absolute path on disk
    language: Language
    suffix: str                  # e.g. ".py", ".cpp"
    size_bytes: int
    raw_dependencies: list[RawDependency] = field(default_factory=list)
    resolved_dependencies: list[ResolvedDependency] = field(default_factory=list)
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    dynamic_imports: list[DynamicImport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None  # non-empty if scan failed catastrophically
    is_script: bool = False       # True for Kotlin .kts script files

    @property
    def internal_dependencies(self) -> list[ResolvedDependency]:
        return [d for d in self.resolved_dependencies if not d.is_external]

    @property
    def external_dependencies(self) -> list[ResolvedDependency]:
        return [d for d in self.resolved_dependencies if d.is_external]

    @property
    def unresolved_dependencies(self) -> list[ResolvedDependency]:
        return [d for d in self.resolved_dependencies if d.is_unresolved]


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


@dataclass
class NodeMetadata:
    """Metadata stored on each graph node."""

    path: str
    language: Language
    suffix: str
    size_bytes: int
    symbol_count: int
    unresolved_count: int
    external_count: int


@dataclass
class EdgeMetadata:
    """Metadata stored on each directed graph edge."""

    raw_text: str
    kind: DependencyKind
    line_number: int
    resolved_by: str
    confidence: ConfidenceLevel | None = None
    confidence_source: ConfidenceSource | None = None


@dataclass
class GraphStats:
    """Summary statistics about the built dependency graph."""

    total_files: int
    total_edges: int
    python_files: int
    java_files: int
    kotlin_files: int
    cpp_files: int
    javascript_files: int
    typescript_files: int
    unknown_files: int
    internal_edges: int
    external_edges: int
    unresolved_edges: int
    total_symbols: int
    language_breakdown: dict[str, int]
    files_with_cycles: int


@dataclass
class GraphBuildResult:
    """
    Complete result of scanning a project and building its dependency graph.
    """

    project_root: str
    scanned_at: datetime
    scan_results: list[ScanResult]
    total_files_found: int
    files_skipped: int
    files_with_errors: int
    stats: GraphStats
    warnings: list[str] = field(default_factory=list)
    # Unified IR built from scan results; None if scan() did not build it.
    # Available after DependencyOrchestrator.scan() completes.
    unified_ir: UnifiedIR | None = None


# Forward reference for the UnifiedIR type annotation above.
# The actual import lives in core/ir.py to avoid circular deppulse → ir → models.
class UnifiedIR:
    """Placeholder; resolved lazily at runtime via get_type()."""

    pass


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------


@dataclass
class ImpactChain:
    """A single path from an affected file back to the mutated source."""

    chain: list[str]    # list of project-relative POSIX paths, source->...->mutated
    length: int        # number of hops


@dataclass
class PerFileImpact:
    """Impact report for a single changed file."""

    mutated_file: str
    affected_files: list[str]
    directly_affected: list[str]
    impact_chains: list[ImpactChain]
    total_affected: int
    blast_radius_percent: float
    connected_component_size: int = 0  # size of the weakly-connected component containing this file


@dataclass
class ImpactReport:
    """
    Full impact analysis report for one or more changed files.
    """

    mutated_files: list[str]
    all_affected_files: list[str]
    per_file_impact: list[PerFileImpact]
    combined_affected_count: int
    total_files_in_project: int
    blast_radius_percent: float
    risk_score: float
    risk_level: RiskLevel
    connected_component_size: int = 0  # size of the largest weakly-connected component among mutated files


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


@dataclass
class RiskComponent:
    """One component of the risk score with its weight and contribution."""

    name: str
    weight: float
    raw_value: float      # the un-normalized value
    normalized_value: float  # 0.0 - 1.0
    contribution: float     # weight * normalized_value
    explanation: str


@dataclass
class RiskReport:
    """
    Detailed risk assessment for one or more files.
    """

    score: float         # 0 - 100
    level: RiskLevel
    components: list[RiskComponent]
    involved_files: list[str]
    explanation: str


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


@dataclass
class CycleInfo:
    """Details of a single dependency cycle."""

    nodes: list[str]    # project-relative POSIX paths
    length: int


@dataclass
class CycleReport:
    """
    Report of all dependency cycles detected in the project graph.
    """

    cycle_count: int
    cycles: list[CycleInfo]
    top_cycle_participants: list[tuple[str, int]]  # (path, count) sorted by count desc
    severity: CycleSeverity
    total_files_in_cycles: int


# ---------------------------------------------------------------------------
# Symbol-level call graph
# ---------------------------------------------------------------------------


class SymbolType(str, Enum):
    """The kind of a symbol in a call graph."""

    FUNCTION = "function"     # Python top-level function
    CLASS = "class"          # Class or type
    METHOD = "method"        # Java/Kotlin method, Python class method
    PROPERTY = "property"    # Kotlin/JS property / Python attribute
    CONSTRUCTOR = "constructor"  # Java/Kotlin constructor
    INTERFACE = "interface"  # Java/Kotlin interface
    ENUM = "enum"            # Java/Kotlin enum
    ANNOTATION = "annotation"  # Java annotation
    UNKNOWN = "unknown"


@dataclass
class Symbol:
    """
    A symbol (function, class, method, etc.) identified during scanning.
    The primary node type in a call graph.
    """

    file_path: str            # project-relative POSIX path
    name: str                 # simple name (e.g. "processData")
    fully_qualified: str      # e.g. "com.example.Utils.processData" or "method:Utils.processData"
    symbol_type: SymbolType
    language: Language
    line_number: int = 0
    signature: str | None = None  # e.g. "(str, int) -> bool"


@dataclass
class SymbolCall:
    """
    A directed call edge in the symbol-level call graph.
    Represents that `callee` is called from within `caller`.
    """

    caller: Symbol
    callee: Symbol
    call_site: tuple[str, int]  # (file_path, line_number)
    is_polymorphic: bool = False  # virtual dispatch (Java/C++ override)
    is_external: bool = False     # cross-module / external library call
    confidence: ConfidenceLevel | None = None
    confidence_source: ConfidenceSource | None = None


@dataclass
class CallGraphStats:
    """Statistics about a built call graph."""

    total_symbols: int
    total_calls: int
    external_calls: int
    polymorphic_calls: int
    max_call_depth: int        # deepest call chain found
    python_symbols: int = 0
    java_symbols: int = 0
    kotlin_symbols: int = 0
    cpp_symbols: int = 0
    javascript_symbols: int = 0


@dataclass
class CallGraphResult:
    """
    Complete result of building a symbol-level call graph from scan results.
    """

    project_root: str
    scanned_at: datetime
    nodes: list[Symbol]
    edges: list[SymbolCall]
    stats: CallGraphStats
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit report
# ---------------------------------------------------------------------------


@dataclass
class TopFileEntry:
    """A file with its dependency count, for reporting top-N lists."""

    path: str
    count: int
    language: Language


@dataclass
class AuditReport:
    """
    Comprehensive audit report combining graph stats, cycle info, and risk.
    This is the primary output of the `report` command.
    """

    project_path: str
    generated_at: datetime
    graph_stats: GraphStats
    cycle_report: CycleReport | None
    top_depended_on: list[TopFileEntry]      # files depended on most (in-degree)
    top_outgoing: list[TopFileEntry]         # files depending on most (out-degree)
    unresolved_summary: list[ResolvedDependency]
    external_summary: list[ResolvedDependency]
    high_risk_files: list[str]
    scan_duration_seconds: float


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def normalize_path_to_posix(path: str, project_root: str) -> str:
    """
    Convert an absolute or mixed path to a project-relative POSIX path.

    On Windows, this converts backslashes to forward slashes and strips
    the project_root prefix. On Unix, just strips the prefix.
    """
    # Normalize to absolute path first
    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(project_root)

    try:
        rel = os.path.relpath(abs_path, abs_root)
    except ValueError:
        # On Windows, relpath fails if paths are on different drives
        rel = path

    # Convert to POSIX (forward slashes)
    parts = PureWindowsPath(rel).parts if os.name == "nt" else PurePosixPath(rel).parts
    return str(PurePosixPath(*parts))


def get_language_from_suffix(suffix: str) -> Language:
    """Map a file suffix to its Language enum value."""
    mapping = {
        ".py": Language.PYTHON,
        ".java": Language.JAVA,
        ".kt": Language.KOTLIN,
        ".kts": Language.KOTLIN,
        ".c": Language.CPP,
        ".cc": Language.CPP,
        ".cpp": Language.CPP,
        ".cxx": Language.CPP,
        ".h": Language.CPP,
        ".hh": Language.CPP,
        ".hpp": Language.CPP,
        ".hxx": Language.CPP,
        ".js": Language.JAVASCRIPT,
        ".jsx": Language.JAVASCRIPT,
        ".mjs": Language.JAVASCRIPT,
        ".ts": Language.TYPESCRIPT,
        ".tsx": Language.TYPESCRIPT,
    }
    return mapping.get(suffix.lower(), Language.UNKNOWN)


# ---------------------------------------------------------------------------
# Test selection (Scenario A)
# ---------------------------------------------------------------------------


@dataclass
class TestSelectionResult:
    """
    Result of selecting which tests to run based on changed source files.
    """

    changed_files: list[str]
    selected_tests: list[str]
    by_strategy: dict[str, list[str]]
    total_affected: int
    blast_radius_percent: float
    max_blast_reached: bool
    fallback_all: bool
    coverage_confidence: float = 0.0   # v1.0: % of changed symbols reachable from selected tests
    changed_symbols: list[str] = field(default_factory=list)  # v1.0: symbol names changed


# ---------------------------------------------------------------------------
# Snapshot & trend monitoring (Scenario B)
# ---------------------------------------------------------------------------


@dataclass
class FileMetrics:
    """Per-file metrics captured in a snapshot."""

    path: str
    in_degree: int
    out_degree: int
    centrality: float


@dataclass
class SnapshotMeta:
    """
    Metadata for a saved dependency-graph snapshot.
    """

    tag: str
    commit_hash: str
    commit_message: str
    saved_at: datetime
    project_root: str
    total_files: int
    total_edges: int
    cycle_count: int
    files_in_cycles: int
    file_metrics: dict[str, FileMetrics]


@dataclass
class SnapshotDiff:
    """
    Delta between two snapshots for trend analysis.
    """

    older: SnapshotMeta
    newer: SnapshotMeta
    new_cycles_added: list[CycleInfo]
    total_edges_delta: int
    files_delta: int
    alerts: list[str]


@dataclass
class TrendAlert:
    """
    A single alert raised during snapshot comparison.
    """

    metric: str
    threshold: str
    older_value: float
    newer_value: float
    severity: str


# ---------------------------------------------------------------------------
# PR impact report (Scenario C)
# ---------------------------------------------------------------------------


@dataclass
class FileRiskEntry:
    """A file with its in-degree and risk level for PR reporting."""

    path: str
    in_degree: int
    risk_level: RiskLevel


@dataclass
class PRReportResult:
    """
    Structured result of a PR impact report.
    """

    changed_files: list[str]
    affected_files: list[str]
    blast_radius: float
    blast_radius_percent: float
    risk_score: float
    risk_level: RiskLevel
    suggested_tests: list[str]
    top_affected: list[FileRiskEntry]
    markdown_body: str


# ---------------------------------------------------------------------------
# Confidence utilities
# ---------------------------------------------------------------------------


def infer_confidence_from_resolution(
    is_unresolved: bool,
    is_external: bool,
    is_stdlib: bool,
    resolved_by: str,
) -> tuple[ConfidenceLevel, ConfidenceSource]:
    """
    Infer a confidence level from the resolution result of a dependency edge.

    This is the default confidence attribution applied when no explicit
    LSP or dynamic analysis has been performed. It is conservative and
    honest about its own limitations.

    Parameters
    ----------
    is_unresolved : bool
        Whether the dependency could not be resolved to a project file.
    is_external : bool
        Whether the dependency points outside the project.
    is_stdlib : bool
        Whether the dependency is a known standard library.
    resolved_by : str
        The scanner name that produced this edge.

    Returns
    -------
    tuple[ConfidenceLevel, ConfidenceSource]
        The inferred confidence level and source.
    """
    if is_unresolved:
        return (ConfidenceLevel.UNKNOWN, ConfidenceSource.UNRESOLVED)

    # External dependencies (third-party packages) are confirmed by the scanner's
    # external detection logic — not just string matching.
    if is_external:
        return (ConfidenceLevel.AST, ConfidenceSource.STATIC_AST)

    # A resolved internal dependency — comes from the AST/CST, so AST-level confidence.
    # The scanner walked the parse tree and matched the result to a project file.
    return (ConfidenceLevel.AST, ConfidenceSource.STATIC_AST)


def confidence_to_score(level: ConfidenceLevel | None) -> float:
    """
    Convert a confidence level to a numeric score (0.0–1.0).

    Used for sorting, filtering, and reporting.
    """
    if level is None:
        return 0.5
    score_map = {
        ConfidenceLevel.LSP: 1.0,
        ConfidenceLevel.AST: 0.85,
        ConfidenceLevel.HEURISTIC: 0.5,
        ConfidenceLevel.DYNAMIC: 0.95,
        ConfidenceLevel.UNKNOWN: 0.0,
    }
    return score_map.get(level, 0.5)


def confidence_emoji(level: ConfidenceLevel | None) -> str:
    """Return a brief visual label for a confidence level."""
    if level is None:
        return "[?]"
    labels = {
        ConfidenceLevel.LSP: "[LSP]",
        ConfidenceLevel.AST: "[AST]",
        ConfidenceLevel.HEURISTIC: "[HRS]",
        ConfidenceLevel.DYNAMIC: "[DYN]",
        ConfidenceLevel.UNKNOWN: "[UNK]",
    }
    return labels.get(level, "[?]")


def describe_confidence(level: ConfidenceLevel | None, source: ConfidenceSource | None) -> str:
    """
    Return a human-readable description of the confidence annotation.
    """
    if level is None or source is None:
        return "Confidence not assessed"

    source_desc = {
        ConfidenceSource.STATIC_AST: "verified by AST/CST parser",
        ConfidenceSource.LSP_REFERENCES: "confirmed by LSP textDocument/references",
        ConfidenceSource.LSP_CALL_HIERARCHY: "confirmed by LSP callHierarchy",
        ConfidenceSource.REGEX_PATTERN: "matched by regex pattern",
        ConfidenceSource.NAME_MATCH: "resolved by symbol name matching",
        ConfidenceSource.DYNAMIC_TRACE: "observed at runtime via tracing",
        ConfidenceSource.RUNTIME_HOOK: "observed at runtime via import hook",
        ConfidenceSource.UNRESOLVED: "target could not be statically determined",
    }.get(source, str(source.value))

    return f"{level.value.upper()} confidence — {source_desc}"
