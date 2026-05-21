"""Reporting sub-package for DepPulse.

Provides JSON / Markdown report generation and SARIF 2.1.0 output.

The original ``deppulse/reporting.py`` module has been migrated to
``deppulse/reporting/legacy.py`` to make room for the ``reporting/`` package.
"""

# Legacy JSON / Markdown exports (migrated from deppulse/reporting.py)
from deppulse.reporting.legacy import (
    _audit_report_to_dict,
    _cycle_report_to_dict,
    _graph_stats_to_dict,
    assemble_audit_report,
    audit_report_to_json,
    audit_report_to_markdown,
    write_json_report,
    write_markdown_report,
)

# SARIF output
from deppulse.reporting.sarif import (
    graph_to_sarif,
    write_sarif_report,
)

__all__ = [
    # Legacy JSON / Markdown exports
    "assemble_audit_report",
    "audit_report_to_json",
    "audit_report_to_markdown",
    "write_json_report",
    "write_markdown_report",
    "_audit_report_to_dict",
    "_cycle_report_to_dict",
    "_graph_stats_to_dict",
    # SARIF exports
    "graph_to_sarif",
    "write_sarif_report",
]
