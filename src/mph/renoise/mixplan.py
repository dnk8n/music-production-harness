"""A score's mix as a Renoise mixer (spec/components/renoise.md, REQ-REN-10, REQ-REN-11).

Each voice's track gets, after Renoise's own mixer device: a Gainer named Duck
when the sidechain ducks it, a Digital Filter for each strip filter it uses, a
Gainer for its level and pan, and a #Send for each of its sends. A voice whose
strip filters nothing keeps REQ-REN-06's bypassed filter, before its strip's
devices. Each send is a send track after the master: its effect (a Multitap's
first tap for a delay, mpReverb 2 for a reverb), then its return strip. The
master's strip sits on the master track.

Values are written as Renoise shows them (Hz, dB, ms, %), and the bridge tool
finds the parameter value that shows each one. The names are those Renoise
3.5.4 gives its devices (scripts/renoise-check lists them). A #Send's mode
isn't a parameter, so it is set in the device's preset data. Automation
becomes points in each pattern: one at each lane point, one every beat along
a move, and one at each pattern's start and end, so a move sounds the same
whatever scale Renoise gives the parameter. The duck is a curve of points at
every trigger hit; dips that overlap add up, never past the depth, as
SuperCollider's do. It is drawn two ticks early, because Renoise applies
automation once a tick and its Gainer glides to each new value: drawn at the
hit, a dip lands about two ticks late.

Every device has a role, which the renders switch on and off: a stem is
rendered with the strips, sends, effects and master devices off and the duck
on, and a sidechain target once more with its duck off too (REQ-REN-03).
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mph.bench import mix
from mph.bench.mix import Lane, Send, Strip
from mph.bench.score import Event, Score, Sidechain
from mph.renoise.luadata import Data

FILTER = "Audio/Effects/Native/Digital Filter"
GAINER = "Audio/Effects/Native/Gainer"
SEND = "Audio/Effects/Native/#Send"
MULTITAP = "Audio/Effects/Native/Multitap"
REVERB = "Audio/Effects/Native/mpReverb 2"

# What each device does, which the renders switch on and off (REQ-REN-03).
DUCK, STRIP, SENDS, EFFECT, MASTER, SPARE = "duck", "strip", "send", "effect", "master", "spare"
MODES = {  # the roles left on in each kind of render
    "mix": (DUCK, STRIP, SENDS, EFFECT, MASTER),
    "stem": (DUCK,),
    "dry": (),
}

# Parameter names as Renoise shows them; the first one a device has is used.
# Renoise 3.5.4's, as scripts/renoise-check listed them on Dean's Mac (2026-11-20).
TYPE = ("Type", "Filter Type")
MODEL = ("Model",)  # the filter's model: in its preset data, not a parameter
CUTOFF = ("Cutoff", "Frequency", "Freq.", "Freq")
RESONANCE = ("Resonance", "Reso")  # a share of the knob, shown in %, not a Q; Biquad only
GAIN = ("Gain",)
PANNING = ("Panning", "Pan")
AMOUNT = ("Amount",)
RECEIVER = ("Receiver",)  # shows the send track's number, from 0
MUTE_SOURCE = ("MuteSource",)  # a #Send's mode: in its preset data, not a parameter
WET_ONLY = ("Mute Dry", "Mute Src.", "Mute Source", "Mute Src", "Dry Mute")  # On: fully wet
DURATION = ("Duration",)
PREDELAY = ("Predelay", "Pre-Delay", "Pre Delay", "PreDelay")
DRY = ("Dry Mix", "Dry")
WET = ("Wet Mix", "Wet")
DAMPING = ("High Cut", "Damping", "Damp", "HF Damp")  # mpReverb 2 has none in 3.5.4

STEP = 1 / 256  # Renoise places automation points to 1/256 of a line
SILENT_DB = mix.SILENT_DB  # silence; the tool sets the parameter's lowest value
RELEASE_POINTS = 16  # points along the duck's release (REQ-REN-11)
TAPS = 4  # a Multitap's taps; only the first plays
JUMP = 1e-9  # a curve that changes more than this at a line jumps there
DUCK_LEAD_TICKS = 2  # the duck is drawn this many ticks early (REQ-REN-11)

Param = dict[str, Data]
Device = dict[str, Data]
Moves = dict[str, tuple[int, tuple[str, ...], str]]  # control -> (device, parameter, unit)


@dataclass(frozen=True, slots=True)
class Timeline:
    """Where the song's patterns start and how long they are, in lines."""

    lengths: tuple[int, ...]
    line_s: float
    lpb: int
    bar_lines: int

    @property
    def starts(self) -> tuple[int, ...]:
        return tuple(itertools.accumulate((0, *self.lengths[:-1])))

    def line(self, bar: float) -> float:
        """The line where a place in the track (bars from 1) falls."""
        return (bar - 1) * self.bar_lines


