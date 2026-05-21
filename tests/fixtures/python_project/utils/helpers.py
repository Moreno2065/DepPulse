"""Helper utilities for the python_project fixture."""

import hashlib as _hm


def format_name(name: str) -> str:
    """Capitalize and format a name."""
    return name.strip().title()


def compute_hash(data: str) -> str:
    """Compute SHA-256 hash of input data."""
    return _hm.sha256(data.encode()).hexdigest()


def upper(s: str) -> str:
    return s.upper()
