"""The mix inside a score (spec/components/mix.md, REQ-MIX-01 to REQ-MIX-03).

A voice's ``mix`` table is its channel strip: filters, level, pan and what it
sends to each of the score's ``sends``. A send is a shared effect, a delay or
a reverb, that returns through a strip of its own, and ``[master]`` is the
strip everything sums into. An ``automate`` table moves strip controls over
the bars as lanes of points. This module holds the mix as data and the
arithmetic of its lanes; ``scmix.py`` turns it into SuperCollider synths.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

FREQUENCIES = ("highpass", "lowpass")  # move evenly in octaves; the rest move in dB
LEVELS = ("level",)
MOVABLE = (*FREQUENCIES, *LEVELS)  # on any strip; a voice's or return's sends move too
STRIP_KEYS = frozenset({"highpass", "lowpass", "resonance", "level", "pan", "send"})
SILENT_DB = -90.0  # at or below this, a level or send is silence (REQ-MIX-03)
LOWEST_HZ, HIGHEST_HZ = 10.0, 30000.0
DELAY, REVERB = "delay", "reverb"
EFFECTS: dict[str, dict[str, float]] = {  # each kind's settings and their defaults
    DELAY: {"beats": 0.0, "feedback": 0.4, "lowpass": 3000.0, "pingpong": 0.0},
    REVERB: {"decay_s": 0.0, "damping": 6000.0, "predelay_ms": 20.0},
}
REQUIRED = {DELAY: "beats", REVERB: "decay_s"}
MAX_SENDS = 4  # sends one strip can feed (the strip synth's bus inputs)
MAX_PREDELAY_MS = 500.0
MAX_DELAY_S = 4.0


class MixError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Lane:
    """One control's automation: points of (bar, value), bars from 1 (REQ-MIX-03)."""

    target: str
    points: tuple[tuple[float, float], ...]

    @property
    def in_octaves(self) -> bool:
        return self.target in FREQUENCIES

    def at(self, bar: float) -> float:
        """The value at a place in the track; after a jump, the value it jumped to."""
        bars = [b for b, _ in self.points]
        after = bisect.bisect_right(bars, bar)
        if after == 0:
            return self.points[0][1]
        if after == len(self.points):
            return self.points[-1][1]
        (start, low), (end, high) = self.points[after - 1], self.points[after]
        return between(low, high, (bar - start) / (end - start), octaves=self.in_octaves)


def between(low: float, high: float, share: float, *, octaves: bool) -> float:
    """``share`` of the way from ``low`` to ``high``: in octaves for a frequency,
    in dB (from no lower than silence) for a level."""
    if octaves:
        return float(low * (high / low) ** share)
    low, high = max(low, SILENT_DB), max(high, SILENT_DB)
    return low + (high - low) * share


@dataclass(frozen=True, slots=True)
class Strip:
    """A channel strip (REQ-MIX-01). A filter set to 0 Hz is off unless a lane moves it."""

    highpass: float = 0.0
    lowpass: float = 0.0
    resonance: float = 0.0
    level: float = 0.0  # dB
    pan: float = 0.0  # -1 left, 1 right: a balance
    sends: tuple[tuple[str, float], ...] = ()  # send name, dB
    lanes: tuple[Lane, ...] = ()

    def lane(self, target: str) -> Lane | None:
        return next((lane for lane in self.lanes if lane.target == target), None)

    def uses(self, control: str) -> bool:
        """True when a filter is on: set, or moved by a lane."""
        return bool(getattr(self, control)) or self.lane(control) is not None

    def start(self, control: str) -> float:
        """A control's value before any lane moves it: its lane's first value, or
        the strip's setting (for a send, what the strip sends)."""
        lane = self.lane(control)
        if lane is not None:
            return lane.points[0][1]
        if control in MOVABLE:
            return float(getattr(self, control))
        return dict(self.sends)[control]


@dataclass(frozen=True, slots=True)
class Send:
    """A shared effect and the strip it returns through (REQ-MIX-02)."""

    name: str
    kind: str
    settings: tuple[tuple[str, float], ...]
    strip: Strip

    def setting(self, name: str) -> float:
        return dict(self.settings)[name]


# --- loading ---------------------------------------------------------------------


