"""A stand-in for Renoise and the bridge tool, so render tests never run Renoise.

It acts as the clock ``bridge.wait`` sleeps on: the first time mph waits, it
reads the job mph wrote (through LuaJIT or Lua 5.1, so the job is checked to
be valid Lua), plays each song the way Renoise would, and writes the result file.
Playing a song means: every note starts at its line plus delay, at its
volume times the sample's gain; a later note or a note-off in the same track
cuts it; slices play from their marker to the next one; a clip voice's note
plays that clip's sample (REQ-TRK-02); the pitch change resamples the sound.
A sampled synth (REQ-REN-09) plays the zone its note falls in, from the
instrument the note names (accented, or for its syllable), transposed from
the zone's own note; a glide plays on the note before it; a cut or note-off fades over the
synth's release. A render in the "mix" or "stem" mode ducks each target
with its Duck's automation (REQ-REN-11); the fake has no other mixer, so a
mix's master is its ducked tracks' sum. Its result lists one device kind's
parameters, and each mixed song (with no devices shown), as the real tool's does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
import soxr

from mph.bench import audio
from mph.renoise import bridge
from tests.renoise import lua

FULL = 0x80


def _played(path: str, track: dict[str, Any], rate: int) -> tuple[np.ndarray, float]:
    """A sample as the song plays it: stereo, at the render rate, at its gain."""
    data, native = soundfile.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    pitch = 2 ** ((track["transpose"] + track["fine_tune"] / 128) / 12)
    factor = rate / native / pitch
    played = soxr.resample(data, native, round(native * factor)) if factor != 1 else data
    return played.astype(np.float32) * float(track["volume"]), factor


def _sound(track: dict[str, Any], rate: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    played, factor = _played(track["sample"], track, rate)
    markers = [round(m * factor) for m in track["slices"]]
    bounds = list(zip(markers, [*markers[1:], len(played)][: len(markers)], strict=True))
    return played, bounds


def _synth(track: dict[str, Any], rate: int) -> Callable[[dict[str, Any]], np.ndarray]:
    """What a sampled synth's note plays: its zone's sample, transposed."""
    cache: dict[tuple[str, int], np.ndarray] = {}

    def piece(note: dict[str, Any]) -> np.ndarray:
        zones = track["more"][note["sound"] - 1]["zones"] if "sound" in note else track["zones"]
        zone = next(z for z in zones if z["low"] <= note["note"] <= z["high"])
        shift = note["note"] - zone["note"]
        if (zone["file"], shift) not in cache:
            shifted = {**track, "transpose": shift, "fine_tune": 0}
            cache[zone["file"], shift] = _played(zone["file"], shifted, rate)[0]
        return cache[zone["file"], shift]

    return piece


def _player(track: dict[str, Any], rate: int) -> Callable[[dict[str, Any]], np.ndarray]:
    """What each note of a track plays: its clip, its slice, its synth's zone,
    or the whole sound."""
    if "zones" in track:
        return _synth(track, rate)
    if "clips" in track:
        clips = [_played(clip["file"], track, rate)[0] for clip in track["clips"]]
        return lambda note: clips[note["clip"] - 1]
    sound, bounds = _sound(track, rate)

    def piece(note: dict[str, Any]) -> np.ndarray:
        if "slice" in note:
            first, last = bounds[note["slice"] - 1]
            return sound[first:last]
        return sound

    return piece


def _track(song: dict[str, Any], track: dict[str, Any], frames: int) -> np.ndarray:
    rate = song["rate"]
    line_s = 60.0 / song["bpm"] / song["lpb"]
    play = _player(track, rate)
    release = round(float(track.get("release", 0.0)) * rate)
    out = np.zeros((frames, 2), np.float32)
    notes = [n for n in track["notes"] if "glide" not in n]  # a glide bends the note before it
    starts = [round((n["line"] + n["delay"] / 256) * line_s * rate) for n in notes]
    for n, note in enumerate(notes):
        if note.get("off"):
            continue
        start = starts[n]
        end = min(starts[n + 1] if n + 1 < len(starts) else frames, frames)
        sound = play(note) * (note["volume"] / FULL)
        held = max(0, end - start)
        tail = (
            sound[held : held + release]
            * np.linspace(1, 0, release, endpoint=False)[:, None][: max(0, len(sound) - held)]
        )
        piece = np.concatenate([sound[:held], tail])[: frames - start]
        out[start : start + len(piece)] += piece
    return out


def _duck(song: dict[str, Any], index: int, frames: int) -> np.ndarray | None:
    """The gain a track's Duck is automated to, sample by sample, or None."""
    mixer = song.get("mix")
    if mixer is None:
        return None
    planned = mixer["tracks"][index]
    roles = [d["role"] for d in planned["devices"]]
    if "duck" not in roles:
        return None
    lane = next(a for a in planned["automation"] if a["device"] == roles.index("duck") + 1)
    line_s = 60.0 / song["bpm"] / song["lpb"]
    starts = np.cumsum([0] + [p["lines"] for p in song["patterns"]])
    times, gains = [], []
    for part in lane["patterns"]:
        for time, db in part["points"]:
            times.append((starts[part["pattern"] - 1] + time - 1) * line_s)
            gains.append(10 ** (db / 20))
    seconds = np.arange(frames) / song["rate"]
    gain: np.ndarray = np.interp(seconds, times, gains).astype(np.float32)
    return gain[:, None]


