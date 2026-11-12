"""Song plans: a score as Renoise tracks, instruments and pattern notes (REQ-REN-01, REQ-REN-06).

At 4 lines per beat a pattern line is one sixteenth. A song with a triplet
voice runs at 12 lines per beat instead, so sixteenths fall every 3 lines and
triplets every 4 (REQ-BEN-13). A note's time becomes its line plus a delay in
1/256 of a line, so swing and an uneven feel live in the delay column where
Dean can see and change them. Velocity goes in the volume column (0x80 is
full). Chokes, and one-shots with a length limit, become note-offs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from mph.bench.score import Event, Score
from mph.renoise.luadata import Data

LPB = 4  # lines per beat: a line is a sixteenth
TRIPLET_LPB = 12  # with a triplet voice: a sixteenth is 3 lines, a triplet 4 (REQ-BEN-13)
BARS_A_PATTERN = 4
BEATS_A_BAR = 4
DELAY_STEPS = 256
FULL_VOLUME = 0x80
FINE_TUNE_STEPS = 128  # per semitone
FINE_TUNE_LIMIT = 127


@dataclass(frozen=True, slots=True)
class Track:
    """One voice's track and instrument."""

    name: str
    sample: str  # the sound file Renoise loads (read in place, never written)
    gain: float  # linear, on the sample's volume (REQ-BEN-09)
    semitones: float = 0.0  # pitch change, e.g. to fit a break to the tempo
    slices: tuple[int, ...] = ()  # slice start frames in the sample


def lpb(score: Score) -> int:
    """Lines per beat for a score's song (REQ-BEN-13)."""
    return TRIPLET_LPB if score.triplets else LPB


def seconds_per_line(score: Score) -> float:
    return score.beat_s / lpb(score)


def pattern_lines(score: Score) -> int:
    return BARS_A_PATTERN * BEATS_A_BAR * lpb(score)


def place(time_s: float, line_s: float) -> tuple[int, int]:
    """(line, delay) for a time, the delay in 1/256 of a line."""
    line, rest = divmod(time_s / line_s, 1.0)
    delay = round(rest * DELAY_STEPS)
    if delay == DELAY_STEPS:
        return int(line) + 1, 0
    return int(line), delay


def placed_time(line: int, delay: int, line_s: float) -> float:
    return (line + delay / DELAY_STEPS) * line_s


def volume(velocity: float) -> int:
    return min(max(round(velocity * FULL_VOLUME), 0), FULL_VOLUME)


def tuning(semitones: float) -> tuple[int, int]:
    """(transpose, fine_tune) for a pitch change in semitones."""
    transpose = round(semitones)
    fine = round((semitones - transpose) * FINE_TUNE_STEPS)
    return transpose, min(max(fine, -FINE_TUNE_LIMIT), FINE_TUNE_LIMIT)


def _offs(score: Score, events: Sequence[Event], name: str) -> list[float]:
    """Times at which a voice must stop: choked by another voice, or its length limit."""
    voice = score.voice(name)
    own = [e.time_s for e in events if e.voice == name]
    times = [
        e.time_s
        for e in events
        if name in score.voice(e.voice).chokes and any(t < e.time_s for t in own)
    ]
    if voice.seconds:
        for n, start in enumerate(own):
            end = start + voice.seconds
            if n + 1 == len(own) or own[n + 1] > end:
                times.append(end)
    return sorted(times)


def notes(score: Score, events: Sequence[Event], name: str) -> list[dict[str, Data]]:
    line_s = seconds_per_line(score)
    placed: list[tuple[int, int, dict[str, Data]]] = []
    taken: set[int] = set()
    for event in (e for e in events if e.voice == name):
        line, delay = place(event.time_s, line_s)
        note: dict[str, Data] = {"line": line, "delay": delay, "volume": volume(event.velocity)}
        if event.slice:
            note["slice"] = event.slice
        placed.append((line, delay, note))
        taken.add(line)
    for time_s in _offs(score, events, name):
        line, delay = place(time_s, line_s)
        if line not in taken:  # a new note on that line cuts the old one anyway
            off: dict[str, Data] = {"line": line, "delay": delay, "volume": 0, "off": True}
            placed.append((line, delay, off))
            taken.add(line)
    return [note for _, _, note in sorted(placed, key=lambda p: (p[0], p[1]))]


def lines_for(seconds: float, line_s: float) -> int:
    return max(1, math.ceil(round(seconds / line_s, 6)))


def song(
    score: Score,
    events: Sequence[Event],
    tracks: Sequence[Track],
    *,
    rate: int,
    save: str,
    renders: Sequence[dict[str, Data]],
    seconds: float | None = None,
) -> dict[str, Data]:
    """One song for the bridge tool: the plan, where to save it, and what to render."""
    track_plans: list[Data] = []
    for track in tracks:
        transpose, fine_tune = tuning(track.semitones)
        track_plans.append(
            {
                "name": track.name,
                "sample": track.sample,
                "volume": round(track.gain, 6),
                "transpose": transpose,
                "fine_tune": fine_tune,
                "slices": list(track.slices),
                "notes": notes(score, events, track.name),
            }
        )
    return {
        "bpm": score.bpm,
        "lpb": lpb(score),
        "pattern_lines": pattern_lines(score),
        "lines": lines_for(score.length_s if seconds is None else seconds, seconds_per_line(score)),
        "rate": rate,
        "save": save,
        "tracks": track_plans,
        "renders": list(renders),
    }