@dataclass(frozen=True, slots=True)
class Curve:
    """A control's value over the song, as Renoise shows it (Hz or dB), at a
    line counted from the song's start."""

    value: Callable[[float], float]  # at a line, after any jump there
    before: Callable[[float], float]  # just before a line
    times: tuple[float, ...]  # lines where it bends or jumps, in order


def param(names: Sequence[str], value: Data, unit: str, *, optional: bool = False) -> Param:
    """One parameter setting. ``unit`` says how the tool finds the value: by
    what Renoise shows (Hz, dB, ms, % or a plain number, read only in that
    unit), a choice's text, a fraction of the range, a raw value, a send
    track's name, or ``preset``: a setting in the device's preset data, by
    its XML tag."""
    found: Param = {"names": list(names), "value": value, "unit": unit}
    if optional:
        found["optional"] = True
    return found


def _decibels(value: float) -> float:
    return round(max(value, SILENT_DB), 4)


def _filter(name: str, kind: str, hz: float, role: str, resonance: float = 0.0) -> Device:
    """A Digital Filter, its model chosen as a Renoise user would. A plain
    one is Butterworth 4n, 24 dB an octave like the SuperCollider strip's
    filters. Butterworth has no resonance (Renoise blanks that knob), so a
    resonant one keeps Biquad, Renoise's own model, with its resonance knob
    turned the strip's share of the way (0 to 1): Renoise shows resonance in
    %, not as a Q. The model is set in the preset data; if Renoise won't take
    it, the filter keeps its own."""
    if resonance <= 0:
        return {
            "path": FILTER,
            "name": name,
            "role": role,
            "params": [
                param(TYPE, kind, "choice"),
                param(MODEL, "Butterworth 4n", "preset", optional=True),
                param(CUTOFF, round(hz, 3), "Hz"),
            ],
        }
    params = [
        param(TYPE, kind, "choice"),
        param(MODEL, "Biquad", "preset", optional=True),
        param(CUTOFF, round(hz, 3), "Hz"),
        param(RESONANCE, round(resonance * 100, 3), "%"),
    ]
    return {"path": FILTER, "name": name, "role": role, "params": params}


def _gainer(name: str, db: float, pan: float, role: str) -> Device:
    params = [param(GAIN, _decibels(db), "dB")]
    if pan:
        params.append(param(PANNING, round((pan + 1) / 2, 4), "fraction"))
    return {"path": GAINER, "name": name, "role": role, "params": params}


def _send(to: str, index: int, db: float) -> Device:
    return {
        "path": SEND,
        "name": f"Send to {to}",
        "role": SENDS,
        "params": [
            param(AMOUNT, _decibels(db), "dB"),
            param(RECEIVER, {"name": to, "index": index}, "send"),
            param(MUTE_SOURCE, "false", "preset"),  # the voice carries on
        ],
    }


def strip_devices(own: Strip, sends: Sequence[str], role: str) -> tuple[list[Device], Moves]:
    """A strip's devices, and for each control a lane can move: its device's
    place in the list (from 1), the parameter and its unit."""
    devices: list[Device] = []
    moves: Moves = {}
    if own.uses("highpass"):
        devices.append(_filter("Highpass", "HighPass", own.start("highpass"), role))
        moves["highpass"] = (len(devices), CUTOFF, "Hz")
    if own.uses("lowpass"):
        devices.append(_filter("Lowpass", "LowPass", own.start("lowpass"), role, own.resonance))
        moves["lowpass"] = (len(devices), CUTOFF, "Hz")
    if own.level or own.pan or own.lane("level"):
        devices.append(_gainer("Level", own.start("level"), own.pan, role))
        moves["level"] = (len(devices), GAIN, "dB")
    for name, _ in own.sends:
        devices.append(_send(name, sends.index(name), own.start(name)))
        moves[name] = (len(devices), AMOUNT, "dB")
    return devices, moves


