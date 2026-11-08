"""Benchmark scores as data (spec/components/benchmarks.md, REQ-BEN-04).

A score is a TOML file: tempo, swing, bars, and one table per voice. Each
voice has a 16-step grid (the same notation as the spec), or a ``pulse``
that repeats every N sixteenths across bar lines (polyrhythm), or a list of
break ``slices``. Velocity rules (alternation, ramps, seeded humanisation)
are optional. ``events`` turns a score into hits at exact sample positions,
so rendering and measuring both start from the same list.
"""

from __future__ import annotations

import hashlib
import json
import random
import tomllib
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

STEPS = 16
VELOCITY = {"X": 1.0, "x": 0.8, "o": 0.8}
STRAIGHT = 0.5
DEFAULT_TAIL_S = 1.0


class ScoreError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Ramp:
    """Over the last ``steps`` steps of every ``every_bars``-th bar, velocity
    rises linearly from ``start`` to ``end``."""

    every_bars: int
    steps: int
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Sound:
    """What a voice asks for: a catalogue search, or a SuperCollider voice."""

    role: str = ""
    words: tuple[str, ...] = ()
    synth: str = ""  # forces a synth voice, e.g. "mph_kick"


@dataclass(frozen=True, slots=True)
class Voice:
    name: str
    sound: Sound
    grid: str = ""  # 16 symbols
    pulse: int = 0  # hit every N sixteenths, continuing across bars
    slices: tuple[int, ...] = ()  # 1-based slice numbers, one per 8th note
    velocities: tuple[float, ...] = ()  # cycled per hit, overriding the grid symbol
    ramp: Ramp | None = None
    humanise: float = 0.0  # +/- fraction of velocity, seeded
    chokes: tuple[str, ...] = ()  # voices this one cuts short
    seconds: float = 0.0  # length limit for one-shots (0 = whole sound)


@dataclass(frozen=True, slots=True)
class Score:
    id: str
    bpm: float
    bars: int
    voices: tuple[Voice, ...]
    swing: float = STRAIGHT
    seed: int = 1
    tail_s: float = DEFAULT_TAIL_S
    title: str = ""
    hear: str = ""  # the spec's "Hear:" text, for the listening page

    @property
    def step_s(self) -> float:
        return 60.0 / self.bpm / 4

    @property
    def length_s(self) -> float:
        return self.bars * STEPS * self.step_s + self.tail_s

    def voice(self, name: str) -> Voice:
        for voice in self.voices:
            if voice.name == name:
                return voice
        raise ScoreError(f"{self.id} has no voice {name!r}")

    def digest(self, extra: dict[str, Any] | None = None) -> str:
        """Hash of the score and anything else that shapes the render."""
        payload = json.dumps({"score": asdict(self), "extra": extra or {}}, sort_keys=True)
        return hashlib.blake2b(payload.encode(), digest_size=8).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    voice: str
    step: int  # global sixteenth index from the start
    time_s: float  # after swing
    velocity: float
    slice: int = 0  # 1-based break slice, 0 for ordinary hits

    def sample(self, rate: int) -> int:
        return round(self.time_s * rate)


# --- loading -------------------------------------------------------------------------


def cells(grid: str) -> list[str]:
    symbols = grid.split()
    if len(symbols) != STEPS or any(s not in (*VELOCITY, ".") for s in symbols):
        raise ScoreError(f"a grid needs {STEPS} symbols from X x o . : {grid!r}")
    return symbols


def _sound(raw: dict[str, Any]) -> Sound:
    words = raw.get("words", ())
    return Sound(str(raw.get("role", "")), tuple(str(w) for w in words), str(raw.get("synth", "")))


def _voice(name: str, raw: dict[str, Any]) -> Voice:
    ramp = raw.get("ramp")
    voice = Voice(
        name=name,
        sound=_sound(raw.get("sound", {})),
        grid=str(raw.get("grid", "")),
        pulse=int(raw.get("pulse", 0)),
        slices=tuple(int(n) for n in raw.get("slices", ())),
        velocities=tuple(float(v) for v in raw.get("velocities", ())),
        ramp=Ramp(**ramp) if ramp else None,
        humanise=float(raw.get("humanise", 0.0)),
        chokes=tuple(str(v) for v in raw.get("chokes", ())),
        seconds=float(raw.get("seconds", 0.0)),
    )
    if sum(bool(x) for x in (voice.grid, voice.pulse, voice.slices)) != 1:
        raise ScoreError(f"voice {name}: give exactly one of grid, pulse or slices")
    if voice.grid:
        cells(voice.grid)
    return voice


