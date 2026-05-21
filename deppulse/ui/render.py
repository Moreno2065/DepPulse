"""Rich console rendering for DepPulse output."""

from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.style import Style
from rich.table import Table
from rich.tree import Tree

from deppulse.models import (
    CycleReport,
    GraphBuildResult,
    ImpactReport,
    PerFileImpact,
    RiskLevel,
    RiskReport,
)


console = Console()

# CI mode flag for GitHub Actions output
_ci_mode = False


def set_ci_mode(enabled: bool = True) -> None:
    """Enable or disable CI mode for GitHub Actions formatted output."""
    global _ci_mode
    _ci_mode = enabled


def is_ci_mode() -> bool:
    """Check if CI mode is enabled."""
    return _ci_mode


# ---------------------------------------------------------------------------
# CI mode helpers
# ---------------------------------------------------------------------------

def ci_group(name: str) -> None:
    """Print GitHub Actions group start."""
    print(f"##[group]{name}")

def ci_endgroup() -> None:
    """Print GitHub Actions group end."""
    print("##[endgroup]")

def ci_info(key: str, value: str) -> None:
    """Print a key-value pair in CI format."""
    print(f"  {key}: {value}")

def ci_section(title: str) -> None:
    """Print a section title in CI format."""
    if _ci_mode:
        print(f"\n##[group]{title}")
    else:
        print(f"\n{title}")

def ci_error(message: str) -> None:
    """Print an error message (always visible in CI)."""
    if _ci_mode:
        print(f"##[error]{message}")
    else:
        console.print(f"[red]Error: {message}[/red]")

def ci_warning(message: str) -> None:
    """Print a warning message (always visible in CI)."""
    if _ci_mode:
        print(f"##[warning]{message}")
    else:
        console.print(f"[yellow]Warning: {message}[/yellow]")


# ---------------------------------------------------------------------------
# Colors / styles
# ---------------------------------------------------------------------------

def _risk_color(level: RiskLevel) -> str:
    return {
        RiskLevel.LOW: "green",
        RiskLevel.MEDIUM: "yellow",
        RiskLevel.HIGH: "red",
    }.get(level, "white")


def _risk_style(level: RiskLevel) -> Style:
    return Style(color=_risk_color(level), bold=True)


# ---------------------------------------------------------------------------
# Scan result rendering
# ---------------------------------------------------------------------------

def render_scan_result(
    result: GraphBuildResult,
    show_tree: bool = False,
    show_table: bool = False,
    show_unresolved: bool = False,
) -> None:
    """Render the output of a scan command."""
    stats = result.stats

    if _ci_mode:
        # CI mode: simple text output without rich panels
        ci_group("DepPulse Scan Summary")
        print(f"  Files scanned:   {stats.total_files}")
        print(f"  Total edges:    {stats.total_edges}")
        print(f"  Python files:  {stats.python_files}")
        print(f"  C/C++ files:   {stats.cpp_files}")
        print(f"  Internal deps: {stats.internal_edges}")
        print(f"  External deps: {stats.external_edges}")
        print(f"  Symbols:       {stats.total_symbols}")
        ci_endgroup()

        # Warnings in CI mode
        if result.warnings:
            ci_warning(f"{len(result.warnings)} warning(s) found")
            for w in result.warnings[:5]:
                print(f"  ! {w}")

        # Cycle warning
        if result.stats.files_with_cycles > 0:
            ci_warning(f"{result.stats.files_with_cycles} file(s) in dependency cycles")
    else:
        # Summary panel
        summary_text = (
            f"[cyan]Files scanned:[/cyan]   [white]{stats.total_files}[/white]\n"
            f"[cyan]Total edges:[/cyan]    [white]{stats.total_edges}[/white]\n"
            f"[cyan]Python files:[/cyan]  [white]{stats.python_files}[/white]\n"
            f"[cyan]C/C++ files:[/cyan]    [white]{stats.cpp_files}[/white]\n"
            f"[cyan]Internal deps:[/cyan]  [white]{stats.internal_edges}[/white]\n"
            f"[cyan]External deps:[/cyan]  [white]{stats.external_edges}[/white]\n"
            f"[cyan]Symbols:[/cyan]        [white]{stats.total_symbols}[/white]"
        )
        console.print(Panel(summary_text, title="[bold]DepPulse Scan Summary[/bold]", border_style="cyan"))

        # Warnings
        if result.warnings:
            console.print()
            warn_panel = "\n".join(f"[yellow]! {w}[/yellow]" for w in result.warnings[:5])
            console.print(Panel(warn_panel, title="[yellow]Warnings[/yellow]", border_style="yellow"))

        # Dependency table
        if show_table:
            console.print()
            _render_dependency_table(result)

        # Top depended-on table
        if result.stats.total_edges > 0:
            console.print()
            _render_top_files_table(result)

        # Cycle warning
        if result.stats.files_with_cycles > 0:
            console.print()
            console.print(
                f"[yellow]![/yellow] {result.stats.files_with_cycles} file(s) participate in dependency cycles. "
                f"Run [cyan]deppulse cycles[/cyan] for details.",
                style="yellow",
            )


