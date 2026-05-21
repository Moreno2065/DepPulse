"""Pytest configuration for DepPulse test suite."""

import sys
from pathlib import Path

# Ensure the project root is on the Python path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
