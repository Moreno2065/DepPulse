import time
from pathlib import Path

import pytest

from deppulse.config import DepPulseConfig
from deppulse.core.orchestrator import DependencyOrchestrator

FIXTURE_ROOT = Path(__file__).parent.parent / "tests" / "fixtures"


@pytest.mark.slow
def test_scan_fixture_under_ceiling():
    """Scan a fixture project and assert it completes within a time ceiling."""
    fixture = FIXTURE_ROOT / "sample_project"
    if not fixture.exists():
        pytest.skip("No large fixture found")

    config = DepPulseConfig.from_path(fixture)
    orchestrator = DependencyOrchestrator(config=config, use_cache=False)

    start = time.monotonic()
    result = orchestrator.scan(fixture)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"Scan took {elapsed:.2f}s, ceiling is 5.0s"
    assert result.stats.total_files > 0
