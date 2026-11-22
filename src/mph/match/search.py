"""The search for the closest patch (spec/components/matching.md, REQ-MAT-04, REQ-MAT-05).

Differential evolution (scipy, ADR-0014) over the knobs, each scaled to
0..1. ``measure`` renders a whole generation at once and returns each
candidate's distance in every view; the first generation, spread out by a
Latin hypercube, sets the scale of each view (REQ-MAT-03).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import differential_evolution
from scipy.stats import qmc

from mph.match.views import scales_of, total

POPSIZE = 6  # members for each knob
MIN_MEMBERS = 5  # what differential evolution needs to breed
STALL_GENERATIONS = 15
STALL_GAIN = 0.01  # less than 1% better over STALL_GENERATIONS: it has stalled
APART = 0.1  # candidates offered for listening are at least this far apart
CHECK_SHARE = 0.1  # a known answer is found when closer than this share of the first median
OFF_SHARE = 0.05  # a knob further off than this share of its range is barely heard

Measure = Callable[[np.ndarray], list[dict[str, float]]]  # points (n, knobs) -> raw distances
EachGeneration = Callable[["Found"], None]  # told of the search after each generation


@dataclass
class Found:
    """What a search found, and how it got there."""

    best: np.ndarray
    total: float
    views: dict[str, float]
    scales: dict[str, float]
    renders: int = 0
    generations: int = 0
    first_median: float = 0.0
    progress: list[float] = field(default_factory=list)  # the best total after each generation
    tried: list[tuple[np.ndarray, float]] = field(default_factory=list)
    start: float | None = None  # the baseline's total, when it was in the first generation

    def apart(self, count: int = 3, distance: float = APART) -> list[tuple[np.ndarray, float]]:
        """The closest candidates that are at least ``distance`` apart from each other."""
        picked: list[tuple[np.ndarray, float]] = []
        for point, score in sorted(self.tried, key=lambda pair: pair[1]):
            if all(spread(point, other) >= distance for other, _ in picked):
                picked.append((point, score))
            if len(picked) == count:
                break
        return picked


def spread(a: np.ndarray, b: np.ndarray) -> float:
    """How far apart two points are: the root mean square of their knobs' differences."""
    return float(np.sqrt(np.mean(np.square(a - b))))


class _Stalled(Exception):
    pass


class _Search:
    def __init__(
        self,
        measure: Measure,
        weights: Mapping[str, float],
        members: int,
        budget: int,
        each_generation: EachGeneration | None = None,
    ):
        self.measure = measure
        self.weights = weights
        self.members = members
        self.budget = budget
        self.each_generation = each_generation
        self.scales: dict[str, float] = {}
        self.found: Found | None = None

    def cost(self, population: np.ndarray) -> np.ndarray:
        """A generation's totals; ``population`` is (knobs, members), as scipy gives it."""
        points = np.ascontiguousarray(population.T)
        raws = self.measure(points)
        first = not self.scales
        if first:
            self.scales = scales_of(raws)
        totals = [total(raw, self.scales, self.weights) for raw in raws]
        self._keep(points, raws, totals, first=first)
        if self.each_generation is not None and self.found is not None:
            self.each_generation(self.found)
        return np.array(totals)

    def _keep(
        self, points: np.ndarray, raws: list[dict[str, float]], totals: list[float], *, first: bool
    ) -> None:
        best = int(np.argmin(totals))
        found = self.found
        if found is None:
            found = self.found = Found(points[best], totals[best], raws[best], self.scales)
            found.first_median = float(np.median(totals))
        elif totals[best] < found.total:
            found.best, found.total, found.views = points[best], totals[best], raws[best]
        found.renders += len(points)
        found.generations += 1
        found.progress.append(found.total)
        found.tried += [(p, t) for p, t in zip(points, totals, strict=True)]
        if first:
            found.start = totals[0]

    def stop(self, *_: object) -> bool:
        """True once the budget is spent or the best has stopped getting better."""
        found = self.found
        if found is None:
            return False
        if found.renders + self.members > self.budget:
            return True
        if len(found.progress) <= STALL_GENERATIONS:
            return False
        before = found.progress[-1 - STALL_GENERATIONS]
        return found.total > before * (1 - STALL_GAIN)


def search(
    knobs: int,
    measure: Measure,
    *,
    budget: int,
    seed: int,
    weights: Mapping[str, float],
    start: Sequence[float] | None = None,
    each_generation: EachGeneration | None = None,
) -> Found:
    """Search the knobs' space (each knob from 0 to 1) for the closest patch.
    ``start`` joins the first generation; ``each_generation`` is told of each."""
    members = max(POPSIZE * knobs, MIN_MEMBERS)
    first = qmc.LatinHypercube(d=knobs, rng=np.random.default_rng(seed)).random(members)
    if start is not None:
        first[0] = np.clip(np.asarray(start, dtype=np.float64), 0.0, 1.0)
    state = _Search(measure, weights, members, budget, each_generation)
    generations = max(1, budget // members - 1)
    differential_evolution(
        state.cost,
        [(0.0, 1.0)] * knobs,
        init=first,
        maxiter=generations,
        polish=False,
        vectorized=True,
        updating="deferred",
        tol=0.0,
        atol=0.0,
        callback=_callback(state),
        **_seeded(seed),
    )
    assert state.found is not None  # the first generation always runs  # noqa: S101
    if start is None:
        state.found.start = None
    return state.found


def _callback(state: _Search) -> Callable[..., bool]:
    def callback(intermediate_result: object) -> bool:  # scipy passes the generation's result
        return state.stop(intermediate_result)

    return callback


def _seeded(seed: int) -> dict[str, object]:
    """scipy took ``seed`` before 1.15 and ``rng`` since."""
    names = inspect.signature(differential_evolution).parameters
    return {"rng": np.random.default_rng(seed)} if "rng" in names else {"seed": seed}


@dataclass(frozen=True, slots=True)
class Check:
    """A known-answer check (REQ-MAT-05): a hidden patch, and how close the search came."""

    hidden: np.ndarray
    found: Found

    @property
    def errors(self) -> list[float]:
        """How far off each knob is, as a share of its range."""
        return [abs(float(a) - float(b)) for a, b in zip(self.found.best, self.hidden, strict=True)]

    @property
    def passed(self) -> bool:
        return self.found.total <= CHECK_SHARE * self.found.first_median

    def barely_heard(self) -> list[int]:
        """The knobs more than OFF_SHARE of their range off: the sound barely shows them."""
        return [k for k, error in enumerate(self.errors) if error > OFF_SHARE]


def hidden_patch(knobs: int, seed: int) -> np.ndarray:
    """A point to hide, kept away from the edges, where a search finds things last."""
    rng = np.random.default_rng(seed + 1_000_003)
    return 0.1 + 0.8 * rng.random(knobs)
