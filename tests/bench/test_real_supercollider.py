"""Renders with the real SuperCollider (marker ``tools``; skipped without it).

Run with ``uv run pytest -m tools`` on a machine with SuperCollider installed.
CI skips these: unit tests never touch real apps (REQ-ENG-03, REQ-ENG-06).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mph.bench import render, sc
from mph.proc import SubprocessRunner
from tests.bench.test_render import cfg_for

pytestmark = pytest.mark.tools


@pytest.fixture
def real() -> SubprocessRunner:
    runner = SubprocessRunner(timeout_s=300)
    if sc.find_tools(runner) is None:
        pytest.skip("SuperCollider isn't installed")
    return runner


@pytest.mark.req("REQ-BEN-01")
@pytest.mark.req("REQ-AUD-05")
@pytest.mark.parametrize("benchmark_id", ["BM-DRUM-02", "BM-DRUM-06", "BM-DRUM-08"])
def test_synth_kit_benchmarks_pass_with_real_supercollider(
    real: SubprocessRunner, tmp_path: Path, benchmark_id: str
) -> None:
    outcome = render.run(
        benchmark_id, cfg_for(tmp_path, rate=96000), real, None, engine="supercollider"
    )
    failures = [m for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []
