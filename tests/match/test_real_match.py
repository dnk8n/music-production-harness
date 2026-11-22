"""The matcher with the real SuperCollider (marker ``tools``; skipped without it).

A known-answer check of mph_horn: a patch hidden in its knobs' ranges is
rendered as the reference, and the search must hear its way back to it
(spec/components/matching.md, REQ-MAT-02, REQ-MAT-05). Run with
``uv run pytest -m tools`` on a machine with SuperCollider installed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mph.bench import audio, sc
from mph.platform_info import Host
from mph.proc import SubprocessRunner
from tests.match.test_commands import cfg_for, mph

pytestmark = pytest.mark.tools

RATE = 24000
MATCH = """
synth = "mph_horn"
why = "a known-answer check with the real horn"
budget = 360
seed = 2
tail_s = 0.5

[reference]
path = "rate.wav"
start_s = 0.0
end_s = 2.0
what = "a steady level, there only for its rate"
licence = "made by the test"

[play]
notes = ["E3"]
at_s = 0.1
length_s = 1.6

[knobs]
brass = [300, 3000, "log"]
swell = [0.2, 1.5]
"""


@pytest.fixture
def real() -> SubprocessRunner:
    runner = SubprocessRunner(timeout_s=600)
    if sc.find_tools(runner) is None:
        pytest.skip("SuperCollider isn't installed")
    return runner


@pytest.mark.req("REQ-MAT-02")
@pytest.mark.req("REQ-MAT-05")
def test_a_known_answer_check_passes_with_the_real_horn(
    real: SubprocessRunner, tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "proj" / "matches"
    folder.mkdir(parents=True)
    audio.write_float(folder / "rate.wav", np.full((2 * RATE, 2), 0.1, dtype=np.float32), RATE)
    (folder / "horn.toml").write_text(MATCH)
    code = mph(cfg_for(tmp_path), real, mac_host, "match", "check", str(folder / "horn.toml"))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Known-answer check of horn: passed." in out
