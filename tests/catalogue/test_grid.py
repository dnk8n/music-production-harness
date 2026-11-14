"""Tempo grids fitted to long recordings (REQ-CAT-10)."""

from __future__ import annotations

import numpy as np
import pytest

from mph.catalogue import grid

RATE = 22050


def _add(out: np.ndarray, sound: np.ndarray, at: float) -> None:
    start = round(at * RATE)
    end = min(start + sound.size, out.size)
    if 0 <= start < end:
        out[start:end] += sound[: end - start]


def club(bpm: float, first: float, *, wander_s: float = 0.0, bass: bool = False) -> np.ndarray:
    """A minute, the shortest phrase, with a kick on every beat and a hat between.
    ``wander_s`` pushes the beats either side of a fixed grid, as a band drifts;
    ``bass`` adds a held note 85 ms behind every beat, like a laid-back bass line."""
    seconds = 64.0
    out = np.zeros(int(seconds * RATE), dtype=np.float32)
    t = np.arange(int(0.08 * RATE)) / RATE
    kick = (np.sin(2 * np.pi * 60 * t) * np.exp(-t / 0.015)).astype(np.float32)
    rng = np.random.default_rng(1)
    hat = (rng.standard_normal(400) * 0.3 * np.exp(-np.arange(400) / 80)).astype(np.float32)
    held = np.arange(int(0.35 * RATE))
    note = (0.5 * np.sin(2 * np.pi * 55 * held / RATE) * np.minimum(1, held / 200)).astype(
        np.float32
    )
    beat = 60 / bpm
    for n in range(int((seconds - first - 0.5) / beat)):
        at = first + n * beat + wander_s * np.sin(2 * np.pi * n * beat / 20)
        _add(out, kick, at)
        _add(out, hat, at + beat / 2)
        if bass:
            _add(out, note, at + 0.085)
    return out


@pytest.mark.req("REQ-CAT-10")
@pytest.mark.parametrize(("bpm", "first"), [(120.0, 0.0), (127.3, 0.37), (93.0, 1.1)])
def test_a_fixed_grid_gives_its_tempo_and_first_beat(bpm: float, first: float) -> None:
    fitted = grid.fit(club(bpm, first), RATE)
    assert fitted is not None
    assert fitted.bpm == pytest.approx(bpm, abs=0.015)  # not half or double time
    assert fitted.bpm == bpm or not bpm.is_integer()  # whole tempos come out whole
    assert fitted.beat_s == pytest.approx(first % (60 / bpm), abs=0.005)  # the kick, not the hat
    assert (fitted.confidence, fitted.drift_ms <= grid.STEADY_MS) == (0.9, True)


@pytest.mark.req("REQ-CAT-10")
def test_the_kick_marks_the_beat_not_a_laid_back_bass() -> None:
    fitted = grid.fit(club(120.0, 0.0, bass=True), RATE)
    assert fitted is not None
    assert fitted.beat_s == pytest.approx(0.0, abs=0.005)


@pytest.mark.req("REQ-CAT-10")
@pytest.mark.parametrize(("wander_s", "confidence"), [(0.008, 0.6), (0.025, 0.3)])
def test_a_played_track_that_drifts_gets_its_tempo_with_less_confidence(
    wander_s: float, confidence: float
) -> None:
    fitted = grid.fit(club(122.0, 0.2, wander_s=wander_s), RATE)
    assert fitted is not None
    assert fitted.bpm == pytest.approx(122.0, abs=0.05)
    assert fitted.drift_ms > grid.STEADY_MS
    assert fitted.confidence == confidence


def hits_at_random(seconds: float = 64.0) -> np.ndarray:
    rng = np.random.default_rng(3)
    out = np.zeros(int(seconds * RATE), dtype=np.float32)
    t = np.arange(int(0.08 * RATE)) / RATE
    kick = (np.sin(2 * np.pi * 60 * t) * np.exp(-t / 0.015)).astype(np.float32)
    at = 0.1
    while at < seconds - 0.2:
        _add(out, kick * rng.uniform(0.3, 1.0), at)
        at += rng.exponential(0.45)
    return out


@pytest.mark.req("REQ-CAT-10")
def test_no_steady_beat_no_grid() -> None:
    noise = np.random.default_rng(2).standard_normal(64 * RATE).astype(np.float32) * 0.1
    assert grid.fit(noise, RATE) is None
    assert grid.fit(hits_at_random(), RATE) is None  # drums, but played freely
    assert grid.fit(np.zeros(64 * RATE, dtype=np.float32), RATE) is None


def test_whole_tempos_snap_and_the_first_beat_wraps() -> None:
    assert (grid._snap(119.996), grid._snap(127.304), grid._snap(93.5)) == (120.0, 127.3, 93.5)
    assert (grid._first_beat(0.4961, 0.5), grid._first_beat(0.5076, 0.5)) == (0.0, 0.008)
    assert grid._first_beat(0.494, 0.5) == 0.494
