"""A match file: what to fit, to what, and how (spec/components/matching.md, REQ-MAT-01).

A match is a TOML file in a project's ``matches/`` folder. It names the
synth to fit and the score voice it is for, the reference sound and the
span of it to fit, what each candidate plays, the knobs the search turns
(with their ranges) and the ones it holds.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mph.bench import sc
from mph.bench.score import ScoreError, midi

MATCHES = "matches"  # the folder of a project that holds its match files
DEFAULT_BUDGET = 3000
DEFAULT_SEED = 1
DEFAULT_TAIL_S = 3.0
VIEWS = ("spectrum", "mel", "loudness", "pitch", "noise")
SET_BY_RENDER = frozenset({"out", "amp", "gate", "freq", "acc", "seed", "rid", "part"})
KEYS = frozenset(
    {
        "synth",
        "score",
        "voice",
        "why",
        "reference",
        "play",
        "knobs",
        "params",
        "keep",
        "budget",
        "seed",
        "tail_s",
        "weights",
    }
)
REFERENCE_KEYS = frozenset({"hash", "path", "start_s", "end_s", "what", "licence"})
PLAY_KEYS = frozenset({"notes", "at_s", "length_s"})
LOG = "log"


class MatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Knob:
    """A knob the search turns, over its range: in steps of its ratio when ``log``."""

    name: str
    low: float
    high: float
    log: bool = False

    def value(self, unit: float) -> float:
        """The knob's value at a point from 0 to 1 of its range."""
        u = min(max(unit, 0.0), 1.0)
        if self.log:
            return float(self.low * (self.high / self.low) ** u)
        return self.low + (self.high - self.low) * u

    def unit(self, value: float) -> float:
        """Where a value sits in the range, from 0 to 1 (clipped)."""
        if self.log:
            u = math.log(value / self.low) / math.log(self.high / self.low) if value > 0 else 0.0
        else:
            u = (value - self.low) / (self.high - self.low)
        return min(max(u, 0.0), 1.0)


@dataclass(frozen=True, slots=True)
class Reference:
    hash: str = ""
    path: str = ""
    start_s: float = 0.0
    end_s: float = 0.0
    what: str = ""
    licence: str = ""

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True, slots=True)
class Play:
    notes: tuple[float, ...]  # Hz, a synth each
    at_s: float = 0.0
    length_s: float = 1.0


@dataclass(frozen=True, slots=True)
class Match:
    name: str
    project: str
    file: Path
    synth: str
    reference: Reference
    play: Play
    knobs: tuple[Knob, ...]
    params: tuple[tuple[str, float], ...] = ()
    keep: tuple[str, ...] = ()
    score: str = ""
    voice: str = ""
    why: str = ""
    budget: int = DEFAULT_BUDGET
    seed: int = DEFAULT_SEED
    tail_s: float = DEFAULT_TAIL_S
    weights: tuple[tuple[str, float], ...] = ()

    def patch(self, units: Sequence[float]) -> dict[str, float]:
        """The synth's controls at a point of the knobs' space (each knob from 0 to 1)."""
        found = {k.name: k.value(u) for k, u in zip(self.knobs, units, strict=True)}
        return {**dict(self.params), **found}

    def units(self, controls: Mapping[str, float]) -> list[float]:
        """Where a set of controls sits in the knobs' space (missing knobs at the middle)."""
        return [k.unit(controls[k.name]) if k.name in controls else 0.5 for k in self.knobs]

    def weight(self, view: str) -> float:
        return dict(self.weights).get(view, 1.0)

    @property
    def pinned(self) -> tuple[str, ...]:
        """The knobs a pin writes into the score: all it searched but those it keeps."""
        return tuple(k.name for k in self.knobs if k.name not in self.keep)


def synth_controls(name: str) -> dict[str, float]:
    """A SynthDef's controls and their defaults, read from synthdefs.scd."""
    found = re.search(
        r"SynthDef\(\\" + re.escape(name) + r",\s*\{\s*\|([^|]*)\|", sc.synthdef_source()
    )
    if not found:
        raise MatchError(f"synthdefs.scd has no SynthDef called {name}")
    controls = {}
    for part in found.group(1).split(","):
        key, _, value = part.partition("=")
        controls[key.strip()] = float(value) if value.strip() else 0.0
    return controls


def load(path: Path) -> Match:
    """The match in ``path``; MatchError says what is wrong with it."""
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MatchError(f"{path}: {exc}") from exc
    return parse(raw, path)


