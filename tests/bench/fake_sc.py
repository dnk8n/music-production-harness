"""A stand-in for scsynth and sclang, so render tests never run SuperCollider.

The fake reads the real OSC score mph writes, and "plays" every /s_new as a
short decaying sine on the right channels at the right sample, honouring
amp, dur and choke (/n_set gate 0). A melodic note (an /s_new with ``freq``)
is a harmonic tone at that pitch, held until its /n_set gate 0; an /n_set of
``freq`` changes its pitch there (a slide), and an accent makes it louder
and brighter. An ``mph_duck`` starts a dip on its bus at its sample, and an
``mph_ducker`` scales its channels by the dips, as the real synths do
(REQ-BEN-12). A clip (a buffer played from a ``start`` frame for ``dur``
seconds at its own rate, REQ-TRK-02) plays the real recording, resampled to
the render's rate. That's enough for the matching, swing, choke, accent,
slide, sidechain, clip and determinism checks to mean something.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile
import soxr

from mph.bench import audio, osc
from mph.proc import Result

HIT_S = 0.08
CHOKE_RELEASE_S = 0.005
NOTE_HARMONICS = 6  # 12 when accented: brighter
ACCENT_GAIN = 1.6
Started = list[tuple[int, dict[str, float]]]  # (sample, args) of each mph_duck or mph_ducker


@dataclass
class _Note:
    start: int
    channel: int
    amp: float
    accent: bool
    pitches: list[tuple[int, float]]  # (from sample, Hz)
    end: int | None = None

    def play(self, data: np.ndarray, rate: int) -> None:
        end = len(data) if self.end is None else min(self.end, len(data))
        hz = np.zeros(end - self.start)
        for (at, freq), until in zip(
            self.pitches, [*[a for a, _ in self.pitches[1:]], end], strict=True
        ):
            hz[at - self.start : until - self.start] = freq
        phase = 2 * np.pi * np.cumsum(hz) / rate  # continuous through a slide
        count = NOTE_HARMONICS * (2 if self.accent else 1)
        tone = np.sum([np.sin(n * phase) / n for n in range(1, count + 1)], axis=0)
        gain = self.amp * (ACCENT_GAIN if self.accent else 1.0) * 0.3
        wave = (gain * tone).astype(np.float32)
        data[self.start : end, self.channel] += wave
        data[self.start : end, self.channel + 1] += wave


@dataclass
class _Playing:
    """What the score has started so far."""

    nodes: dict[int, tuple[int, int, int]] = field(default_factory=dict)  # (start, end, channel)
    buffers: dict[int, str] = field(default_factory=dict)  # the file each buffer was read from
    notes: dict[int, _Note] = field(default_factory=dict)
    ducks: Started = field(default_factory=list)
    duckers: Started = field(default_factory=list)


def _pairs(pairs: Sequence[object]) -> dict[str, float]:
    return {str(pairs[i]): float(pairs[i + 1]) for i in range(0, len(pairs), 2)}  # type: ignore[arg-type]


def _args(message: osc.Message) -> dict[str, float]:
    return _pairs(message.args[4:])


def _hit(name: str, args: dict[str, float], rate: int) -> np.ndarray:
    key = f"{name}/{args.get('buf', 0)}/{args.get('start', 0)}/{args.get('decay', 0)}"
    freq = 300 + zlib.crc32(key.encode()) % 3000
    seconds = args.get("dur") or HIT_S
    t = np.arange(round(min(seconds, HIT_S) * rate)) / rate
    return (args.get("amp", 1.0) * np.sin(2 * np.pi * freq * t) * np.exp(-t * 40)).astype(
        np.float32
    )


def _clip(path: str, args: dict[str, float], rate: int) -> np.ndarray:
    """A buffer played from its ``start`` frame for ``dur`` seconds, at its own speed."""
    data, native = soundfile.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    first = int(args["start"])
    piece = data[first : first + round(args["dur"] * native)]
    moved = soxr.resample(piece, native, rate, quality="VHQ") if native != rate else piece
    return (np.asarray(moved) * args.get("amp", 1.0)).astype(np.float32)


def _note_message(message: osc.Message, at: int, rate: int, notes: dict[int, _Note]) -> bool:
    """Handle a message about a melodic note; False if it's about something else."""
    if message.address == "/s_new" and "freq" in _args(message):
        args = _args(message)
        notes[int(message.args[1])] = _Note(
            at, int(args["out"]), args["amp"], bool(args["acc"]), [(at, args["freq"])]
        )
        return True
    node = int(message.args[0]) if message.address == "/n_set" else 0
    if node not in notes:
        return False
    change = _pairs(message.args[1:])
    if "freq" in change:
        notes[node].pitches.append((at, change["freq"]))
    if change.get("gate") == 0:
        notes[node].end = at + round(CHOKE_RELEASE_S * rate)
    return True