def _render_dependency_table(result: GraphBuildResult) -> None:
    """Render a table of top dependency edges."""
    table = Table(title="Dependency Edges (sample)", header_style="bold cyan")
    table.add_column("Source", style="white")
    table.add_column("Kind", style="cyan")
    table.add_column("Dependency", style="white")

    edges_shown = 0
    for scan_result in result.scan_results:
        for resolved in scan_result.internal_dependencies[:5]:
            if edges_shown >= 50:
                break
            table.add_row(
                scan_result.file_path,
                resolved.raw.kind.value,
                resolved.normalized_path or "(unresolved)",
            )
            edges_shown += 1
        if edges_shown >= 50:
            break

    console.print(table)


def _render_top_files_table(result: GraphBuildResult) -> None:
    """Render tables of top depended-on and top outgoing files."""
    from deppulse.core.orchestrator import DependencyOrchestrator

    # Build quick in/out degree tables
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}

    for scan_result in result.scan_results:
        out_deg[scan_result.file_path] = len(scan_result.internal_dependencies)

    # in-degree: count how many other files depend on this one
    for scan_result in result.scan_results:
        for resolved in scan_result.internal_dependencies:
            if resolved.normalized_path:
                in_deg[resolved.normalized_path] = in_deg.get(resolved.normalized_path, 0) + 1

    # Top depended on (highest in-degree)
    top_in = sorted(in_deg.items(), key=lambda x: -x[1])[:10]
    if top_in:
        table = Table(title="Top Depended-On Files (highest in-degree)", header_style="bold cyan")
        table.add_column("Rank", justify="right", style="dim")
        table.add_column("File", style="white")
        table.add_column("Dependents", justify="right", style="cyan")
        for i, (path, count) in enumerate(top_in, 1):
            table.add_row(str(i), path, str(count))
        console.print(table)


# ---------------------------------------------------------------------------
# Impact report rendering
# ---------------------------------------------------------------------------

def render_impact_report(
    impact: ImpactReport,
    show_chains: bool = False,
    max_chains: int = 50,
) -> None:
    """Render the output of a trace command."""
    # Per-file impacts
    for pfi in impact.per_file_impact:
        _render_per_file_impact(pfi, show_chains=show_chains, max_chains=max_chains)

    # Combined summary
    console.print()
    combined_text = (
        f"[cyan]Combined affected files:[/cyan]  [white]{impact.combined_affected_count}[/white]\n"
        f"[cyan]Total files in project:[/cyan]  [white]{impact.total_files_in_project}[/white]\n"
        f"[cyan]Blast radius:[/cyan]           [white]{impact.blast_radius_percent:.1f}%[/white]"
    )
    console.print(Panel(combined_text, title="[bold]Combined Impact[/bold]", border_style="cyan"))


def _render_per_file_impact(pfi: PerFileImpact, show_chains: bool, max_chains: int) -> None:
    """Render impact for a single mutated file."""
    risk_color = _risk_color(pfi.blast_radius_percent_to_risk())
    border = risk_color

    header = f"[bold]Impact: {pfi.mutated_file}[/bold]"
    body_lines = [
        f"[red]Mutated file:[/red] {pfi.mutated_file}",
        f"[yellow]Affected files:[/yellow] {pfi.total_affected}",
        f"[yellow]Blast radius:[/yellow] {pfi.blast_radius_percent:.1f}%",
        f"[cyan]Directly affected:[/cyan] {len(pfi.directly_affected)}",
    ]
    if pfi.affected_files:
        body_lines.append(
            f"[dim]Affected list:[/dim] {', '.join(pfi.affected_files[:5])}"
            + (f" ... (+{len(pfi.affected_files) - 5} more)" if len(pfi.affected_files) > 5 else "")
        )

    console.print(Panel("\n".join(body_lines), title=header, border_style=border))

    # Impact chains
    if show_chains and pfi.impact_chains:
        console.print()
        chain_table = Table(
            title="Impact Chains",
            header_style="bold cyan",
            show_lines=True,
        )
        chain_table.add_column("Chain", style="white")
        for i, chain in enumerate(pfi.impact_chains[:max_chains], 1):
            chain_str = " → ".join(chain.chain)
            chain_table.add_row(f"[dim]{i}.[/dim] {chain_str}")
        console.print(chain_table)


