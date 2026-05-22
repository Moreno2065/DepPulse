"""Base scanner interface implementing the Strategy Pattern."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from deppulse.models import ResolvedDependency, ScanResult


class BaseScanner(ABC):
    """
    Abstract base class for language-specific scanners.

    Each scanner is responsible for:
    - Determining whether it can scan a given file (`can_scan`)
    - Extracting dependencies from a file (`scan`)
    - Resolving a raw dependency reference to a project-relative path (`resolve_dependency`)

    Subclasses must be thread-safe if used concurrently.
    """

    name: str = "base"

    @abstractmethod
    def can_scan(self, path: Path) -> bool:
        """
        Return True if this scanner can handle the given file.

        Override to check file suffix, magic bytes, or other heuristics.
        """

    @abstractmethod
    def scan(
        self,
        file_path: Path,
        project_root: Path,
        file_index: dict[str, Path] = {},
    ) -> "ScanResult":
        """
        Scan a single file and return a structured ScanResult.

        Parameters
        ----------
        file_path : Path
            Absolute path to the file on disk.
        project_root : Path
            Absolute path to the project root directory.
        file_index : dict[str, Path], optional
            A mapping from project-relative POSIX path string to absolute Path,
            built by the orchestrator. Scanners can use this to resolve
            internal dependencies efficiently without walking the tree again.

        Returns
        -------
        ScanResult
            Structured result containing raw and resolved dependencies,
            extracted symbols, and any warnings.
        """

    def resolve_dependency(
        self,
        raw_text: str,
        source_file: Path,
        project_root: Path,
        file_index: dict[str, Path] = {},
    ) -> "ResolvedDependency":
        """
        Resolve a raw dependency text to a project-relative path or classify
        it as external/stdlib.

        The base implementation returns an unresolved dependency.
        Subclasses should override this with language-specific resolution logic.
        """
        from deppulse.models import DependencyKind, RawDependency, ResolvedDependency

        raw = RawDependency(raw_text=raw_text, kind=DependencyKind.UNKNOWN, line_number=0)
        return ResolvedDependency(
            raw=raw,
            normalized_path=None,
            is_external=True,
            is_stdlib=False,
            is_unresolved=True,
            resolution_note="base scanner cannot resolve",
        )
