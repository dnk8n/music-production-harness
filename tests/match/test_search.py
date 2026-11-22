"""The search, and known answers (spec/components/matching.md, REQ-MAT-04, REQ-MAT-05)."""

from __future__ import annotations

import numpy as np
import pytest

from mph.match import search, views
from mph.match.matchfile import VIEWS

RATE = 16000
NOTES = (220.0,)


def bowl(target: np.ndarray, calls: list[np.ndarray] | None = None) -> search.Measure:
    """A distance that is the same in every view: how far a point is from ``target``."""

    def measure(points: np.ndarray) -> list[dict[str, float]]:
        if calls is not None:
            calls.append(points.copy())
        return [dict.fromkeys(VIEWS, float(np.sum((p - target) ** 2))) for p in points]

    return measure


def toy(point: np.ndarray) -> np.ndarray:
    """A small synth: knob 0 is how fast its harmonics fade, knob 1 how long it swells."""
    t = np.arange(RATE // 2) / RATE
    fade, swell = 0.5 + 4 * point[0], 0.02 + 0.4 * point[1]
    wave = sum(np.exp(-n / fade) * np.sin(2 * np.pi * n * 220 * t) for n in range(1, 10))
    return np.asarray(wave * np.minimum(1.0, t / swell))


def heard(hidden: np.ndarray) -> search.Measure:
    reference = toy(hidden)
    rms = float(np.sqrt(np.mean(reference**2)))
    ref = views.look(reference, RATE, NOTES)

    def measure(points: np.ndarray) -> list[dict[str, float]]:
        return [
            views.distances(ref, views.look(views.scaled_to(toy(p), rms), RATE, NOTES))
            for p in points
        ]

    return measure


@pytest.mark.req("REQ-MAT-04")
@pytest.mark.req("REQ-MAT-05")
def test_a_search_finds_a_hidden_patch_by_ear() -> None:
    hidden = search.hidden_patch(2, seed=5)
    found = search.search(2, heard(hidden), budget=480, seed=5, weights={})
    check = search.Check(hidden, found)
    assert check.passed, (found.total, found.first_median)
    assert check.barely_heard() == []
    assert max(check.errors) < search.OFF_SHARE
    assert found.renders <= 480 and found.generations == len(found.progress)


@pytest.mark.req("REQ-MAT-04")
def test_the_baseline_is_one_of_the_first_generation() -> None:
    calls: list[np.ndarray] = []
    target = np.array([0.3, 0.7, 0.5])
    found = search.search(
        3, bowl(target, calls), budget=200, seed=2, weights={}, start=[0.2, 0.2, 0.2]
    )
    first = calls[0]
    assert first.shape == (search.POPSIZE * 3, 3)
    assert list(first[0]) == [0.2, 0.2, 0.2]
    assert found.start == pytest.approx(
        5 * float(np.sum((first[0] - target) ** 2)) / found.scales["mel"]
    )
    spread_out = np.sort(first[1:, 0])
    assert spread_out[0] < 0.2 and spread_out[-1] > 0.8  # a Latin hypercube covers the range
    without = search.search(3, bowl(target), budget=200, seed=2, weights={})
    assert without.start is None


@pytest.mark.req("REQ-MAT-04")
def test_a_search_stops_at_its_budget_and_when_it_stalls() -> None:
    target = np.array([0.5, 0.5])
    budget = search.search(2, bowl(target), budget=60, seed=1, weights={})
    assert budget.renders <= 60
    told: list[int] = []
    search.search(
        2,
        bowl(target),
        budget=60,
        seed=1,
        weights={},
        each_generation=lambda f: told.append(f.generations),
    )
    assert told == list(range(1, len(told) + 1))
    # Far from 0 everywhere, so it never gets better by 1% over 15 generations.
    near = bowl(target)

    def floored(points: np.ndarray) -> list[dict[str, float]]:
        return [{v: 100 + d for v, d in raw.items()} for raw in near(points)]

    stalled = search.search(2, floored, budget=100_000, seed=1, weights={})
    gens = search.STALL_GENERATIONS
    assert len(stalled.progress) == gens + 1  # stopped as soon as it could tell
    assert stalled.progress[-1] > stalled.progress[0] * (1 - search.STALL_GAIN)
    # A sound no knob changes: every candidate alike, so scipy stops at once.
    flat = search.search(
        2, lambda p: [dict.fromkeys(VIEWS, 1.0)] * len(p), budget=10_000, seed=1, weights={}
    )
    assert flat.generations <= 2


@pytest.mark.req("REQ-MAT-04")
def test_a_seeded_search_repeats() -> None:
    target = np.array([0.1, 0.9, 0.4])
    one = search.search(3, bowl(target), budget=300, seed=7, weights={})
    two = search.search(3, bowl(target), budget=300, seed=7, weights={})
    assert (list(one.best), one.total, one.progress) == (list(two.best), two.total, two.progress)


@pytest.mark.req("REQ-MAT-06")
def test_candidates_to_hear_are_the_closest_that_are_apart() -> None:
    found = search.Found(np.zeros(2), 0.0, {}, {})
    found.tried = [
        (np.array([0.50, 0.50]), 1.0),
        (np.array([0.52, 0.50]), 1.1),  # too near the closest
        (np.array([0.80, 0.50]), 1.2),
        (np.array([0.10, 0.10]), 3.0),
        (np.array([0.11, 0.10]), 2.9),  # too near the one before it, which is closer
    ]
    picked = found.apart(3)
    assert [score for _, score in picked] == [1.0, 1.2, 2.9]
    assert search.spread(np.array([0.0, 0.0]), np.array([0.3, 0.4])) == pytest.approx(
        0.3536, abs=1e-4
    )


@pytest.mark.req("REQ-MAT-05")
def test_a_check_says_which_knobs_the_sound_barely_shows() -> None:
    found = search.Found(np.array([0.50, 0.32, 0.9]), 0.05, {}, {}, first_median=1.0)
    check = search.Check(np.array([0.51, 0.20, 0.9]), found)
    assert check.errors == pytest.approx([0.01, 0.12, 0.0])
    assert check.barely_heard() == [1]
    assert check.passed  # 0.05 is under a tenth of the first median
    assert not search.Check(
        check.hidden, search.Found(found.best, 0.2, {}, {}, first_median=1.0)
    ).passed
    hidden = search.hidden_patch(5, seed=3)
    assert ((hidden >= 0.1) & (hidden <= 0.9)).all()
    assert list(hidden) == list(search.hidden_patch(5, seed=3))
