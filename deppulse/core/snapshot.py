"""Snapshot management for architecture trend monitoring."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import networkx as nx

from deppulse.config import DepPulseConfig
from deppulse.models import (
    CycleInfo,
    CycleReport,
    FileMetrics,
    GraphBuildResult,
    SnapshotDiff,
    SnapshotMeta,
    TrendAlert,
)

# ---------------------------------------------------------------------------
# Snapshot storage path helpers
# ---------------------------------------------------------------------------


def _snapshots_dir(project_root: Path) -> Path:
    return project_root / ".deppulse" / "snapshots"


def _index_path(project_root: Path) -> Path:
    return _snapshots_dir(project_root) / "snapshot-index.json"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


class GitError(Exception):
    """Raised when a git command fails."""


def _run_git(cwd: Path, args: list[str]) -> str:
    env = os.environ.copy()
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_current_commit(project_root: Path) -> tuple[str, str]:
    """
    Return (short_hash, commit_message) for the current HEAD commit.
    Returns ("unknown", "") if not a git repo.
    """
    hash_raw = _run_git(project_root, ["rev-parse", "HEAD"])
    short_hash = hash_raw[:8] if hash_raw else "unknown"

    # Get first line of commit message
    msg_raw = _run_git(project_root, ["log", "-1", "--format=%B"])
    first_line = msg_raw.splitlines()[0].strip() if msg_raw else ""

    return short_hash, first_line


# ---------------------------------------------------------------------------
# Snapshot manager
# ---------------------------------------------------------------------------


class SnapshotManager:
    """
    Save, load, compare, and trend-check dependency graph snapshots.

    Snapshots are stored in ``.deppulse/snapshots/`` as JSON files, indexed
    by ``snapshot-index.json``.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self._snapshots_dir = _snapshots_dir(self.project_root)
        self._index_path = _index_path(self.project_root)

    @property
    def snapshots_dir(self) -> Path:
        return self._snapshots_dir

    @property
    def index_path(self) -> Path:
        return self._index_path

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(
        self,
        graph_result: GraphBuildResult,
        tag: str | None = None,
    ) -> SnapshotMeta:
        """
        Save a snapshot of the current dependency graph.

        Parameters
        ----------
        graph_result : GraphBuildResult
            The result of a full project scan.
        tag : str, optional
            A human-readable tag (e.g. "v0.2.0"). If not provided,
            the commit hash is used.

        Returns
        -------
        SnapshotMeta
            The saved snapshot metadata.
        """
        commit_hash, commit_msg = get_current_commit(self.project_root)
        tag = tag or commit_hash[:8]
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        # Build file metrics from the scan results
        file_metrics: dict[str, FileMetrics] = {}

        # Build a quick lookup: file_path -> ScanResult
        scan_by_path: dict[str, object] = {}
        for sr in graph_result.scan_results:
            scan_by_path[sr.file_path] = sr

        # Build a simple graph from scan results for degree computation
        graph = nx.DiGraph()
        for sr in graph_result.scan_results:
            if sr.file_path not in graph:
                graph.add_node(sr.file_path)
            for dep in sr.internal_dependencies:
                if dep.normalized_path:
                    graph.add_node(dep.normalized_path)
                    graph.add_edge(sr.file_path, dep.normalized_path)

        # Compute betweenness centrality
        centrality_scores: dict[str, float] = {}
        if graph.number_of_nodes() > 1:
            try:
                betweenness = nx.betweenness_centrality(graph, normalized=True)
                centrality_scores = betweenness
            except Exception:
                centrality_scores = {}

        # Cycle report
        cycle_report: CycleReport | None = None
        try:
            from deppulse.core.cycles import find_cycles
            cycle_report = find_cycles(graph)
        except Exception:
            cycle_report = None

        # Build per-file metrics
        for node in graph.nodes():
            file_metrics[node] = FileMetrics(
                path=node,
                in_degree=graph.in_degree(node),
                out_degree=graph.out_degree(node),
                centrality=centrality_scores.get(node, 0.0),
            )

        # Serialize cycles
        cycles_json: list[dict] = []
        if cycle_report:
            for c in cycle_report.cycles:
                cycles_json.append({"nodes": c.nodes, "length": c.length})

        snapshot_data = {
            "version": "1.0",
            "tag": tag,
            "commit_hash": commit_hash,
            "commit_message": commit_msg,
            "saved_at": now.isoformat(),
            "project_root": str(self.project_root),
            "total_files": graph_result.stats.total_files,
            "total_edges": graph_result.stats.total_edges,
            "cycle_count": cycle_report.cycle_count if cycle_report else 0,
            "files_in_cycles": cycle_report.total_files_in_cycles if cycle_report else 0,
            "file_metrics": {
                path: {"path": m.path, "in_degree": m.in_degree, "out_degree": m.out_degree, "centrality": m.centrality}
                for path, m in file_metrics.items()
            },
            "cycles": cycles_json,
        }

        # Save to file
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{date_str}-{tag}.json"
        file_path = self._snapshots_dir / filename
        file_path.write_text(json.dumps(snapshot_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # Update index
        self._add_to_index(tag, filename, now)

        return SnapshotMeta(
            tag=tag,
            commit_hash=commit_hash,
            commit_message=commit_msg,
            saved_at=now,
            project_root=str(self.project_root),
            total_files=graph_result.stats.total_files,
            total_edges=graph_result.stats.total_edges,
            cycle_count=cycle_report.cycle_count if cycle_report else 0,
            files_in_cycles=cycle_report.total_files_in_cycles if cycle_report else 0,
            file_metrics=file_metrics,
        )

    def _add_to_index(self, tag: str, filename: str, saved_at: datetime) -> None:
        """Add or update a snapshot entry in the index file."""
        index = self._read_index()
        # Remove existing entry with same tag
        index["snapshots"] = [s for s in index["snapshots"] if s["tag"] != tag]
        # Add new entry
        index["snapshots"].append({
            "tag": tag,
            "filename": filename,
            "saved_at": saved_at.isoformat(),
        })
        self._index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _read_index(self) -> dict:
        """Read the snapshot index, returning a default if it doesn't exist."""
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"version": "1.0", "snapshots": []}

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self, tag_or_file: str) -> SnapshotMeta:
        """
        Load a snapshot by tag or by filename.

        Parameters
        ----------
        tag_or_file : str
            Either a tag string (looked up in index) or a full filename.

        Returns
        -------
        SnapshotMeta

        Raises
        ------
        FileNotFoundError
            If the snapshot cannot be found.
        """
        index = self._read_index()

        # Try to find by tag
        for entry in index["snapshots"]:
            if entry["tag"] == tag_or_file:
                file_path = self._snapshots_dir / entry["filename"]
                return self._load_from_file(file_path)

        # Try as direct filename
        file_path = self._snapshots_dir / tag_or_file
        if file_path.exists():
            return self._load_from_file(file_path)

        # Search for partial tag match
        for entry in index["snapshots"]:
            if tag_or_file in entry["tag"]:
                file_path = self._snapshots_dir / entry["filename"]
                return self._load_from_file(file_path)

        raise FileNotFoundError(f"Snapshot not found: {tag_or_file}")

    def _load_from_file(self, file_path: Path) -> SnapshotMeta:
        """Reconstruct SnapshotMeta from a JSON snapshot file."""
        data = json.loads(file_path.read_text(encoding="utf-8"))

        file_metrics: dict[str, FileMetrics] = {}
        for path_str, m_dict in data.get("file_metrics", {}).items():
            file_metrics[path_str] = FileMetrics(
                path=m_dict["path"],
                in_degree=m_dict["in_degree"],
                out_degree=m_dict["out_degree"],
                centrality=m_dict["centrality"],
            )

        saved_at = datetime.fromisoformat(data["saved_at"])

        return SnapshotMeta(
            tag=data["tag"],
            commit_hash=data["commit_hash"],
            commit_message=data.get("commit_message", ""),
            saved_at=saved_at,
            project_root=data["project_root"],
            total_files=data["total_files"],
            total_edges=data["total_edges"],
            cycle_count=data["cycle_count"],
            files_in_cycles=data["files_in_cycles"],
            file_metrics=file_metrics,
        )

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_snapshots(self) -> list[SnapshotMeta]:
        """Return all saved snapshots sorted by saved_at descending."""
        snapshots: list[SnapshotMeta] = []
        for entry in self._read_index()["snapshots"]:
            try:
                meta = self.load(entry["tag"])
                snapshots.append(meta)
            except FileNotFoundError:
                continue
        return sorted(snapshots, key=lambda m: m.saved_at, reverse=True)

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(self, older_tag: str, newer_tag: str) -> SnapshotDiff:
        """
        Compare two snapshots and return the delta.

        Parameters
        ----------
        older_tag : str
            Tag or filename of the older snapshot.
        newer_tag : str
            Tag or filename of the newer snapshot.

        Returns
        -------
        SnapshotDiff
        """
        older = self.load(older_tag)
        newer = self.load(newer_tag)

        # New cycles: cycles in newer that are not in older
        older_cycle_keys: set[str] = set()
        for entry in self._read_index()["snapshots"]:
            if entry["tag"] == older_tag:
                older_file_path = self._snapshots_dir / entry["filename"]
                if older_file_path.exists():
                    data = json.loads(older_file_path.read_text(encoding="utf-8"))
                    for c in data.get("cycles", []):
                        key = _cycle_key(c["nodes"])
                        older_cycle_keys.add(key)

        new_cycles_added: list[CycleInfo] = []
        for entry in self._read_index()["snapshots"]:
            if entry["tag"] == newer_tag:
                newer_file_path = self._snapshots_dir / entry["filename"]
                if newer_file_path.exists():
                    data = json.loads(newer_file_path.read_text(encoding="utf-8"))
                    for c in data.get("cycles", []):
                        key = _cycle_key(c["nodes"])
                        if key not in older_cycle_keys:
                            new_cycles_added.append(CycleInfo(nodes=c["nodes"], length=c["length"]))

        # Build alerts
        alerts: list[str] = []

        edges_delta = newer.total_edges - older.total_edges
        files_delta = newer.total_files - older.total_files

        edges_growth_pct = (edges_delta / max(older.total_edges, 1)) * 100
        if edges_growth_pct > 30:
            alerts.append(f"Total edges grew by {edges_growth_pct:.1f}% ({edges_delta:+d} edges)")

        if len(new_cycles_added) > 0:
            alerts.append(f"{len(new_cycles_added)} new dependency cycle(s) introduced")

        return SnapshotDiff(
            older=older,
            newer=newer,
            new_cycles_added=new_cycles_added,
            total_edges_delta=edges_delta,
            files_delta=files_delta,
            alerts=alerts,
        )

    # ------------------------------------------------------------------
    # Trend checking
    # ------------------------------------------------------------------

    def check_trends(self, since_tag: str) -> tuple[SnapshotDiff, list[TrendAlert]]:
        """
        Compare the snapshot at ``since_tag`` with the current graph state.

        Runs a fresh scan of the project, saves a temporary snapshot for the
        current state, and compares against ``since_tag``.

        Returns
        -------
        tuple[SnapshotDiff, list[TrendAlert]]
            The diff and a list of trend alerts.

        Note: this does NOT persist the current-state snapshot to disk.
        """
        from deppulse.core.orchestrator import DependencyOrchestrator

        config = DepPulseConfig.from_path(self.project_root)
        orchestrator = DependencyOrchestrator(config=config, use_cache=False)
        result = orchestrator.scan(self.project_root)

        current_meta = self.save(result, tag=f"current-{datetime.now().strftime('%Y%H%M%S')}")

        # Reload the just-saved snapshot
        diff = self.diff(since_tag, current_meta.tag)

        # Generate structured alerts
        trend_alerts = self._compute_trend_alerts(diff)

        # Remove the temporary snapshot
        self._remove_from_index(current_meta.tag)

        return diff, trend_alerts

    def _compute_trend_alerts(self, diff: SnapshotDiff) -> list[TrendAlert]:
        """Generate structured TrendAlert objects from a snapshot diff."""
        alerts: list[TrendAlert] = []

        # Total edges growth > 30%
        if diff.older.total_edges > 0:
            growth_pct = (diff.total_edges_delta / diff.older.total_edges) * 100
            if growth_pct > 30:
                alerts.append(TrendAlert(
                    metric="total_edges_growth",
                    threshold="30%",
                    older_value=float(diff.older.total_edges),
                    newer_value=float(diff.newer.total_edges),
                    severity="WARNING",
                ))

        # New cycles
        if len(diff.new_cycles_added) > 0:
            alerts.append(TrendAlert(
                metric="new_cycles",
                threshold="0",
                older_value=0.0,
                newer_value=float(len(diff.new_cycles_added)),
                severity="CRITICAL",
            ))

        # Per-file in-degree changes > 100%
        for path, newer_metric in diff.newer.file_metrics.items():
            older_metric = diff.older.file_metrics.get(path)
            if older_metric and older_metric.in_degree > 0:
                change_pct = ((newer_metric.in_degree - older_metric.in_degree) / older_metric.in_degree) * 100
                if change_pct > 100:
                    alerts.append(TrendAlert(
                        metric=f"in_degree_change:{path}",
                        threshold="100%",
                        older_value=float(older_metric.in_degree),
                        newer_value=float(newer_metric.in_degree),
                        severity="WARNING",
                    ))

        return alerts

    def _remove_from_index(self, tag: str) -> None:
        """Remove a snapshot entry from the index (does not delete the file)."""
        index = self._read_index()
        index["snapshots"] = [s for s in index["snapshots"] if s["tag"] != tag]
        self._index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")


def _cycle_key(nodes: list[str]) -> str:
    """Create a canonical string key for a cycle."""
    if not nodes:
        return ""
    # Sort rotations to normalize
    n = len(nodes)
    doubled = nodes + nodes
    min_idx = min(range(n), key=lambda i: "".join(doubled[i : i + n]))
    canonical = tuple(doubled[min_idx : min_idx + n])
    return str(canonical)