def pattern_points(curve: Curve, timeline: Timeline) -> list[dict[str, Data]]:
    """The curve as automation points in each pattern, at its own lines from 1:
    the pattern's start and end, each bend, and both sides of each jump. Each
    point is placed on the nearest 1/256 of a line with the value the curve
    has at its own time; silence is written as SILENT_DB."""
    out: list[dict[str, Data]] = []
    for position, (start, length) in enumerate(
        zip(timeline.starts, timeline.lengths, strict=True), start=1
    ):
        last = start + length - STEP
        slots: dict[int, float] = {0: curve.value(start)}
        lo = bisect.bisect_right(curve.times, start)
        hi = bisect.bisect_left(curve.times, last)
        for t in curve.times[lo:hi]:
            slot = round((t - start) / STEP)
            if slot > 0 and abs(curve.value(t) - curve.before(t)) > JUMP:
                slots[slot - 1] = curve.before(t)
            slots[slot] = curve.value(t)  # its own value, so a dip's floor isn't missed
        slots[round(length / STEP) - 1] = curve.value(last)
        points: list[Data] = [
            [1 + slot * STEP, round(max(v, SILENT_DB), 4)] for slot, v in sorted(slots.items())
        ]
        out.append({"pattern": position, "points": points})
    return out


def lane_curve(lane: Lane, timeline: Timeline) -> Curve:
    """A lane over the song: its own points, and a point every beat along a move.
    It is worked out in lines, so a jump (two points at one place) is found
    exactly: just before it, the value it jumps from; at it, the value it jumps to."""
    lines = [timeline.line(bar) for bar, _ in lane.points]
    values = [v if lane.in_octaves else max(v, SILENT_DB) for _, v in lane.points]

    def at(t: float, after: int) -> float:  # ``after``: the points at or before t
        if after == 0:
            return values[0]
        if after == len(values):
            return values[-1]
        share = (t - lines[after - 1]) / (lines[after] - lines[after - 1])
        return mix.between(values[after - 1], values[after], share, octaves=lane.in_octaves)

    times = set(lines)
    for (first, v0), (end, v1) in itertools.pairwise(zip(lines, values, strict=True)):
        if v0 == v1 or first == end:
            continue
        beat = math.floor(first / timeline.lpb) + 1
        while beat * timeline.lpb < end:
            times.add(float(beat * timeline.lpb))
            beat += 1
    return Curve(
        value=lambda t: at(t, bisect.bisect_right(lines, t)),
        before=lambda t: at(t, bisect.bisect_left(lines, t)),
        times=tuple(sorted(times)),
    )


def duck_curve(
    hits: Sequence[float], chain: Sidechain, timeline: Timeline, lead_s: float = 0.0
) -> Curve:
    """The Duck's gain in dB (REQ-REN-11): down to the depth over the attack at
    every hit, held, then back along a half cosine over the release, all drawn
    ``lead_s`` early. Dips that overlap add up, never past the depth (REQ-BEN-12)."""
    depth = 1 - 10 ** (-chain.depth_db / 20)
    attack, hold, release = (ms / 1000 for ms in (chain.attack_ms, chain.hold_ms, chain.release_ms))
    span = attack + hold + release
    starts = sorted(hits)

    def dip(dt: float) -> float:
        if dt < 0 or dt >= span:
            return 0.0
        if dt < attack:
            return depth * dt / attack
        if dt < attack + hold:
            return depth
        return depth * (1 + math.cos(math.pi * (dt - attack - hold) / release)) / 2

    def gain_db(t: float) -> float:
        now = t * timeline.line_s + lead_s
        lo, hi = bisect.bisect_left(starts, now - span), bisect.bisect_right(starts, now)
        taken = min(depth, sum(dip(now - h) for h in starts[lo:hi]))
        return 20 * math.log10(max(1 - taken, 1e-9))

    marks = [0.0, attack, attack + hold]
    marks += [attack + hold + release * k / RELEASE_POINTS for k in range(1, RELEASE_POINTS + 1)]
    times = sorted({(h + m - lead_s) / timeline.line_s for h in starts for m in marks})
    return Curve(value=gain_db, before=gain_db, times=tuple(t for t in times if t >= 0))


def automation(
    device: int, names: Sequence[str], unit: str, curve: Curve, timeline: Timeline
) -> dict[str, Data]:
    """A parameter's points in every pattern. ``device`` is its place among the
    track's planned devices, from 1."""
    return {
        "device": device,
        "names": list(names),
        "unit": unit,
        "patterns": pattern_points(curve, timeline),
    }


def _strip(
    own: Strip | None, sends: Sequence[str], timeline: Timeline, role: str, after: int
) -> tuple[list[Device], list[dict[str, Data]]]:
    """A strip's devices and its lanes' automation, placed after ``after``
    devices already on the track."""
    if own is None:
        return [], []
    devices, moves = strip_devices(own, sends, role)
    lanes = []
    for lane in own.lanes:
        device, names, unit = moves[lane.target]
        lanes.append(automation(after + device, names, unit, lane_curve(lane, timeline), timeline))
    return devices, lanes


