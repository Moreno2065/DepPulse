"""Git integration for DepPulse using subprocess."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path
from typing import Optional


class GitError(Exception):
    """Raised when a git command fails."""


def is_git_repo(path: Path) -> bool:
    """
    Return True if `path` is inside a git repository.

    Uses `git rev-parse --is-inside-work-tree` for accurate detection,
    which distinguishes between a bare repo, worktree, or nested subdirectories.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(path.resolve()),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _run_git(
    cwd: Path,
    args: list[str],
    check: bool = True,
) -> str:
    """Run a git subprocess and return its stdout."""
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
        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            raise GitError(stderr or f"git {' '.join(args)} failed with code {result.returncode}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git command timed out: {' '.join(args)}") from e
    except FileNotFoundError as e:
        raise GitError("git is not installed or not in PATH") from e


def get_changed_files(
    project_root: Path,
    staged: bool = False,
    ref: Optional[str] = None,
) -> list[str]:
    """
    Return a list of changed file paths (project-relative) using git.

    Parameters
    ----------
    project_root : Path
        Root of the git repository.
    staged : bool
        If True, return staged changes (--cached). Mutually exclusive with `ref`.
    ref : str, optional
        If provided, compare against this git ref (e.g. "main", "HEAD~5").
        Uses `<ref>...HEAD` comparison.

    Returns
    -------
    list[str]
        Project-relative paths of changed files.
    """
    if staged and ref:
        raise ValueError("Cannot specify both --staged and --ref")

    try:
        if staged:
            output = _run_git(project_root, ["diff", "--cached", "--name-only"])
        elif ref:
            output = _run_git(project_root, ["diff", "--name-only", f"{ref}...HEAD"])
        else:
            # Default: working tree vs HEAD
            output = _run_git(project_root, ["diff", "--name-only"])

        if not output:
            return []

        # Convert to project-relative paths
        changed = []
        abs_root = project_root.resolve()
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            full_path = (abs_root / line).resolve()
            try:
                rel = full_path.relative_to(abs_root)
                changed.append(str(rel).replace(os.sep, "/"))
            except ValueError:
                # File outside project root
                changed.append(line.replace(os.sep, "/"))

        return changed

    except GitError:
        return []


def get_git_status_summary(project_root: Path) -> dict[str, str]:
    """
    Return a brief summary of the git repository status.
    """
    try:
        branch = _run_git(project_root, ["branch", "--show-current"], check=False)
        status = _run_git(project_root, ["status", "--porcelain"], check=False)

        lines = [l for l in status.splitlines() if l.strip()]
        staged = sum(1 for l in lines if l[0] in "MADRC")
        unstaged = sum(1 for l in lines if l[1] in "MDRC")
        untracked = sum(1 for l in lines if l.startswith("??"))

        return {
            "branch": branch or "(detached)",
            "staged": str(staged),
            "unstaged": str(unstaged),
            "untracked": str(untracked),
        }
    except GitError:
        return {
            "branch": "not a git repo",
            "staged": "0",
            "unstaged": "0",
            "untracked": "0",
        }
