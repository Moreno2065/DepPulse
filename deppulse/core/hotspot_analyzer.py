"""HotspotAnalyzer: compute historical change hotspots from git history.

Used by the risk model to weight historical change patterns when scoring
the risk of a change. Computes:
- bug_fix_rate: fraction of commits that look like bug fixes
- churn_frequency: commit frequency relative to project average
- co_change_risk: probability that this file changes when known hotspots change
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class FileHotspotData:
    """
    Historical hotspot metrics for a single file.
    """

    path: str
    total_commits: int = 0
    bug_fix_commits: int = 0
    churn_count: int = 0          # number of lines changed in last 90 days
    bug_fix_rate: float = 0.0   # bug_fix_commits / total_commits
    churn_frequency: float = 0.0  # commits_last_90d / project_avg_commits (capped at 3.0)
    co_change_count: int = 0      # number of times this file changed alongside hotspot files
    is_hotspot: bool = False      # True if bug_fix_rate > project_avg * 2


@dataclass
class HotspotReport:
    """
    Complete hotspot analysis for a project.
    """

    project_root: str
    computed_at: datetime
    file_data: dict[str, FileHotspotData] = field(default_factory=dict)
    project_avg_bug_fix_rate: float = 0.0
    project_avg_churn: float = 0.0
    co_change_matrix: dict[str, set[str]] = field(default_factory=dict)
    # Cache metadata
    cache_tag: str = ""
    git_ref: str = ""


# ---------------------------------------------------------------------------
# HotspotAnalyzer
# ---------------------------------------------------------------------------


class HotspotAnalyzer:
    """
    Compute historical change hotspots from git history.

    Reads from `.deppulse/hotspot-cache.json` if available and fresh,
    otherwise computes from git log and caches results.

    Parameters
    ----------
    project_root : Path
        Root of the git repository.
    cache_path : Path, optional
        Path to the hotspot cache file. Defaults to `.deppulse/hotspot-cache.json`.
    days : int, optional
        Number of days to look back for churn frequency. Default: 90.
    """

    _CACHE_TTL_DAYS = 7

    def __init__(
        self,
        project_root: Path,
        cache_path: Optional[Path] = None,
        days: int = 90,
    ) -> None:
        self.project_root = project_root.resolve()
        self.cache_path = cache_path or (self.project_root / ".deppulse" / "hotspot-cache.json")
        self.days = days

    def analyze(self, force_refresh: bool = False) -> HotspotReport:
        """
        Analyze git history and compute hotspot metrics.

        Parameters
        ----------
        force_refresh : bool
            If True, ignore the cache and recompute from scratch.

        Returns
        -------
        HotspotReport
            Hotspot data for all tracked files.
        """
        # Try to load from cache
        if not force_refresh:
            cached = self._load_cache()
            if cached:
                # Check if cache is still fresh
                age = datetime.now() - cached.computed_at
                if age.days < self._CACHE_TTL_DAYS:
                    return cached

        # Compute from git log
        report = self._compute_hotspots()
        self._save_cache(report)
        return report

    def get_file_data(self, file_path: str) -> FileHotspotData:
        """
        Get hotspot data for a specific file, computing if needed.
        """
        report = self.analyze()
        return report.file_data.get(file_path, FileHotspotData(path=file_path))

    # ------------------------------------------------------------------------
    # Git log parsing
    # ------------------------------------------------------------------------

    def _run_git_log(
        self,
        since_days: int,
        format_args: str = "--format=%H %ae %ai %s",
    ) -> str:
        """Run git log and return output."""
        cutoff = datetime.now() - timedelta(days=since_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        args = [
            "git", "-C", str(self.project_root),
            "log", f"--since={cutoff_str}",
            f"--format={format_args}",
            "--name-only",
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _compute_hotspots(self) -> HotspotReport:
        """Compute hotspot metrics from git history."""
        cutoff = datetime.now() - timedelta(days=self.days)

        # Parse git log for per-file commit history
        log_output = self._run_git_log(self.days)
        file_commits: dict[str, list[tuple[str, str, str]]] = defaultdict(list)  # path → [(commit_hash, author_email, message)]

        lines = log_output.splitlines()
        current_commit = ""
        current_email = ""
        current_date = ""
        current_msg = ""
        current_files: list[str] = []

        i = 0
        while i < len(lines):
            line = lines[i]
            i += 1

            if not line:
                # Empty line separates commit header from file list
                if current_commit and current_msg:
                    for f in current_files:
                        file_commits[f].append((current_commit, current_email, current_msg))
                current_files = []
                continue

            if " " in line and len(line.split()[0]) == 40:
                # Commit header line: HASH EMAIL DATE SUBJECT
                parts = line.split(" ", 3)
                if len(parts) == 4:
                    current_commit, current_email, current_date, current_msg = parts
            elif not line.startswith(" "):
                # File path line
                current_files.append(line)

        # Also get all-time commit history for bug fix rate calculation
        all_time_log = self._run_git_log(365 * 5)  # 5 years
        file_all_time: dict[str, int] = defaultdict(int)

        all_time_lines = all_time_log.splitlines()
        current_files = []
        for line in all_time_lines:
            if not line:
                for f in current_files:
                    file_all_time[f] += 1
                current_files = []
                continue
            if " " in line and len(line.split()[0]) == 40:
                current_files = []
            elif not line.startswith(" "):
                current_files.append(line)

        # Compute per-file metrics
        file_data: dict[str, FileHotspotData] = {}

        # Determine known hotspot files (bug fix rate > project avg * 2)
        all_bug_fixes = 0
        all_commits = 0
        for path, commits in file_commits.items():
            all_commits += len(commits)
            all_bug_fixes += sum(1 for _, _, msg in commits if self._looks_like_bug_fix(msg))

        project_avg_bug_fix_rate = all_bug_fixes / max(all_commits, 1)
        project_avg_commits = all_commits / max(len(file_commits), 1)

        for path, commits in file_commits.items():
            bug_fixes = sum(1 for _, _, msg in commits if self._looks_like_bug_fix(msg))
            bug_fix_rate = bug_fixes / max(len(commits), 1)
            churn_count = len(commits)
            churn_frequency = min(churn_count / max(project_avg_commits, 1), 3.0)

            hotspot = FileHotspotData(
                path=path,
                total_commits=len(commits),
                bug_fix_commits=bug_fixes,
                churn_count=churn_count,
                bug_fix_rate=bug_fix_rate,
                churn_frequency=churn_frequency,
                co_change_count=0,
                is_hotspot=bug_fix_rate > project_avg_bug_fix_rate * 2,
            )
            file_data[path] = hotspot

        # Build co-change matrix from recent commits
        co_change_matrix: dict[str, set[str]] = defaultdict(set)
        for path, commits in file_commits.items():
            # For each commit, note co-changes
            commit_file_map: dict[str, set[str]] = defaultdict(set)
            # Re-parse to group files by commit
            pass

        # Rebuild co-change matrix properly
        co_change_matrix = self._build_co_change_matrix(file_commits)

        # Compute co_change_count for each file
        known_hotspots = {p for p, d in file_data.items() if d.is_hotspot}
        for path, data in file_data.items():
            co_files = co_change_matrix.get(path, set())
            data.co_change_count = len(co_files & known_hotspots)

        # Get git ref
        ref = ""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.project_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            ref = result.stdout.strip()[:8]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        report = HotspotReport(
            project_root=str(self.project_root),
            computed_at=datetime.now(),
            file_data=file_data,
            project_avg_bug_fix_rate=project_avg_bug_fix_rate,
            project_avg_churn=project_avg_commits,
            co_change_matrix={k: v for k, v in co_change_matrix.items()},
            git_ref=ref,
        )

        return report

    def _build_co_change_matrix(
        self,
        file_commits: dict[str, list[tuple[str, str, str]]],
    ) -> dict[str, set[str]]:
        """
        Build a co-change matrix: for each file, the set of files that
        frequently change alongside it.
        """
        # Group commits by commit hash
        commit_to_files: dict[str, set[str]] = defaultdict(set)
        for path, commits in file_commits.items():
            for commit_hash, _, _ in commits:
                commit_to_files[commit_hash].add(path)

        # Build co-change matrix
        co_change: dict[str, set[str]] = defaultdict(set)
        for commit_hash, files in commit_to_files.items():
            files_list = sorted(files)
            # Only consider commits with 2-20 files (filter out mass reformatting)
            if 2 <= len(files_list) <= 20:
                for f1 in files_list:
                    for f2 in files_list:
                        if f1 != f2:
                            co_change[f1].add(f2)

        return co_change

    @staticmethod
    def _looks_like_bug_fix(message: str) -> bool:
        """Return True if a commit message looks like a bug fix."""
        msg_lower = message.lower()
        bug_keywords = [
            r"\bfix\b", r"\bbug\b", r"\berror\b", r"\bcrash\b",
            r"\bhotfix\b", r"\bhot.?fix\b", r"\bpatch\b",
            r"\bsecurity\b", r"\bcve-\d+\b", r"\bbreach\b",
            r"\bcorrupt\b", r"\bleak\b", r"\bexception\b",
            r"\bbreak(?:ing)?\b", r"\bbackwards?\b",
            r"\bnull\b.*\bpointer\b", r"\bnpe\b",
            r"\brace\b.*\bcondition\b", r"\bdeadlock\b",
            r"\btimeout\b", r"\bhang(?:ing)?\b",
            r"\bincorrect\b", r"\bwrong\b", r"\bbroken\b",
            r"\bfail(?:ed|ing)?\b", r"\bregress(?:ion)?\b",
        ]
        fix_patterns = [
            r"\bfix(?:es|ed)?\b.*", r"\bbug(?:s)?\s+(?:fix|fixed)\b",
            r"(?:hot|emergency)\s*fix", r"\bpatch\b",
            r"revert", r"\bundo\b",
        ]

        for pattern in bug_keywords:
            if re.search(pattern, msg_lower):
                return True

        for pattern in fix_patterns:
            if re.search(pattern, msg_lower):
                return True

        return False

    # ------------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------------

    def _load_cache(self) -> Optional[HotspotReport]:
        """Load hotspot report from cache file."""
        if not self.cache_path.exists():
            return None

        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        try:
            file_data = {}
            for path_str, fdata in data.get("file_data", {}).items():
                file_data[path_str] = FileHotspotData(
                    path=fdata["path"],
                    total_commits=fdata.get("total_commits", 0),
                    bug_fix_commits=fdata.get("bug_fix_commits", 0),
                    churn_count=fdata.get("churn_count", 0),
                    bug_fix_rate=fdata.get("bug_fix_rate", 0.0),
                    churn_frequency=fdata.get("churn_frequency", 0.0),
                    co_change_count=fdata.get("co_change_count", 0),
                    is_hotspot=fdata.get("is_hotspot", False),
                )

            co_change_matrix = {}
            for path_str, co_files in data.get("co_change_matrix", {}).items():
                co_change_matrix[path_str] = set(co_files)

            computed_at_str = data.get("computed_at", "")
            computed_at = datetime.fromisoformat(computed_at_str) if computed_at_str else datetime.now()

            return HotspotReport(
                project_root=data.get("project_root", str(self.project_root)),
                computed_at=computed_at,
                file_data=file_data,
                project_avg_bug_fix_rate=data.get("project_avg_bug_fix_rate", 0.0),
                project_avg_churn=data.get("project_avg_churn", 0.0),
                co_change_matrix=co_change_matrix,
                git_ref=data.get("git_ref", ""),
            )
        except (KeyError, ValueError):
            return None

    def _save_cache(self, report: HotspotReport) -> None:
        """Save hotspot report to cache file."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "project_root": report.project_root,
            "computed_at": report.computed_at.isoformat(),
            "project_avg_bug_fix_rate": report.project_avg_bug_fix_rate,
            "project_avg_churn": report.project_avg_churn,
            "git_ref": report.git_ref,
            "file_data": {
                path: {
                    "path": d.path,
                    "total_commits": d.total_commits,
                    "bug_fix_commits": d.bug_fix_commits,
                    "churn_count": d.churn_count,
                    "bug_fix_rate": d.bug_fix_rate,
                    "churn_frequency": d.churn_frequency,
                    "co_change_count": d.co_change_count,
                    "is_hotspot": d.is_hotspot,
                }
                for path, d in report.file_data.items()
            },
            "co_change_matrix": {
                path: sorted(files) for path, files in report.co_change_matrix.items()
            },
        }

        self.cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
