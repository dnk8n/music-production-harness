"""Song plans: a score as Renoise tracks, instruments and pattern notes (REQ-REN-01, REQ-REN-06).

At 4 lines per beat a pattern line is one sixteenth. A song with a triplet
voice runs at 12 lines per beat instead, so sixteenths fall every 3 lines and
triplets every 4 (REQ-BEN-13). A song with a melodic voice runs at 8, so a
plain note's gate, half a sixteenth, ends on a line of its own, where its
note-off goes (REQ-REN-09). A note's time becomes its line plus a delay in
1/256 of a line, so swing and an uneven feel live in the delay column where
Dean can see and change them. Velocity goes in the volume column (0x80 is
full). Chokes, and one-shots with a length limit, become note-offs.

A track's sections each start a pattern of their own, named in the pattern
sequence (spec/components/tracks.md, REQ-TRK-04). A clip voice's instrument
holds one sample per clip, each on its own key from C-4 up, so a cue is one
note; a clip stopped before its last bar gets a note-off (REQ-TRK-02).

A melodic voice plays a sampled synth (REQ-REN-09): one sample per pitch,
each on its own key with the keys between them sharing the nearest, and a
second instrument for its accented notes. A note-off ends each note's gate,
and a note tied into the next makes that one glide (0Gxx) instead of
starting again. Every song sets the track headroom to 0 dB (REQ-REN-12) and
12 ticks a line, which the glide speeds count in.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from mph.bench.score import Event, Score
from mph.renoise import mixplan
from mph.renoise.luadata import Data

LPB = 4  # lines per beat: a line is a sixteenth
TRIPLET_LPB = 12  # with a triplet voice: a sixteenth is 3 lines, a triplet 4 (REQ-BEN-13)
MELODIC_LPB = 8  # with a melodic voice: a line is half a sixteenth, a plain note's gate
BARS_A_PATTERN = 4
BEATS_A_BAR = 4
DELAY_STEPS = 256
FULL_VOLUME = 0x80
FINE_TUNE_STEPS = 128  # per semitone
FINE_TUNE_LIMIT = 127
CLIP_KEYS = 120 - 48  # clips sit on keys from C-4 (48) to B-9 (119)
TOP_NOTE = 119  # B-9
MIDI_OFFSET = 12  # Renoise's C-4 (48) is MIDI's middle C (60)
TPL = 12  # ticks per line, which glide speeds count in
GLIDE_STEPS = 16  # 0Gxx glides in sixteenths of a semitone a tick
FASTEST_GLIDE = 0xFF
HEADROOM_DB = 0.0  # REQ-REN-12


@dataclass(frozen=True, slots=True)
class Zone:
    """One sample of a sampled synth (REQ-REN-09): its file and the Renoise
    note it was sampled at."""

    file: str
    note: int


@dataclass(frozen=True, slots=True)
class Synth:
    """A melodic voice's sampled synth (REQ-REN-09)."""

    zones: tuple[Zone, ...]  # plain notes
    accents: tuple[Zone, ...] = ()  # accented notes, a second instrument
    release_s: float = 0.0  # the synth's release after a note-off
    glide_s: float = 0.0  # how long a slide takes; 0 is at once


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
    synth: Synth | None = None  # a melodic voice's sampled synth


def lpb(score: Score) -> int:
    """Lines per beat for a score's song (REQ-BEN-13, REQ-REN-09)."""
    if score.triplets:
        return TRIPLET_LPB
    return MELODIC_LPB if score.melodic else LPB


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


def renoise_note(pitch: int) -> int:
    """A MIDI note as Renoise numbers it (C-4 is 48), kept on the keyboard."""
    return min(max(pitch - MIDI_OFFSET, 0), TOP_NOTE)


def glide_speed(interval: int, glide_s: float, line_s: float) -> int:
    """0Gxx's amount: sixteenths of a semitone a tick, enough to cover
    ``interval`` semitones in ``glide_s`` (REQ-REN-09)."""
    if glide_s <= 0:
        return FASTEST_GLIDE
    ticks = max(1.0, glide_s / (line_s / TPL))
    return min(max(math.ceil(abs(interval) * GLIDE_STEPS / ticks), 1), FASTEST_GLIDE)


def key_ranges(notes: Sequence[int]) -> list[tuple[int, int]]:
    """The keys each sampled note plays on: its own, and those up to halfway to
    its neighbours, so every key plays the nearest sample."""
    ordered = sorted(notes)
    ranges = []
    for n, note in enumerate(ordered):
        low = 0 if n == 0 else (ordered[n - 1] + note) // 2 + 1
        high = TOP_NOTE if n + 1 == len(ordered) else (note + ordered[n + 1]) // 2
        ranges.append((low, high))
    return ranges


def _zones(zones: Sequence[Zone]) -> list[dict[str, Data]]:
    ordered = sorted(zones, key=lambda z: z.note)
    return [
        {"file": zone.file, "note": zone.note, "low": low, "high": high}
        for zone, (low, high) in zip(ordered, key_ranges([z.note for z in ordered]), strict=True)
    ]


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