def _number(raw: object, where: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise MixError(f"{where} needs a number: {raw!r}")
    return float(raw)


def _frequency(raw: object, where: str) -> float:
    value = _number(raw, where)
    if not LOWEST_HZ <= value <= HIGHEST_HZ:
        raise MixError(f"{where} needs a frequency from {LOWEST_HZ:g} to {HIGHEST_HZ:g} Hz")
    return value


def _decibels(raw: object, where: str) -> float:
    value = _number(raw, where)
    if math.isnan(value) or value == math.inf:
        raise MixError(f"{where} needs a level in dB, or -inf for silence")
    return value


def _sends(raw: object, where: str) -> tuple[tuple[str, float], ...]:
    if not isinstance(raw, dict):
        raise MixError(f"{where}: send names sends and dB, like {{ echo = -8 }}")
    if len(raw) > MAX_SENDS:
        raise MixError(f"{where} feeds {len(raw)} sends; a strip feeds at most {MAX_SENDS}")
    return tuple((str(k), _decibels(v, f"{where}, send {k}")) for k, v in raw.items())


def _point(raw: object, target: str, where: str) -> tuple[float, float]:
    pair = raw if isinstance(raw, list) else []
    if len(pair) != 2:  # noqa: PLR2004 - a bar and a value
        raise MixError(f"{where}: a point is [bar, value], like [37, 1200]: {raw!r}")
    bar = _number(pair[0], f"{where}, a point's bar")
    if not math.isfinite(bar) or bar < 1:
        raise MixError(f"{where}: bars count from 1: {raw!r}")
    if target in FREQUENCIES:
        return bar, _frequency(pair[1], where)
    return bar, _decibels(pair[1], where)


def _lane(target: str, raw: object, where: str) -> Lane:
    points = tuple(_point(p, target, where) for p in (raw if isinstance(raw, list) else []))
    if not points:
        raise MixError(f"{where} needs a list of [bar, value] points")
    bars = [bar for bar, _ in points]
    if bars != sorted(bars):
        raise MixError(f"{where}: points go in order of their bars")
    if any(bars.count(bar) > 2 for bar in bars):  # noqa: PLR2004 - a jump is two points
        raise MixError(f"{where}: a bar holds at most two points, for a jump")
    return Lane(target, points)


def strip(
    raw: Mapping[str, Any] | None,
    moves: Mapping[str, Any] | None,
    where: str,
    *,
    extra: Mapping[str, Any] | None = None,
) -> Strip | None:
    """A strip from its settings and its ``automate`` table; None if the score
    gives neither. ``extra`` holds settings that belong to something else in
    the same table (a send's effect), so they aren't mistaken for typos."""
    if raw is None and moves is None:
        return None
    settings = {k: v for k, v in (raw or {}).items() if k not in (extra or {})}
    if unknown := set(settings) - STRIP_KEYS:
        shown = ", ".join(sorted(STRIP_KEYS))
        raise MixError(f"{where}: a strip takes {shown}, not {', '.join(sorted(unknown))}")
    resonance = _number(settings.get("resonance", 0.0), f"{where}, resonance")
    pan = _number(settings.get("pan", 0.0), f"{where}, pan")
    if not (0 <= resonance <= 1 and -1 <= pan <= 1):
        raise MixError(f"{where}: resonance goes from 0 to 1, and pan from -1 to 1")
    found = Strip(
        highpass=_frequency(settings["highpass"], where) if "highpass" in settings else 0.0,
        lowpass=_frequency(settings["lowpass"], where) if "lowpass" in settings else 0.0,
        resonance=resonance,
        level=_decibels(settings.get("level", 0.0), f"{where}, level"),
        pan=pan,
        sends=_sends(settings["send"], where) if "send" in settings else (),
    )
    lanes = tuple(
        _lane(str(target), points, f"{where}, automate {target}")
        for target, points in (moves or {}).items()
    )
    return replace(found, lanes=lanes)


def sends(raw: object) -> tuple[Send, ...]:
    """The score's ``sends`` tables, in the order they are written (REQ-MIX-02)."""
    if not isinstance(raw, dict):
        raise MixError("sends holds one table per send, like [sends.echo]")
    return tuple(_send(str(name), table) for name, table in raw.items())


def _send(name: str, raw: object) -> Send:
    where = f"send {name}"
    if not isinstance(raw, dict) or raw.get("kind") not in EFFECTS:
        raise MixError(f"{where} needs a kind: {', '.join(EFFECTS)}")
    kind = str(raw["kind"])
    known = EFFECTS[kind]
    if REQUIRED[kind] not in raw:
        raise MixError(f"{where}: a {kind} needs {REQUIRED[kind]}")
    table = {k: v for k, v in raw.items() if k not in ("kind", "automate")}
    effect = {k: v for k, v in table.items() if k in known}
    settings = {**known, **{k: _effect_value(k, v, where) for k, v in effect.items()}}
    _check_effect(kind, settings, where)
    found = strip(table, raw.get("automate"), where, extra=known) or Strip()
    return Send(name, kind, tuple(settings.items()), found)


def _effect_value(name: str, raw: object, where: str) -> float:
    if name == "pingpong":
        if not isinstance(raw, bool):
            raise MixError(f"{where}: pingpong is true or false")
        return float(raw)
    return _number(raw, f"{where}, {name}")


def _check_effect(kind: str, settings: Mapping[str, float], where: str) -> None:
    if kind == DELAY:
        if not 0 < settings["beats"] <= 8 or not 0 <= settings["feedback"] < 1:  # noqa: PLR2004
            raise MixError(f"{where}: beats goes above 0 up to 8, and feedback from 0 to under 1")
        _frequency(settings["lowpass"], f"{where}, lowpass")
        return
    if not 0 < settings["decay_s"] <= 30 or not 0 <= settings["predelay_ms"] <= MAX_PREDELAY_MS:  # noqa: PLR2004
        raise MixError(f"{where}: decay_s goes above 0 up to 30, predelay_ms from 0 to 500")
    _frequency(settings["damping"], f"{where}, damping")


def check(
    strips: Mapping[str, Strip], found: Sequence[Send], master: Strip | None, score_id: str
) -> None:
    """Every send a strip feeds exists, a return feeds only sends after it (so
    sends never feed back), the master sends nowhere, and every lane moves a
    control its strip has (REQ-MIX-01 to REQ-MIX-03)."""
    names = [send.name for send in found]
    if len(set(names)) != len(names):
        raise MixError(f"{score_id}: two sends share a name")
    clash = set(names) & set(MOVABLE)
    if clash:
        raise MixError(f"{score_id}: a send can't be called {sorted(clash)[0]}")
    for owner, own in strips.items():
        _routed(own, names, f"{score_id}: {owner}")
    for n, send in enumerate(found):
        later = names[n + 1 :]
        if early := [k for k, _ in send.strip.sends if k not in later]:
            raise MixError(
                f"{score_id}: send {send.name} feeds {early[0]}, but a send only feeds "
                "sends listed after it"
            )
        _routed(send.strip, names, f"{score_id}: send {send.name}")
    if master is not None:
        if master.sends or master.pan:
            raise MixError(f"{score_id}: the master has no sends and no pan")
        _routed(master, names, f"{score_id}: the master")


def _routed(own: Strip, names: Sequence[str], where: str) -> None:
    if unknown := [k for k, _ in own.sends if k not in names]:
        raise MixError(f"{where} sends to {unknown[0]!r}, which isn't one of the score's sends")
    sent = {k for k, _ in own.sends}
    for lane in own.lanes:
        if lane.target not in MOVABLE and lane.target not in sent:
            raise MixError(
                f"{where} automates {lane.target!r}; it can move "
                f"{', '.join((*MOVABLE, *sorted(sent)))}"
            )


# --- excerpts (REQ-TRK-03) -----------------------------------------------------------


def cut(own: Strip | None, first: int, last: int) -> Strip | None:
    """A strip as an excerpt of bars ``first`` to ``last`` plays it: each lane
    starts at the track's value at ``first``, keeps its points inside the
    excerpt, ends at the track's value just after ``last``, and counts its
    bars from the excerpt's first bar."""
    if own is None or not own.lanes:
        return own
    return replace(own, lanes=tuple(_cut(lane, first, last) for lane in own.lanes))


def _cut(lane: Lane, first: int, last: int) -> Lane:
    lead = first - 1
    end = last + 1
    inside = [(bar - lead, value) for bar, value in lane.points if first < bar < end]
    points = [(1.0, lane.at(first)), *inside, (float(end - lead), lane.at(end))]
    return Lane(lane.target, tuple(points))