def render_impact_report_simple(
    impact: ImpactReport,
    show_chains: bool = False,
    max_chains: int = 50,
) -> None:
    """Simplified single-panel impact rendering for diff command."""
    color = _risk_color(impact.risk_level)

    body_parts = [
        f"[cyan]Mutated files:[/cyan]     {len(impact.mutated_files)}",
        f"[yellow]Total affected:[/yellow]  {impact.combined_affected_count}",
        f"[cyan]Blast radius:[/cyan]      {impact.blast_radius_percent:.1f}%",
        f"[cyan]Risk level:[/cyan]        [{color}]{impact.risk_level.value}[/{color}]",
    ]

    if impact.mutated_files:
        body_parts.append(f"\n[red]Changed files:[/red]")
        for f in impact.mutated_files:
            body_parts.append(f"  - {f}")

    if impact.all_affected_files:
        body_parts.append(f"\n[yellow]All affected files ({len(impact.all_affected_files)}):[/yellow]")
        for f in impact.all_affected_files[:15]:
            body_parts.append(f"  - {f}")
        if len(impact.all_affected_files) > 15:
            body_parts.append(f"  ... (+{len(impact.all_affected_files) - 15} more)")

    console.print(Panel(
        "\n".join(body_parts),
        title="[bold]Change Impact Analysis[/bold]",
        border_style=color,
    ))


# ---------------------------------------------------------------------------
# Cycle report rendering
# ---------------------------------------------------------------------------

def render_cycle_report(report: CycleReport) -> None:
    """Render the output of a cycles command."""
    severity_color = {
        "NONE": "green",
        "MINOR": "green",
        "MODERATE": "yellow",
        "SEVERE": "red",
    }.get(report.severity.value, "white")

    if _ci_mode:
        ci_group("Cycle Detection")
        print(f"  Total cycles: {report.cycle_count}")
        print(f"  Files in cycles: {report.total_files_in_cycles}")
        print(f"  Severity: {report.severity.value}")
        if report.top_cycle_participants:
            print("  Top cycle participants:")
            for i, (path, count) in enumerate(report.top_cycle_participants[:5], 1):
                print(f"    {i}. {path}: {count} cycles")
        if report.cycles:
            print("  Cycle chains:")
            for i, cycle in enumerate(report.cycles[:10], 1):
                chain_str = " -> ".join(cycle.nodes)
                print(f"    {i}. {chain_str}")
        ci_endgroup()
    else:
        summary = (
            f"[cyan]Total cycles:[/cyan]         [white]{report.cycle_count}[/white]\n"
            f"[cyan]Files in cycles:[/cyan]       [white]{report.total_files_in_cycles}[/white]\n"
            f"[cyan]Severity:[/cyan]             [{severity_color}]{report.severity.value}[/{severity_color}]"
        )
        console.print(Panel(summary, title="[bold]Cycle Detection[/bold]", border_style=severity_color))

        if report.top_cycle_participants:
            console.print()
            table = Table(title="Top Cycle Participants", header_style="bold yellow")
            table.add_column("Rank", justify="right", style="dim")
            table.add_column("File", style="white")
            table.add_column("Cycles", justify="right", style="yellow")
            for i, (path, count) in enumerate(report.top_cycle_participants, 1):
                table.add_row(str(i), path, str(count))
            console.print(table)

        if report.cycles:
            console.print()
            table = Table(title="Cycle Chains", header_style="bold yellow", show_lines=True)
            table.add_column("#", justify="right", style="dim")
            table.add_column("Cycle", style="white")
            for i, cycle in enumerate(report.cycles[:20], 1):
                chain_str = " → ".join(cycle.nodes)
                table.add_row(str(i), chain_str)
            if len(report.cycles) > 20:
                console.print(f"[dim]... and {len(report.cycles) - 20} more cycles[/dim]")
            console.print(table)


# ---------------------------------------------------------------------------
# Risk report rendering
# ---------------------------------------------------------------------------