def parse(raw: Mapping[str, Any], path: Path) -> Match:
    where = path.name
    _known(raw, KEYS, where)
    synth = str(raw.get("synth", ""))
    if not synth:
        raise MatchError(f'{where}: say which synth it fits, like synth = "mph_horn"')
    controls = synth_controls(synth)
    knobs = tuple(_knob(name, value, where) for name, value in _table(raw, "knobs", where).items())
    if not knobs:
        raise MatchError(
            f"{where}: [knobs] needs at least one knob to turn, like brass = [400, 3000]"
        )
    params = tuple((str(k), float(v)) for k, v in _table(raw, "params", where).items())
    _controls_exist([k.name for k in knobs] + [k for k, _ in params], controls, synth, where)
    keep = tuple(str(k) for k in raw.get("keep", []))
    weights = tuple((str(k), float(v)) for k, v in _table(raw, "weights", where).items())
    if unknown := sorted({k for k, _ in weights} - set(VIEWS)):
        raise MatchError(f"{where}: [weights] has {unknown}; the views are {list(VIEWS)}")
    reference = _reference(_table(raw, "reference", where), where)
    play = _play(_table(raw, "play", where), where)
    if play.at_s + play.length_s > reference.length_s:
        raise MatchError(
            f"{where}: [play] holds its notes past the reference's span "
            f"({play.at_s:g} + {play.length_s:g} s, and the span is {reference.length_s:g} s)"
        )
    return Match(
        name=path.stem,
        project=project_of(path),
        file=path,
        synth=synth,
        reference=reference,
        play=play,
        knobs=knobs,
        params=params,
        keep=keep,
        score=str(raw.get("score", "")),
        voice=str(raw.get("voice", "")),
        why=str(raw.get("why", "")),
        budget=int(raw.get("budget", DEFAULT_BUDGET)),
        seed=int(raw.get("seed", DEFAULT_SEED)),
        tail_s=float(raw.get("tail_s", DEFAULT_TAIL_S)),
        weights=weights,
    )


def project_of(path: Path) -> str:
    """The project a match file belongs to: the folder that holds its ``matches/``."""
    folder = path.resolve().parent
    return folder.parent.name if folder.name == MATCHES else folder.name


def _known(raw: Mapping[str, Any], keys: frozenset[str], where: str) -> None:
    if unknown := sorted(set(raw) - keys):
        raise MatchError(f"{where}: unknown keys {unknown} (it takes {sorted(keys)})")


def _table(raw: Mapping[str, Any], key: str, where: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise MatchError(f"{where}: {key} must be a table, like [{key}]")
    return value


def _knob(name: str, value: object, where: str) -> Knob:
    ok = isinstance(value, list) and len(value) in (2, 3)
    if ok and isinstance(value, list):
        low, high, *rest = value
        log = rest == [LOG]
        good = all(isinstance(v, int | float) for v in (low, high)) and (log or not rest)
        if good and float(low) < float(high) and (not log or float(low) > 0):
            return Knob(name, float(low), float(high), log)
    raise MatchError(
        f'{where}: knob {name} needs a range like [400, 3000], or [400, 3000, "log"] '
        f"above 0, low before high: {value!r}"
    )


def _controls_exist(
    names: Sequence[str], controls: Mapping[str, float], synth: str, where: str
) -> None:
    for name in names:
        if name in SET_BY_RENDER:
            raise MatchError(f"{where}: {name} is set by every render, so it can't be a knob")
        if name not in controls:
            raise MatchError(f"{where}: {synth} has no control {name} (it has {sorted(controls)})")


def _reference(raw: Mapping[str, Any], where: str) -> Reference:
    _known(raw, REFERENCE_KEYS, f"{where} [reference]")
    found = Reference(
        hash=str(raw.get("hash", "")),
        path=str(raw.get("path", "")),
        start_s=float(raw.get("start_s", 0.0)),
        end_s=float(raw.get("end_s", 0.0)),
        what=str(raw.get("what", "")),
        licence=str(raw.get("licence", "")),
    )
    if bool(found.hash) == bool(found.path):
        raise MatchError(f"{where}: [reference] names its sound by hash or by path, one of the two")
    if not 0 <= found.start_s < found.end_s:
        raise MatchError(f"{where}: [reference] needs start_s before end_s, in seconds")
    return found


def _play(raw: Mapping[str, Any], where: str) -> Play:
    _known(raw, PLAY_KEYS, f"{where} [play]")
    notes = raw.get("notes", [])
    if not isinstance(notes, list) or not notes:
        raise MatchError(f'{where}: [play] needs notes, like notes = ["E3"] or [162.5]')
    try:
        hz = tuple(float(n) if isinstance(n, int | float) else sc.hz(midi(str(n))) for n in notes)
    except ScoreError as exc:
        raise MatchError(f"{where}: [play] {exc}") from exc
    found = Play(hz, float(raw.get("at_s", 0.0)), float(raw.get("length_s", 1.0)))
    if found.at_s < 0 or found.length_s <= 0:
        raise MatchError(f"{where}: [play] needs at_s of 0 or more and length_s above 0")
    return found
