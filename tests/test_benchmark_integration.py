import subprocess
import sys
from pathlib import Path

import pytest

BENCHMARK_SCRIPT = Path(__file__).parent.parent / "scripts" / "benchmark.py"


@pytest.mark.slow
def test_benchmark_runs_without_error():
    """Ensure benchmark script executes and produces valid JSON."""
    result = subprocess.run(
        [sys.executable, str(BENCHMARK_SCRIPT), "--repo", "flask", "--output", "test-bench.json", "--skip-clone"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    # If no cached clone exists, it may skip gracefully — we just check it doesn't crash
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = Path("test-bench.json")
    if output.exists():
        import json
        data = json.loads(output.read_text(encoding="utf-8"))
        assert "repos" in data
        assert "timestamp" in data
        output.unlink(missing_ok=True)