def render_risk_report(report: RiskReport) -> None:
    """Render the output of a risk assessment."""
    color = _risk_color(report.level)

    summary = (
        f"[cyan]Risk Score:[/cyan]  [cyan bold]{report.score:.1f}/100[/cyan bold]\n"
        f"[cyan]Level:[/cyan]       [cyan bold]{report.level.value}[/cyan bold]"
    )
    console.print(Panel(summary, title="[bold]Risk Assessment[/bold]", border_style=color))

    console.print()
    table = Table(
        title="Risk Score Breakdown",
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("Component", style="white")
    table.add_column("Weight", justify="right", style="cyan")
    table.add_column("Contribution", justify="right", style="yellow")
    table.add_column("Explanation", style="dim")

    for comp in report.components:
        contrib_pct = f"{comp.contribution * 100:.1f} ({comp.weight:.0%})"
        table.add_row(
            comp.name,
            f"{comp.weight:.0%}",
            f"{comp.contribution * 100:.1f}",
            comp.explanation[:80],
        )
    console.print(table)

    if report.involved_files:
        console.print()
        console.print("[cyan]Files assessed:[/cyan]")
        for f in report.involved_files[:10]:
            console.print(f"  - {f}")
        if len(report.involved_files) > 10:
            console.print(f"  ... (+{len(report.involved_files) - 10} more)")


# ---------------------------------------------------------------------------
# Diff report rendering
# ---------------------------------------------------------------------------

def render_diff_report(
    changed_files: list[str],
    unsupported: list[str],
    impact: Optional[ImpactReport],
    git_summary: dict[str, str],
) -> None:
    """Render the output of a diff command."""
    # Git status summary
    status_text = (
        f"[cyan]Branch:[/cyan]     [white]{git_summary.get('branch', 'N/A')}[/white]\n"
        f"[cyan]Staged:[/cyan]     [white]{git_summary.get('staged', '0')}[/white]\n"
        f"[cyan]Modified:[/cyan]   [white]{git_summary.get('unstaged', '0')}[/white]\n"
        f"[cyan]Untracked:[/cyan]  [white]{git_summary.get('untracked', '0')}[/white]"
    )
    console.print(Panel(status_text, title="[bold]Git Status[/bold]", border_style="cyan"))

    # Changed files
    console.print()
    if changed_files:
        console.print(f"[yellow]Changed files in graph ({len(changed_files)}):[/yellow]")
        for f in changed_files:
            console.print(f"  - {f}")
    else:
        console.print("[green]No changed files found in dependency graph.[/green]")

    if unsupported:
        console.print()
        console.print(f"[dim]Unsupported changed files ({len(unsupported)}):[/dim]")
        for f in unsupported[:10]:
            console.print(f"  - {f}", style="dim")
        if len(unsupported) > 10:
            console.print(f"  ... (+{len(unsupported) - 10} more)", style="dim")

    # Impact
    if impact:
        console.print()
        render_impact_report_simple(impact)


# ---------------------------------------------------------------------------
# Doctor command rendering
# ---------------------------------------------------------------------------

def render_doctor(
    project_exists: bool,
    is_git_repo: bool,
    supported_files: int,
    config_loaded: str,
    cache_status: str,
    scanner_names: list[str],
) -> None:
    """Render the output of the doctor command."""
    if _ci_mode:
        ci_group("DepPulse Environment Check")
        print(f"  Project exists: {'Yes' if project_exists else 'No'}")
        print(f"  Git repository: {'Yes' if is_git_repo else 'No'}")
        print(f"  Supported files: {supported_files}")
        print(f"  Configuration: {config_loaded}")
        print(f"  Cache: {cache_status}")
        print(f"  Scanners: {', '.join(scanner_names) if scanner_names else 'None'}")
        ci_endgroup()
    else:
        rows = [
            ("Project exists", "[green]Yes[/green]" if project_exists else "[red]No[/red]"),
            ("Git repository", "[green]Yes[/green]" if is_git_repo else "[dim]No[/dim]"),
            ("Supported files found", str(supported_files)),
            ("Configuration", config_loaded),
            ("Cache", cache_status),
            ("Scanners available", ", ".join(scanner_names) if scanner_names else "[yellow]None[/yellow]"),
        ]

        table = Table(title="[bold]DepPulse Environment Check[/bold]", header_style="bold cyan")
        table.add_column("Check", style="white")
        table.add_column("Status", style="white")
        for label, value in rows:
            table.add_row(label, value)
        console.print(table)


# ---------------------------------------------------------------------------
# JSON output helper
# ---------------------------------------------------------------------------

def render_json_output(data: dict) -> None:
    """Render a dictionary as JSON to the console."""
    import json
    console.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Helper: PerFileImpact needs this method
# ---------------------------------------------------------------------------

def _patch_per_file_impact() -> None:
    """Monkey-patch PerFileImpact to add the helper method."""
    from deppulse.models import PerFileImpact, RiskLevel

    def blast_radius_to_risk(self) -> RiskLevel:
        if self.blast_radius_percent >= 50:
            return RiskLevel.HIGH
        elif self.blast_radius_percent >= 20:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    PerFileImpact.blast_radius_percent_to_risk = blast_radius_to_risk


_patch_per_file_impact()