def voice_track(score: Score, name: str, timeline: Timeline, duck: Curve | None) -> dict[str, Data]:
    """A voice's devices and automation: its duck, its strip, or REQ-REN-06's
    bypassed filter. The Duck starts where its curve does, so a hit on the
    song's first line doesn't glide there."""
    devices: list[Device] = []
    lanes: list[dict[str, Data]] = []
    if duck is not None:
        devices.append(_gainer("Duck", duck.value(0.0), 0.0, DUCK))
        lanes.append(automation(1, GAIN, "dB", duck, timeline))
    own = score.voice(name).strip
    if own is None or not (own.uses("highpass") or own.uses("lowpass")):
        devices.append({"path": FILTER, "name": "Filter", "role": SPARE, "params": []})
    sends = [s.name for s in score.sends]
    strip, more = _strip(own, sends, timeline, STRIP, len(devices))
    return {"devices": [*devices, *strip], "automation": [*lanes, *more]}


def _effect(send: Send, timeline: Timeline) -> Device:
    """A send's effect, fully wet: a Multitap's first tap for a delay, line-synced,
    with its low-pass on the input and in the feedback path (so the first echo
    is filtered once and each one after it once more, as in SuperCollider);
    mpReverb 2 for a reverb.

    With line sync on, a tap's time in lines is its "Sync Time". Its "Sync
    DelayTime" is a pattern command's target that always reads -1."""
    if send.kind == mix.DELAY:
        lines = max(1, round(send.setting("beats") * timeline.lpb))
        feedback = round(send.setting("feedback") * 100, 3)
        params = [
            param(WET_ONLY, "On", "choice"),
            param(("T1 Line Sync",), "On", "choice"),
            param(("T1 L Sync Time",), lines, "number"),
            param(("T1 R Sync Time",), lines, "number"),
            param(("T1 L Feedback",), feedback, "%"),
            param(("T1 R Feedback",), feedback, "%"),
            param(("T1 Ping Pong",), "On" if send.setting("pingpong") else "Off", "choice"),
            param(("T1 Amount",), 0.0, "dB"),
            param(("T1 Flt Mode",), "In + Feed", "choice"),
            param(("T1 Flt Type",), "Lowpass", "choice"),
            param(("T1 Flt Freq.",), round(send.setting("lowpass"), 3), "Hz"),
        ]
        params += [
            param((f"T{k} Amount",), SILENT_DB, "dB", optional=True) for k in range(2, TAPS + 1)
        ]
        return {"path": MULTITAP, "name": send.name, "role": EFFECT, "params": params}
    params = [
        param(DURATION, round(send.setting("decay_s") * 1000, 3), "ms"),
        param(PREDELAY, round(send.setting("predelay_ms"), 3), "ms"),
        param(DRY, 0.0, "%"),
        param(WET, 100.0, "%"),
        param(DAMPING, round(send.setting("damping"), 3), "Hz", optional=True),
    ]
    return {"path": REVERB, "name": send.name, "role": EFFECT, "params": params}


def send_track(score: Score, send: Send, timeline: Timeline) -> dict[str, Data]:
    """A send track: its effect, then its return strip."""
    sends = [s.name for s in score.sends]
    strip, lanes = _strip(send.strip, sends, timeline, STRIP, 1)
    return {"name": send.name, "devices": [_effect(send, timeline), *strip], "automation": lanes}


def master_track(score: Score, timeline: Timeline) -> dict[str, Data]:
    strip, lanes = _strip(score.master, [], timeline, MASTER, 0)
    return {"devices": strip, "automation": lanes}


def plan(
    score: Score,
    events: Sequence[Event],
    lengths: Sequence[int],
    *,
    line_s: float,
    lpb: int,
    bar_lines: int,
    tpl: int,
) -> dict[str, Data] | None:
    """The song's mixer, or None for a score with no mix and no sidechain.
    ``lengths`` are the song's patterns, in lines, of ``tpl`` ticks each."""
    chain = score.sidechain if score.sidechain is not None and score.sidechain.active else None
    if not score.mixed and chain is None:
        return None
    timeline = Timeline(tuple(lengths), line_s, lpb, bar_lines)
    duck, targets = None, set()
    if chain is not None:
        hits = [e.time_s for e in events if e.voice == chain.trigger]
        lead_s = DUCK_LEAD_TICKS * line_s / tpl
        duck, targets = duck_curve(hits, chain, timeline, lead_s), set(chain.targets)
    return {
        "tracks": [
            voice_track(score, v.name, timeline, duck if v.name in targets else None)
            for v in score.voices
        ],
        "sends": [send_track(score, s, timeline) for s in score.sends],
        "master": master_track(score, timeline),
    }