def parse(raw: dict[str, Any]) -> Score:
    try:
        voices = tuple(_voice(name, table) for name, table in raw["voices"].items())
        score = Score(
            id=str(raw["id"]),
            bpm=float(raw["bpm"]),
            bars=int(raw["bars"]),
            voices=voices,
            swing=float(raw.get("swing", STRAIGHT)),
            seed=int(raw.get("seed", 1)),
            tail_s=float(raw.get("tail_s", DEFAULT_TAIL_S)),
            title=str(raw.get("title", "")),
            hear=str(raw.get("hear", "")),
        )
    except (KeyError, TypeError) as exc:
        raise ScoreError(f"score is missing or mistypes {exc}") from exc
    if not STRAIGHT <= score.swing < 1:
        raise ScoreError(f"{score.id}: swing must be from 0.5 (straight) to under 1")
    names = {v.name for v in voices}
    for voice in voices:
        if unknown := set(voice.chokes) - names:
            raise ScoreError(f"{score.id}: {voice.name} chokes unknown voices {sorted(unknown)}")
    return score


def load_text(text: str) -> Score:
    try:
        return parse(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise ScoreError(str(exc)) from exc


def available() -> list[str]:
    folder = resources.files("mph.bench").joinpath("scores")
    return sorted(
        p.name.removesuffix(".toml") for p in folder.iterdir() if p.name.endswith(".toml")
    )


def load(benchmark_id: str) -> Score:
    if benchmark_id not in available():
        raise ScoreError(f"no score for {benchmark_id} (have: {', '.join(available())})")
    path = resources.files("mph.bench").joinpath("scores", f"{benchmark_id}.toml")
    return load_text(path.read_text("utf-8"))


# --- events ---------------------------------------------------------------------------


def swung_time(step: int, step_s: float, swing: float) -> float:
    """Every odd sixteenth lands ``swing`` of the way through its 8th note."""
    pair, offbeat = divmod(step, 2)
    return pair * 2 * step_s + (2 * step_s * swing if offbeat else 0.0)


def _hits(voice: Voice, bars: int) -> Iterator[tuple[int, float, int]]:
    """(global step, velocity from the symbol, slice) before velocity rules."""
    total = bars * STEPS
    if voice.pulse:
        yield from ((step, VELOCITY["x"], 0) for step in range(0, total, voice.pulse))
    elif voice.slices:
        repeats = total // 2 // len(voice.slices) + 1  # one slice per 8th note
        for n, number in enumerate(voice.slices * repeats):
            if n * 2 < total:
                yield n * 2, VELOCITY["x"], number
    else:
        symbols = cells(voice.grid)
        for step in range(total):
            symbol = symbols[step % STEPS]
            if symbol != ".":
                yield step, VELOCITY[symbol], 0


def _velocity(voice: Voice, n: int, step: int, base: float, rng: random.Random) -> float:
    velocity = voice.velocities[n % len(voice.velocities)] if voice.velocities else base
    ramp = voice.ramp
    if ramp:
        bar, position = divmod(step, STEPS)
        first = STEPS - ramp.steps
        if (bar + 1) % ramp.every_bars == 0 and position >= first:
            fraction = (position - first) / max(ramp.steps - 1, 1)
            velocity = ramp.start + (ramp.end - ramp.start) * fraction
    if voice.humanise:
        velocity *= 1 + rng.uniform(-voice.humanise, voice.humanise)
    return round(min(max(velocity, 0.0), 1.0), 4)


def events(score: Score) -> list[Event]:
    found: list[Event] = []
    for index, voice in enumerate(score.voices):
        rng = random.Random(score.seed * 1000 + index)  # noqa: S311 - musical, not crypto
        for n, (step, base, number) in enumerate(_hits(voice, score.bars)):
            time_s = swung_time(step, score.step_s, score.swing)
            velocity = _velocity(voice, n, step, base, rng)
            found.append(Event(voice.name, step, time_s, velocity, number))
    found.sort(key=lambda e: (e.time_s, e.voice))
    return found


def bar_one(score: Score, voice: Voice) -> str:
    """Bar 1 of a voice in grid notation, for checking against the spec."""
    hits = {e.step: e for e in events(score) if e.voice == voice.name and e.step < STEPS}
    symbols = []
    for step in range(STEPS):
        hit = hits.get(step)
        if hit is None:
            symbols.append(".")
        elif voice.grid:
            symbols.append(cells(voice.grid)[step])
        else:
            symbols.append("x")
    return " ".join(symbols)