def _gate_offs(own: Sequence[Event]) -> list[float]:
    """Where a melodic voice's gates end: after each note, unless it ties into
    the next or the next starts first (then the new note releases it)."""
    times = []
    for n, event in enumerate(own):
        end = event.time_s + event.length_s
        later = own[n + 1].time_s if n + 1 < len(own) else math.inf
        if not event.tie and later > end + 1e-9:
            times.append(end)
    return times


def melodic_notes(score: Score, events: Sequence[Event], track: Track) -> list[dict[str, Data]]:
    """A melodic voice's notes: each pitch, its velocity and whether it's
    accented; a tied note's successor glides; note-offs end the gates."""
    synth = track.synth
    assert synth is not None  # noqa: S101 - only melodic tracks come here
    line_s = seconds_per_line(score)
    own = sorted((e for e in events if e.voice == track.name), key=lambda e: e.time_s)
    placed: list[tuple[int, int, dict[str, Data]]] = []
    taken: set[int] = set()
    for n, event in enumerate(own):
        line, delay = place(event.time_s, line_s)
        note: dict[str, Data] = {"line": line, "delay": delay, "note": renoise_note(event.pitch)}
        if n and own[n - 1].tie:
            interval = event.pitch - own[n - 1].pitch
            note["glide"] = glide_speed(interval, synth.glide_s, line_s)
        else:
            note["volume"] = volume(event.velocity)
            note["accent"] = event.accent and bool(synth.accents)
        placed.append((line, delay, note))
        taken.add(line)
    for time_s in _gate_offs(own):
        line, delay = place(time_s, line_s)
        if line not in taken:
            placed.append((line, delay, {"line": line, "delay": delay, "volume": 0, "off": True}))
            taken.add(line)
    return [note for _, _, note in sorted(placed, key=lambda p: (p[0], p[1]))]


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


def pattern_lengths(score: Score, lines: int) -> list[tuple[int, str]]:
    """Each pattern's length in lines, and the name of the section it starts
    ("" for none): each section starts a pattern and runs over patterns of up
    to 4 bars (REQ-TRK-04). The rest of the song, all of it when there are no
    sections, is cut into 4-bar patterns."""
    bar = BEATS_A_BAR * lpb(score)
    found: list[tuple[int, str]] = []
    left = lines
    for section in score.sections:
        for first in range(section.first, section.last + 1, BARS_A_PATTERN):
            length = min(BARS_A_PATTERN, section.last + 1 - first) * bar
            found.append((length, section.name if first == section.first else ""))
            left -= length
    while left > 0:
        found.append((min(pattern_lines(score), left), ""))
        left -= pattern_lines(score)
    return found


def patterns(score: Score, lines: int) -> list[dict[str, Data]]:
    """The song's patterns for the bridge tool, a section's first one named after it."""
    found: list[dict[str, Data]] = []
    for length, section in pattern_lengths(score, lines):
        entry: dict[str, Data] = {"lines": length}
        if section:
            entry["section"] = section
        found.append(entry)
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
    }
    if track.clips is not None:
        found["clips"] = [{"file": file, "name": name} for file, name in track.clips]
    if track.synth is None:
        found["notes"] = notes(score, events, track.name)
        return found
    found["zones"] = _zones(track.synth.zones)
    if track.synth.accents:
        found["accents"] = _zones(track.synth.accents)
    found["release"] = round(track.synth.release_s, 6)
    found["notes"] = melodic_notes(score, events, track)
    return found


def _instruments(plans: Sequence[dict[str, Data]]) -> int:
    """Gives each track its instrument, after them each accent instrument in
    track order, and returns how many there are."""
    count = len(plans)
    for index, found in enumerate(plans, start=1):
        found["instrument"] = index
        if "accents" in found:
            count += 1
            found["accent_instrument"] = count
    return count


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
    """One song for the bridge tool: the plan, its mixer if the score mixes or
    ducks (REQ-REN-10, REQ-REN-11), where to save it, and what to render."""
    line_s = seconds_per_line(score)
    lines = lines_for(score.length_s if seconds is None else seconds, line_s)
    plans = [_track_plan(score, events, track) for track in tracks]
    lengths = [length for length, _ in pattern_lengths(score, lines)]
    found: dict[str, Data] = {
        "bpm": score.bpm,
        "lpb": lpb(score),
        "tpl": TPL,
        "headroom_db": HEADROOM_DB,
        "patterns": patterns(score, lines),
        "lines": lines,
        "rate": rate,
        "save": save,
        "instruments": _instruments(plans),
        "tracks": plans,
        "renders": list(renders),
    }
    bar_lines = BEATS_A_BAR * lpb(score)
    mixer = mixplan.plan(
        score, events, lengths, line_s=line_s, lpb=lpb(score), bar_lines=bar_lines, tpl=TPL
    )
    if mixer is not None:
        found["mix"] = mixer
        found["modes"] = {mode: list(roles) for mode, roles in mixplan.MODES.items()}
    return found
