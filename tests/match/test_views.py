"""How close a candidate sounds to its reference (spec/components/matching.md, REQ-MAT-03)."""

from __future__ import annotations

import numpy as np
import pytest

from mph.match import views
from mph.match.matchfile import VIEWS

RATE = 22050
NOTES = (220.0,)


def tone(
    hz: float = 220.0, seconds: float = 1.0, swell: float = 0.0, noise: float = 0.0
) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    wave = sum(np.sin(2 * np.pi * n * hz * t) / n for n in range(1, 8))
    if swell:
        wave = wave * np.minimum(1.0, t / swell)
    if noise:
        wave = wave + noise * np.random.default_rng(1).standard_normal(len(t))
    return np.asarray(wave)


def apart(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """As a search measures: the candidate at the reference's level first."""
    rms = float(np.sqrt(np.mean(a**2)))
    return views.distances(
        views.look(a, RATE, NOTES), views.look(views.scaled_to(b, rms), RATE, NOTES)
    )


@pytest.mark.req("REQ-MAT-03")
def test_a_sound_is_no_distance_from_itself_at_any_level() -> None:
    same = apart(tone(), tone())
    assert same == dict.fromkeys(VIEWS, 0.0)
    louder = apart(tone(), 3 * tone())  # scaled to the reference's level first
    assert all(louder[v] == pytest.approx(0.0, abs=1e-6) for v in VIEWS)


@pytest.mark.req("REQ-MAT-03")
def test_the_pitch_view_hears_a_semitone_as_a_hundred_cents() -> None:
    found = apart(tone(220.0), tone(220.0 * 2 ** (1 / 12)))
    assert found["pitch"] == pytest.approx(100.0, abs=5.0)
    assert found["spectrum"] > 0 and found["mel"] > 0


@pytest.mark.req("REQ-MAT-03")
def test_the_loudness_view_hears_a_swell() -> None:
    found = apart(tone(), tone(swell=0.6))
    assert found["loudness"] > 1.0  # dB, on average
    assert found["pitch"] == pytest.approx(0.0, abs=2.0)  # same note


@pytest.mark.req("REQ-MAT-03")
def test_the_noise_view_hears_breath_against_tone() -> None:
    clean = apart(tone(), tone())["noise"]
    breathy = apart(tone(), tone(noise=0.5))["noise"]
    assert breathy > clean + 0.5  # log10 of flatness: noise flattens the spectrum


@pytest.mark.req("REQ-MAT-03")
def test_sounds_that_are_never_voiced_together_are_an_octave_apart() -> None:
    quiet = np.zeros(RATE)
    reference = views.look(tone(), RATE, NOTES)
    found = views.distances(reference, views.look(quiet, RATE, NOTES))
    assert found["pitch"] == views.MISS_CENTS
    silent = views.look(quiet, RATE, NOTES)
    assert views.distances(silent, silent)["noise"] == 0.0  # never heard: nothing to compare


@pytest.mark.req("REQ-MAT-03")
def test_each_view_counts_by_its_spread_and_weight() -> None:
    raws = [dict.fromkeys(VIEWS, float(n)) for n in (1, 2, 3)]
    raws[0]["pitch"] = 0.0
    scales = views.scales_of(raws)
    assert scales["mel"] == 2.0  # the median
    zero = views.scales_of([dict.fromkeys(VIEWS, 0.0)])
    assert min(zero.values()) > 0  # never 0
    one = dict.fromkeys(VIEWS, 2.0)
    assert views.total(one, scales, {}) == pytest.approx(5.0)  # five views, each 2 / 2
    assert views.total(one, scales, {"pitch": 3.0}) == pytest.approx(7.0)


@pytest.mark.req("REQ-MAT-03")
def test_stereo_is_heard_in_mono_and_silence_stays_silent() -> None:
    stereo = np.stack([tone(), np.zeros(RATE)], axis=1)
    assert np.allclose(views.mono(stereo), tone() / 2)
    assert not views.scaled_to(np.zeros(4), 0.5).any()
