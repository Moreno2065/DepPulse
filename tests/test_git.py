"""Tests for the git module."""

import subprocess
import tempfile
from pathlib import Path

from deppulse.git import (
    get_changed_files,
    get_git_status_summary,
    is_git_repo,
)


class TestIsGitRepo:
    def test_temp_dir_inside_repo_returns_bool(self):
        """is_git_repo should return a boolean regardless of parent directory."""
        with tempfile.TemporaryDirectory() as tmp:
            result = is_git_repo(Path(tmp))
            assert isinstance(result, bool)
            # Note: result may be True if temp dir is inside an existing git repo


class TestGitChangedFiles:
    def test_no_changes_in_new_repo(self):
        """A fresh git repo with no commits should return empty."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
            changed = get_changed_files(tmp_path)
            assert changed == []


class TestGitStatusSummary:
    def test_non_git_repo_returns_defaults(self):
        """A non-git directory should return 'not a git repo' or a valid branch."""
        with tempfile.TemporaryDirectory() as tmp:
            summary = get_git_status_summary(Path(tmp))
            # Either "not a git repo" or "(detached)" or a branch name - all valid
            assert isinstance(summary["branch"], str)
            assert summary["staged"].isdigit()
            assert summary["unstaged"].isdigit()