def _dips(length: int, rate: int, ducks: Started) -> np.ndarray:
    """How much level the mph_duck envelopes take away, summed: a linear
    attack to the depth, held there, then a half-cosine release back to 0."""
    taken = np.zeros(length)
    for at, args in ducks:
        attack = round(args["attack"] * rate)
        release = round(args["release"] * rate)
        rise = args["depth"] * np.arange(1, attack + 1) / attack
        held = np.full(round(args["hold"] * rate), args["depth"])
        fall = args["depth"] * (0.5 + 0.5 * np.cos(np.pi * np.arange(1, release + 1) / release))
        shape = np.concatenate([rise, held, fall])[: max(length - at, 0)]
        taken[at : at + len(shape)] += shape
    return taken


def _sidechain(message: osc.Message, at: int, ducks: Started, duckers: Started) -> bool:
    if message.address != "/s_new" or message.args[0] not in ("mph_duck", "mph_ducker"):
        return False
    (ducks if message.args[0] == "mph_duck" else duckers).append((at, _args(message)))
    return True


def _hit_message(
    message: osc.Message, at: int, rate: int, data: np.ndarray, playing: _Playing
) -> None:
    """A drum hit or a clip starts (/s_new), or a hit is choked (/n_set gate 0).
    A clip is a buffer played from a start frame at its own speed: a break's
    slice sets a playback rate too."""
    if message.address == "/s_new":
        args = _args(message)
        buffer = playing.buffers.get(int(args.get("buf", -1)), "")
        if buffer and "start" in args and "rate" not in args:
            wave = _clip(buffer, args, rate)
        else:
            wave = np.repeat(_hit(str(message.args[0]), args, rate)[:, None], 2, axis=1)
        wave = wave[: max(len(data) - at, 0)]
        channel = int(args["out"])
        data[at : at + len(wave), channel : channel + 2] += wave
        playing.nodes[int(message.args[1])] = (at, at + len(wave), channel)
    elif message.address == "/n_set":
        start, end, channel = playing.nodes[int(message.args[0])]
        cut = max(at + round(CHOKE_RELEASE_S * rate), start)
        if cut < end:
            data[cut:end, channel : channel + 2] = 0.0


def _play(message: osc.Message, at: int, rate: int, data: np.ndarray, playing: _Playing) -> None:
    if message.address == "/b_allocRead":
        playing.buffers[int(message.args[0])] = str(message.args[1])
    elif not _sidechain(message, at, playing.ducks, playing.duckers) and not _note_message(
        message, at, rate, playing.notes
    ):
        _hit_message(message, at, rate, data, playing)


def fake_scsynth(argv: Sequence[str]) -> Result:
    if "-v" in argv:
        return Result(0, stdout="scsynth 3.13.0 (fake)\n")
    channels = int(argv[argv.index("-o") + 1])
    score_file = Path(argv[argv.index("-N") + 1])
    out, rate = Path(argv[argv.index("-N") + 3]), int(argv[argv.index("-N") + 4])
    bundles = osc.decode_score(score_file.read_bytes())
    length = round(bundles[-1].time_s * rate)
    data = np.zeros((length, channels), dtype=np.float32)
    playing = _Playing()
    for bundle in bundles:
        at = round(bundle.time_s * rate)
        for message in bundle.messages:
            _play(message, at, rate, data, playing)
    for note in playing.notes.values():
        note.play(data, rate)
    taken = _dips(length, rate, playing.ducks)
    for _, args in playing.duckers:
        channel = int(args["out"])
        gain = (1 - np.clip(taken, 0, args["depth"])).astype(np.float32)
        data[:, channel : channel + 2] *= gain[:, None]
    audio.write_float(out, data, rate)
    return Result(0)


def fake_sclang(argv: Sequence[str]) -> Result:
    folder = Path(argv[2])
    (folder / "mph_kick.scsyndef").write_bytes(b"SCgf")
    return Result(0, stdout="mph: synthdefs written\n")
