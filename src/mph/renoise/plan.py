"""Song plans: a score as Renoise tracks, instruments and pattern notes (REQ-REN-01, REQ-REN-06).

At 4 lines per beat a pattern line is one sixteenth. A song with a triplet
voice runs at 12 lines per beat instead, so sixteenths fall every 3 lines and
triplets every 4 (REQ-BEN-13). A note's time becomes its line plus a delay in
1/256 of a line, so swing and an uneven feel live in the delay column where
Dean can see and change them. Velocity goes in the volume column (0x80 is
full). Chokes, and one-shots with a length limit, become note-offs.

A track's sections each start a pattern of their own, named in the pattern
sequence (spec/components/tracks.md, REQ-TRK-04). A clip voice's instrument
holds one sample per clip, each on its own key from C-4 up, so a cue is one
note; a clip stopped before its last bar gets a note-off (REQ-TRK-02).
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
CLIP_KEYS = 120 - 48  # clips sit on keys from C-4 (48) to B-9 (119)


@dataclass(frozen=True, slots=True)
class Track:
    """One voice's track and instrument."""

    name: str
    sample: str  # the sound file Renoise loads (read in place, never written)
    gain: float  # linear, on the sample's volume (REQ-BEN-09)
    semitones: float = 0.0  # pitch change, e.g. to fit a break to the tempo
    slices: tuple[int, ...] = ()  # slice start frames in the sample
    # A clip voice's (file, name), one sample each; None for any other voice.
    clips: tuple[tuple[str, str], ...] | None = None


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


def _clip_offs(score: Score, events: Sequence[Event], name: str) -> list[float]:
    """Where clips stop before their own last bar (REQ-TRK-02)."""
    clips = score.voice(name).clips
    return [
        e.time_s + e.length_s
        for e in events
        if e.voice == name and round(e.length_s / score.bar_s) < clips[e.clip - 1].bars
    ]


def _offs(score: Score, events: Sequence[Event], name: str) -> list[float]:
    """Times at which a voice must stop: choked by another voice, its length
    limit, or the end of a clip's play."""
    voice = score.voice(name)
    if voice.clips:
        return _clip_offs(score, events, name)
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
        if event.clip:
            note["clip"] = event.clip
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


def patterns(score: Score, lines: int) -> list[dict[str, Data]]:
    """Pattern lengths: each section starts a pattern, named after it, and runs
    over patterns of up to 4 bars (REQ-TRK-04). The rest of the song, all of it
    when there are no sections, is cut into 4-bar patterns."""
    bar = BEATS_A_BAR * lpb(score)
    found: list[dict[str, Data]] = []
    left = lines
    for section in score.sections:
        for first in range(section.first, section.last + 1, BARS_A_PATTERN):
            length = min(BARS_A_PATTERN, section.last + 1 - first) * bar
            entry: dict[str, Data] = {"lines": length}
            if first == section.first:
                entry["section"] = section.name
            found.append(entry)
            left -= length
    while left > 0:
        found.append({"lines": min(pattern_lines(score), left)})
        left -= pattern_lines(score)
    return found


def _track_plan(score: Score, events: Sequence[Event], track: Track) -> dict[str, Data]:
    transpose, fine_tune = tuning(track.semitones)
    if (count := len(track.clips or ())) > CLIP_KEYS:
        raise ValueError(f"{track.name} has {count} clips; a Renoise instrument fits {CLIP_KEYS}")
    found: dict[str, Data] = {
        "name": track.name,
        "sample": track.sample,
        "volume": round(track.gain, 6),
        "transpose": transpose,
        "fine_tune": fine_tune,
        "slices": list(track.slices),
        "notes": notes(score, events, track.name),
    }
    if track.clips is not None:
        found["clips"] = [{"file": file, "name": name} for file, name in track.clips]
    return found


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
    lines = lines_for(score.length_s if seconds is None else seconds, seconds_per_line(score))
    return {
        "bpm": score.bpm,
        "lpb": lpb(score),
        "patterns": patterns(score, lines),
        "lines": lines,
        "rate": rate,
        "save": save,
        "tracks": [_track_plan(score, events, track) for track in tracks],
        "renders": list(renders),
    }
