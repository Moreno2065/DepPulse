"""Lightweight file-based cache for scan results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CacheData = dict[str, Any]


@dataclass
class CacheEntry:
    """A single cache entry mapping a project-relative path to scan metadata."""

    path: str
    mtime_ns: int
    size_bytes: int
    content_hash: str
    result: dict[str, Any]


@dataclass
class ScanCache:
    """
    A JSON file cache that avoids reparsing unchanged files.

    Cache file: .deppulse/cache.json
    Each entry is keyed by project-relative POSIX path.
    """

    cache_file: Path
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    _dirty: bool = field(default=False, init=False)

    @classmethod
    def load(cls, cache_dir: Path) -> ScanCache:
        """Load an existing cache, or return an empty cache if none exists."""
        cache_file = cache_dir / "cache.json"
        instance = cls(cache_file=cache_file)

        if not cache_file.exists():
            return instance

        try:
            data: dict[str, Any] = json.loads(cache_file.read_text(encoding="utf-8"))
            for path, entry_data in data.get("entries", {}).items():
                try:
                    instance.entries[path] = CacheEntry(
                        path=entry_data["path"],
                        mtime_ns=entry_data["mtime_ns"],
                        size_bytes=entry_data["size_bytes"],
                        content_hash=entry_data["content_hash"],
                        result=entry_data["result"],
                    )
                except (KeyError, TypeError):
                    # Corrupted entry — skip
                    continue
        except (json.JSONDecodeError, OSError):
            # Corrupted cache — ignore and rebuild
            pass

        return instance

    def save(self) -> None:
        """Write the cache to disk. Does nothing if no entries changed."""
        if not self._dirty:
            return

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "entries": {
                path: {
                    "path": e.path,
                    "mtime_ns": e.mtime_ns,
                    "size_bytes": e.size_bytes,
                    "content_hash": e.content_hash,
                    "result": e.result,
                }
                for path, e in self.entries.items()
            }
        }

        try:
            self.cache_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self._dirty = False
        except OSError:
            # Disk write error — cache will be rebuilt next run
            pass

    def get(
        self,
        rel_path: str,
        abs_path: Path,
    ) -> dict[str, Any] | None:
        """
        Return a cached scan result if the file has not changed.

        Returns None if the file has been modified, does not exist in cache,
        or if any metadata mismatch is detected.
        """
        entry = self.entries.get(rel_path)
        if entry is None:
            return None

        try:
            stat = abs_path.stat()
            current_mtime = stat.st_mtime_ns
            current_size = stat.st_size
        except OSError:
            return None

        if current_mtime != entry.mtime_ns or current_size != entry.size_bytes:
            return None

        # Content hash as a secondary check: if mtime/size match but the file
        # was modified beyond the first 64 KB, hash the full file to be sure.
        if entry.content_hash:
            try:
                actual_hash = hashlib.sha1(abs_path.read_bytes()).hexdigest()
                if actual_hash != entry.content_hash:
                    return None
            except OSError:
                return None

        return entry.result

    def set(
        self,
        rel_path: str,
        abs_path: Path,
        result: dict[str, Any],
    ) -> None:
        """Store a scan result in the cache."""
        try:
            stat = abs_path.stat()
            mtime_ns = stat.st_mtime_ns
            size_bytes = stat.st_size
        except OSError:
            return

        # Compute full-content hash (not truncated) to avoid false positives
        # when a large file is modified beyond the first 64 KB.
        try:
            content_hash = hashlib.sha1(abs_path.read_bytes()).hexdigest()
        except OSError:
            content_hash = ""

        self.entries[rel_path] = CacheEntry(
            path=rel_path,
            mtime_ns=mtime_ns,
            size_bytes=size_bytes,
            content_hash=content_hash,
            result=result,
        )
        self._dirty = True

    def clear(self) -> None:
        """Remove all cache entries and delete the cache file."""
        self.entries.clear()
        self._dirty = True
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
        except OSError:
            pass

    def invalidate(self, rel_path: str) -> None:
        """Remove a single entry from the cache."""
        if rel_path in self.entries:
            del self.entries[rel_path]
            self._dirty = True

    def get_stats(self) -> dict[str, int]:
        """Return basic cache statistics."""
        return {
            "entries": len(self.entries),
            "size_kb": self.cache_file.stat().st_size // 1024 if self.cache_file.exists() else 0,
        }
