"""Candidates rendered as the track renders them (spec/components/matching.md, REQ-MAT-02)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from mph.bench import audio, osc, sc
from mph.match.candidates import FIRST_GENERATOR, NOTES_EACH, Batch
from mph.match.matchfile import Play
from mph.proc import Result
from tests.bench.fake_sc import fake_scsynth
from tests.fakes import FakeRunner
from tests.match.fake_synth import knob_scsynth

RATE = 22050
TOOLS = sc.Tools("/usr/bin/scsynth", "/usr/bin/sclang")


def batch(
    tmp_path: Path, runner: FakeRunner, workers: int = 1, notes: tuple[float, ...] = (220.0,)
) -> Batch:
    return Batch(
        runner=runner,
        tools=TOOLS,
        synthdefs=tmp_path / "synthdefs",
        synth="mph_horn",
        play=Play(notes, at_s=0.1, length_s=0.5),
        rate=RATE,
        span_s=1.0,
        tail_s=0.5,
        work=tmp_path / "work",
        workers=workers,
    )


def pairs(message: osc.Message) -> dict[str, object]:
    args = message.args[4:]
    return {str(args[i]): args[i + 1] for i in range(0, len(args), 2)}


@pytest.mark.req("REQ-MAT-02")
def test_each_candidate_plays_in_a_slot_of_its_own(tmp_path: Path) -> None:
    found = batch(tmp_path, FakeRunner(), notes=(220.0, 330.0)).bundles(
        [{"brass": 900.0}, {"brass": 1800.0, "swell": 2.0}]
    )
    assert found[0].messages[0] == osc.Message("/d_loadDir", (str(tmp_path / "synthdefs"),))
    starts = [b for b in found if b.messages[0].address == "/s_new"]
    assert [b.time_s for b in starts] == [round(0.1 * RATE) / RATE, round(1.6 * RATE) / RATE]
    first, second = (pairs(m) for m in starts[1].messages)  # the second candidate's chord
    assert (first["freq"], second["freq"]) == (220.0, 330.0)
    assert (first["part"], second["part"], first["acc"], first["seed"]) == (0, 1, 0, 1)
    assert (first["brass"], first["swell"]) == (1800.0, 2.0)
    # Neighbouring slots draw from different random generators.
    assert pairs(starts[0].messages[0])["rid"] == FIRST_GENERATOR
    assert first["rid"] == FIRST_GENERATOR + NOTES_EACH
    offs = [b for b in found if b.messages[0].address == "/n_set"]
    assert [b.time_s for b in offs] == [round(0.6 * RATE) / RATE, round(2.1 * RATE) / RATE]
    assert found[-1].time_s == 3.0  # the run ends after the last slot


@pytest.mark.req("REQ-MAT-02")
def test_candidates_render_in_a_few_runs_at_once_in_order(tmp_path: Path) -> None:
    runner = FakeRunner(responses={"scsynth": knob_scsynth})
    patches = [{"brass": b} for b in (300.0, 600.0, 1200.0, 2400.0, 4800.0)]
    sounds = batch(tmp_path, runner, workers=2).render(patches)
    assert len(runner.calls) == 2  # two runs for five candidates
    assert [s.shape for s in sounds] == [(RATE, 2)] * 5  # each as long as the span
    centroids = [audio.centroid(s, RATE) for s in sounds]
    assert centroids == sorted(centroids)  # brighter as brass rises, in the patches' order
    assert not sounds[0][: round(0.1 * RATE) - 1].any()  # silent until its note starts
    argv = runner.calls[0][0]
    assert argv[argv.index("-N") + 4 :] == (str(RATE), "WAV", "float")  # the reference's rate
    assert list((tmp_path / "work").iterdir()) == []  # each run's files go once read
    assert batch(tmp_path, runner).render([]) == []


@pytest.mark.req("REQ-MAT-02")
def test_a_candidate_plays_as_the_fake_supercollider_plays_a_note(tmp_path: Path) -> None:
    runner = FakeRunner(responses={"scsynth": fake_scsynth})
    (sound,) = batch(tmp_path, runner).render([{"brass": 900.0}])
    pitch = audio.pitch_hz(sound[round(0.2 * RATE) : round(0.5 * RATE)], RATE, 150, 300)
    assert pitch == pytest.approx(220.0, rel=0.01)


@pytest.mark.req("REQ-MAT-02")
@pytest.mark.parametrize(
    "answer",
    [Result(1, stderr="boom"), Result(0, stdout="FAILURE IN SERVER /s_new SynthDef not found")],
)
def test_a_run_that_fails_says_so(tmp_path: Path, answer: Result) -> None:
    def scsynth(argv: Sequence[str]) -> Result:
        knob_scsynth(argv)
        return answer

    runner = FakeRunner(responses={"scsynth": scsynth})
    with pytest.raises(sc.RenderError, match="couldn't render the candidates"):
        batch(tmp_path, runner).render([{"brass": 900.0}])
    assert list((tmp_path / "work").iterdir()) == []


@pytest.mark.req("REQ-MAT-02")
def test_a_run_that_writes_nothing_says_so(tmp_path: Path) -> None:
    runner = FakeRunner(responses={"scsynth": Result(0)})
    with pytest.raises(sc.RenderError):
        batch(tmp_path, runner).each([{"brass": 900.0}], np.shape)