def perform(job: dict[str, Any]) -> list[str]:
    files = []
    for song in job["songs"]:
        line_s = 60.0 / song["bpm"] / song["lpb"]
        frames = round(song["lines"] * line_s * song["rate"])
        dry = [_track(song, t, frames) for t in song["tracks"]]
        ducks = [_duck(song, n, frames) for n in range(len(dry))]
        ducked = [t if d is None else t * d for t, d in zip(dry, ducks, strict=True)]
        Path(song["save"]).write_bytes(b"fake xrns")
        for wanted in song["renders"]:
            tracks = dry if wanted.get("mode") == "dry" else ducked
            solo = wanted.get("solo")
            mix = tracks[solo - 1] if solo else np.sum(tracks, axis=0)
            audio.write_float(Path(wanted["file"]), mix, song["rate"])
            files.append(wanted["file"])
    return files


DESCRIBED = {"Audio/Effects/Native/Gainer": [{"name": "Gain", "shows": ["-INF dB", "0.000 dB"]}]}


class FakeRenoise:
    """The clock mph waits on, doing the tool's work when first asked to sleep."""

    def __init__(self, jobs: Path, *, broken: bool = False) -> None:
        self.jobs = jobs
        self.broken = broken
        self.performed: list[dict[str, Any]] = []
        self.waited = 0.0

    def now(self) -> float:
        return self.waited

    def sleep(self, seconds: float) -> None:
        self.waited += seconds
        for job_file in sorted(self.jobs.glob("*.job.lua")):
            job = lua.run("data", str(job_file))
            job_file.unlink()
            result: dict[str, Any] = {"ok": False, "error": "could not load the kick"}
            if not self.broken:
                result = {"ok": True, "renoise": "3.5.4 (fake)", "files": perform(job)}
                mixed = [song for song in job["songs"] if "mix" in song]
                if mixed:
                    result["devices"] = DESCRIBED
                    result["shown"] = [
                        {"song": Path(s["save"]).stem, "tracks": [], "envelopes": []} for s in mixed
                    ]
                self.performed.append(job)
            (self.jobs / job_file.name.replace(".job.lua", ".result.json")).write_text(
                json.dumps(result), "utf-8"
            )

    def clock(self) -> bridge.Clock:
        return bridge.Clock(now=self.now, sleep=self.sleep)
