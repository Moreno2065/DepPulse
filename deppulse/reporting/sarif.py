"""SARIF 2.1.0 output generation for DepPulse."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from deppulse import __version__
from deppulse.models import (
    CycleInfo,
    CycleReport,
    GraphBuildResult,
    ResolvedDependency,
)


def graph_to_sarif(
    result: GraphBuildResult,
    *,
    project_root: str | None = None,
    cycle_report: CycleReport | None = None,
    tool_name: str = "DepPulse",
    tool_version: str = __version__,
) -> dict:
    """
    Convert a GraphBuildResult to SARIF 2.1.0 format.

    Args:
        result: The scan result to convert
        project_root: Override project root path (defaults to result.project_root)
        cycle_report: Optional cycle report to include
        tool_name: Tool driver name
        tool_version: Tool version string

    Returns:
        A SARIF 2.1.0 log dictionary
    """
    root = project_root or result.project_root or "/"

    sarif = {
        "version": "2.1.0",
        "$schema": (
            "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
        ),
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "informationUri": "https://github.com/deppulse/deppulse",
                        "rules": _build_rules(),
                    }
                },
                "results": _build_results(result, root),
                "files": _build_files(result, root),
            }
        ],
    }

    # Add cycle results if provided
    if cycle_report:
        cycle_results = _build_cycle_results(cycle_report, root)
        sarif["runs"][0]["results"].extend(cycle_results)

    return sarif


def _build_rules() -> list[dict]:
    """Build SARIF rule definitions for dependency kinds."""
    return [
        {
            "id": "internal-dependency",
            "name": "InternalDependency",
            "shortDescription": {"text": "Internal module dependency detected"},
            "fullDescription": {"text": "A file depends on another internal module in the project."},
            "defaultConfiguration": {"level": "note"},
            "properties": {"tags": ["internal", "dependency"]},
        },
        {
            "id": "external-dependency",
            "name": "ExternalDependency",
            "shortDescription": {"text": "External package dependency detected"},
            "fullDescription": {"text": "A file depends on an external package that is not part of the project."},
            "defaultConfiguration": {"level": "warning"},
            "properties": {"tags": ["external", "dependency"]},
        },
        {
            "id": "unresolved-dependency",
            "name": "UnresolvedDependency",
            "shortDescription": {"text": "Unresolved dependency detected"},
            "fullDescription": {"text": "A file has a dependency that could not be resolved to any known module."},
            "defaultConfiguration": {"level": "error"},
            "properties": {"tags": ["unresolved", "dependency"]},
        },
        {
            "id": "dependency-cycle",
            "name": "DependencyCycle",
            "shortDescription": {"text": "Circular dependency detected"},
            "fullDescription": {"text": "Files form a circular dependency chain, which can cause maintenance issues."},
            "defaultConfiguration": {"level": "warning"},
            "properties": {"tags": ["cycle", "dependency"]},
        },
    ]


def _build_results(result: GraphBuildResult, root: str) -> list[dict]:
    """Build SARIF results from scan results."""
    results = []
    root_uri = _make_root_uri(root)

    for scan_result in result.scan_results:
        file_uri = _make_uri(scan_result.file_path, root, root_uri)

        # Internal dependencies → note level
        for dep in scan_result.internal_dependencies:
            if dep.normalized_path:
                results.append({
                    "ruleId": "internal-dependency",
                    "level": "note",
                    "message": {
                        "text": f"Depends on internal module: {dep.normalized_path}"
                    },
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {"uri": file_uri},
                            "region": {"startLine": dep.raw.line_number},
                        }
                    }],
                    "properties": {
                        "dependency": dep.normalized_path,
                        "kind": dep.raw.kind.value,
                        "confidence": dep.confidence.value if dep.confidence else "none",
                        "confidence_source": dep.confidence_source.value if dep.confidence_source else "none",
                    },
                })

        # External dependencies → warning level
        for dep in scan_result.external_dependencies:
            results.append({
                "ruleId": "external-dependency",
                "level": "warning",
                "message": {
                    "text": f"External dependency: {dep.raw.raw_text}"
                },
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": file_uri},
                        "region": {"startLine": dep.raw.line_number},
                    }
                }],
                "properties": {
                    "dependency": dep.raw.raw_text,
                    "kind": dep.raw.kind.value,
                    "isStdlib": dep.is_stdlib,
                    "confidence": dep.confidence.value if dep.confidence else "none",
                    "confidence_source": dep.confidence_source.value if dep.confidence_source else "none",
                },
            })

        # Unresolved dependencies → error level
        for dep in scan_result.unresolved_dependencies:
            results.append({
                "ruleId": "unresolved-dependency",
                "level": "error",
                "message": {
                    "text": f"Unresolved dependency: {dep.raw.raw_text}"
                },
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": file_uri},
                        "region": {"startLine": dep.raw.line_number},
                    }
                }],
                "properties": {
                    "dependency": dep.raw.raw_text,
                    "kind": dep.raw.kind.value,
                    "note": dep.resolution_note or "",
                    "confidence": dep.confidence.value if dep.confidence else "none",
                    "confidence_source": dep.confidence_source.value if dep.confidence_source else "none",
                },
            })

    return results


def _build_files(result: GraphBuildResult, root: str) -> dict:
    """Build SARIF files dictionary."""
    root_uri = _make_root_uri(root)
    files = {}

    for scan_result in result.scan_results:
        uri = _make_uri(scan_result.file_path, root, root_uri)
        files[uri] = {
            "uri": uri,
            "properties": {
                "language": scan_result.language.value if scan_result.language else "unknown",
                "sizeBytes": scan_result.size_bytes,
            },
        }

    return files


def _build_cycle_results(cycle_report: CycleReport, root: str) -> list[dict]:
    """Build SARIF results for dependency cycles."""
    results = []
    root_uri = _make_root_uri(root)

    for i, cycle in enumerate(cycle_report.cycles[:20]):  # Limit to 20 cycles
        # Use the first node of the cycle as the location
        first_node = cycle.nodes[0] if cycle.nodes else "unknown"
        file_uri = _make_uri(first_node, root, root_uri)

        results.append({
            "ruleId": "dependency-cycle",
            "level": "warning",
            "message": {
                "text": f"Circular dependency detected: {' -> '.join(cycle.nodes)}"
            },
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": file_uri},
                }
            }],
            "properties": {
                "cycleIndex": i + 1,
                "cycleLength": cycle.length,
                "cycleNodes": cycle.nodes,
            },
        })

    return results


def _make_root_uri(root: str) -> str:
    """
    Return an empty string so that _make_uri returns project-relative paths.

    SARIF allows artifactLocation.uri to be a project-relative path (not
    necessarily a full file:// URI), which is what our model already stores
    as scan_result.file_path (a POSIX path relative to project root).
    """
    return ""


def _make_uri(file_path: str, root: str, root_uri: str) -> str:
    """
    Convert a file path to a project-relative POSIX path for SARIF.

    The file_path is expected to already be a project-relative POSIX path
    (as stored in ScanResult.file_path). This function just ensures it
    uses forward slashes.
    """
    # file_path is already project-relative; ensure forward slashes
    return file_path.replace("\\", "/")


def write_sarif_report(sarif: dict, output_path: Path) -> None:
    """
    Write a SARIF dictionary to a JSON file.

    Args:
        sarif: The SARIF dictionary to write
        output_path: Path to the output file
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(sarif, indent=2, ensure_ascii=False, default=_json_serializer)
    output_path.write_text(content, encoding="utf-8")


def _json_serializer(obj):
    """Custom JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# Helper functions for individual dependency/cycle conversion
# ---------------------------------------------------------------------------

def _dependency_level(dep: ResolvedDependency) -> str:
    """Determine SARIF result level based on dependency type."""
    if dep.is_unresolved:
        return "error"
    elif dep.is_external:
        return "warning"
    else:
        return "note"


def _dependency_message(dep: ResolvedDependency) -> str:
    """Generate SARIF message text for a dependency."""
    if dep.is_unresolved:
        msg = f"Unresolved dependency: {dep.raw.raw_text}"
        if dep.resolution_note:
            msg += f" ({dep.resolution_note})"
        return msg
    elif dep.is_stdlib:
        return f"Standard library import: {dep.raw.raw_text}"
    elif dep.is_external:
        return f"External dependency: {dep.raw.raw_text}"
    else:
        return f"Internal dependency: {dep.normalized_path or dep.raw.raw_text}"


def _dependency_rule_id(dep: ResolvedDependency) -> str:
    """Generate rule ID for a dependency."""
    return dep.raw.kind.value


def _dependency_to_result(dep: ResolvedDependency, file_path: str) -> dict:
    """Convert a single ResolvedDependency to a SARIF result."""
    props = {
        "dependency": dep.normalized_path or dep.raw.raw_text,
        "kind": dep.raw.kind.value,
        "confidence": dep.confidence.value if dep.confidence else "none",
        "confidence_source": dep.confidence_source.value if dep.confidence_source else "none",
    }
    return {
        "ruleId": _dependency_rule_id(dep),
        "level": _dependency_level(dep),
        "message": {"text": _dependency_message(dep)},
        "locations": [{
            "artifactLocation": {"uri": file_path},
            "region": {"startLine": dep.raw.line_number},
        }],
        "properties": props,
    }


def _cycle_to_result(cycle: CycleInfo, cycle_index: int) -> dict:
    """Convert a CycleInfo to a SARIF result."""
    return {
        "ruleId": "dependency-cycle",
        "level": "warning",
        "message": {
            "text": f"Circular dependency detected: {' -> '.join(cycle.nodes)} ({cycle.length} nodes)"
        },
        "locations": [{
            "artifactLocation": {"uri": cycle.nodes[0] if cycle.nodes else "unknown"},
        }],
        "properties": {
            "cycleIndex": cycle_index,
            "cycleLength": cycle.length,
            "cycleNodes": cycle.nodes,
        },
    }
