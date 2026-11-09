"""A stand-in for Renoise and the bridge tool, so render tests never run Renoise.

It acts as the clock ``bridge.wait`` sleeps on: the first time mph waits, it
reads the job mph wrote (through lua5.1, so the job is checked to be valid
Lua), plays each song the way Renoise would, and writes the result file.
Playing a song means: every note starts at its line plus delay, at its
volume times the sample's gain; a later note or a note-off in the same track
cuts it; slices play from their marker to the next one; the pitch change
resamples the sound.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
import soxr

from mph.bench import audio
from mph.renoise import bridge
from tests.renoise import lua

FULL = 0x80


def _sound(track: dict[str, Any], rate: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    data, native = soundfile.read(track["sample"], dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    pitch = 2 ** ((track["transpose"] + track["fine_tune"] / 128) / 12)
    factor = rate / native / pitch
    played = soxr.resample(data, native, round(native * factor)) if factor != 1 else data
    markers = [round(m * factor) for m in track["slices"]]
    bounds = list(zip(markers, [*markers[1:], len(played)][: len(markers)], strict=True))
    return played.astype(np.float32) * float(track["volume"]), bounds


def _track(song: dict[str, Any], track: dict[str, Any], frames: int) -> np.ndarray:
    rate = song["rate"]
    line_s = 60.0 / song["bpm"] / song["lpb"]
    sound, bounds = _sound(track, rate)
    out = np.zeros((frames, 2), np.float32)
    starts = [round((n["line"] + n["delay"] / 256) * line_s * rate) for n in track["notes"]]
    for n, note in enumerate(track["notes"]):
        if note.get("off"):
            continue
        start = starts[n]
        end = starts[n + 1] if n + 1 < len(starts) else frames
        piece = sound
        if "slice" in note:
            first, last = bounds[note["slice"] - 1]
            piece = sound[first:last]
        piece = piece[: max(0, min(end, frames) - start)] * (note["volume"] / FULL)
        out[start : start + len(piece)] += piece
    return out


def perform(job: dict[str, Any]) -> list[str]:
    files = []
    for song in job["songs"]:
        line_s = 60.0 / song["bpm"] / song["lpb"]
        frames = round(song["lines"] * line_s * song["rate"])
        tracks = [_track(song, t, frames) for t in song["tracks"]]
        Path(song["save"]).write_bytes(b"fake xrns")
        for wanted in song["renders"]:
            solo = wanted.get("solo")
            mix = tracks[solo - 1] if solo else np.sum(tracks, axis=0)
            audio.write_float(Path(wanted["file"]), mix, song["rate"])
            files.append(wanted["file"])
    return files


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
                self.performed.append(job)
            (self.jobs / job_file.name.replace(".job.lua", ".result.json")).write_text(
                json.dumps(result), "utf-8"
            )

    def clock(self) -> bridge.Clock:
        return bridge.Clock(now=self.now, sleep=self.sleep)
