# DepPulse Audit Report

**Project:** `C:\Users\Mao\PycharmProjects\DepPulse`
**Generated:** 2026-05-21 18:39:54
**Duration:** 0.01s

## Graph Statistics

| Metric | Value |
|--------|-------|
| Total files scanned | 61 |
| Total dependency edges | 88 |
| Python files | 56 |
| C/C++ files | 5 |
| Internal edges | 88 |
| External edges | 0 |
| Total symbols extracted | 737 |

## Top Depended-On Files

These files are depended on by the most other files (highest in-degree):

| Rank | File | Dependents | Language |
|------|------|-----------|----------|
| 1 | `deppulse/models.py` | 25 | python |
| 2 | `deppulse/core/orchestrator.py` | 8 | python |
| 3 | `deppulse/scanners/base.py` | 5 | python |
| 4 | `deppulse/cache.py` | 4 | python |
| 5 | `deppulse/config.py` | 4 | python |
| 6 | `deppulse/git.py` | 2 | python |
| 7 | `deppulse/__init__.py` | 2 | python |
| 8 | `deppulse/core/analyzer.py` | 2 | python |
| 9 | `deppulse/core/callgraph.py` | 2 | python |
| 10 | `deppulse/core/cycles.py` | 2 | python |

## Top Outgoing Dependencies

These files depend on the most other files (highest out-degree):

| Rank | File | Dependencies | Language |
|------|------|-------------|----------|
| 1 | `deppulse/cli.py` | 13 | python |
| 2 | `deppulse/core/orchestrator.py` | 8 | python |
| 3 | `tests/test_orchestrator.py` | 4 | python |
| 4 | `tests/test_sarif.py` | 4 | python |
| 5 | `tests/test_analyzer.py` | 3 | python |
| 6 | `tests/test_cycles.py` | 3 | python |
| 7 | `tests/test_risk.py` | 3 | python |
| 8 | `deppulse/reporting/sarif.py` | 2 | python |
| 9 | `deppulse/reporting/__init__.py` | 2 | python |
| 10 | `deppulse/scanners/cpp_scanner.py` | 2 | python |

## Unresolved Dependencies (39 shown, max 50)

| File | Raw Dependency | Line | Note |
|------|---------------|------|------|
| - | `import networkx as nx` | 10 | no project file found for networkx |
| - | `import networkx as nx` | 652 | no project file found for networkx |
| - | `import networkx as nx` | 9 | no project file found for networkx |
| - | `import networkx as nx` | 8 | no project file found for networkx |
| - | `import networkx as nx` | 12 | no project file found for networkx |
| - | `import javalang` | 5 | no project file found for javalang |
| - | `from rich.console import Console` | 7 | no project file found for rich.console |
| - | `from rich.panel import Panel` | 8 | no project file found for rich.panel |
| - | `from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn` | 9 | no project file found for rich.progress |
| - | `from rich.style import Style` | 10 | no project file found for rich.style |
| - | `from rich.table import Table` | 11 | no project file found for rich.table |
| - | `from rich.tree import Tree` | 12 | no project file found for rich.tree |
| - | `import networkx as nx` | 14 | no project file found for networkx |
| - | `import pytest` | 3 | no project file found for pytest |
| - | `import networkx as nx` | 80 | no project file found for networkx |
| - | `import pytest` | 8 | no project file found for pytest |
| - | `import pytest` | 6 | no project file found for pytest |
| - | `import pytest` | 7 | no project file found for pytest |
| - | `import networkx as nx` | 6 | no project file found for networkx |
| - | `import networkx as nx` | 103 | no project file found for networkx |
| - | `import pytest` | 5 | no project file found for pytest |
| - | `import pytest` | 10 | no project file found for pytest |
| - | `import networkx as nx` | 73 | no project file found for networkx |
| - | `from utils.math_utils import add` | 3 | no project file found for utils.math_utils |
| - | `from . import config` | 4 | relative import unresolved:  (level=1) |
| - | `from . import app` | 3 | relative import unresolved:  (level=1) |
| - | `from utils.helpers import format_name` | 3 | no project file found for utils.helpers |
| - | `from utils import compute_hash` | 4 | no project file found for utils |
| - | `from models.user import User` | 9 | no project file found for models.user |

## External Dependencies (50 shown, max 50)

| Raw Dependency | Kind |
|---------------|------|
| `from __future__ import annotations` | import |
| `import hashlib` | import |
| `import json` | import |
| `import os` | import |
| `from dataclasses import dataclass, field` | import |
| `from pathlib import Path` | import |
| `from typing import Any, Optional` | import |
| `import argparse` | import |
| `import time` | import |
| `from typing import Optional` | import |
| `import networkx as nx` | import |
| `import subprocess` | import |
| `import traceback` | import |
| `from datetime import datetime` | import |
| `from enum import Enum` | import |
| `from pathlib import PurePosixPath, PureWindowsPath` | import |
| `import ast` | import |
| `from collections import deque` | import |
| `import re` | import |

## High-Risk Files (20)

Files with high in-degree or involved in dependency cycles:

- `deppulse/cache.py`
- `deppulse/config.py`
- `deppulse/git.py`
- `deppulse/models.py`
- `deppulse/__init__.py`
- `deppulse/core/analyzer.py`
- `deppulse/core/callgraph.py`
- `deppulse/core/cycles.py`
- `deppulse/core/orchestrator.py`
- `deppulse/core/risk.py`
- `deppulse/reporting/sarif.py`
- `deppulse/reporting/__init__.py`
- `deppulse/scanners/base.py`
- `deppulse/scanners/cpp_scanner.py`
- `deppulse/scanners/java_scanner.py`
- `deppulse/scanners/kotlin_scanner.py`
- `deppulse/scanners/python_scanner.py`
- `tests/fixtures/mixed_project/logger.h`
- `tests/fixtures/python_project/cycle_b.py`
- `tests/fixtures/python_project/services/api.py`

## Legend

- **In-degree**: number of files that depend on this file
- **Out-degree**: number of files this file depends on
- **Blast radius**: % of project files affected by a change
