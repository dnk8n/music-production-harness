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

A mix (spec/components/mix.md) runs on private buses past the output
channels, in the real graph's order: taps of the sidechain's targets, the
duckers, then the strips, sends and master as they were created. A strip
applies its level, pan and sends, read from its control buses where
``/n_map`` maps them (``/c_set``, ``mph_line`` and ``mph_xline`` move the
buses), but not its filters, which only the real synths have. The delay
repeats its input every ``time`` seconds, ``feedback`` times as loud each
time, and the hall is a train of faint echoes 50 ms apart that dies away
over ``decay`` seconds.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile
import soxr

from mph.bench import audio, mix, osc, scmix
from mph.proc import Result

HIT_S = 0.08
CHOKE_RELEASE_S = 0.005
NOTE_HARMONICS = 6  # 12 when accented: brighter
ACCENT_GAIN = 1.6
HALL_GAP_S = 0.05  # the fake hall's echoes
HALL_LEVEL = 0.3
FADED = 1e-4  # an echo this quiet ends the train
Started = list[tuple[int, dict[str, float]]]  # (sample, args) of each mph_duck or mph_ducker
MIXING = ("mph_tap", "mph_strip", *scmix.EFFECTS.values())


class _Buses:
    """The render's output channels, and the private pairs past them."""

    def __init__(self, data: np.ndarray) -> None:
        self.data = data
        self.private: dict[int, np.ndarray] = {}

    def pair(self, bus: int) -> np.ndarray:
        """Two channels from ``bus``: a view of the output, or a private pair."""
        if bus + 2 <= self.data.shape[1]:
            return self.data[:, bus : bus + 2]
        if bus not in self.private:
            self.private[bus] = np.zeros((len(self.data), 2), dtype=np.float32)
        return self.private[bus]


@dataclass
class _Note:
    start: int
    channel: int
    amp: float
    accent: bool
    pitches: list[tuple[int, float]]  # (from sample, Hz)
    end: int | None = None

    def play(self, buses: _Buses, rate: int) -> None:
        data = buses.pair(self.channel)
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
        data[self.start : end] += wave[:, None]


@dataclass
class _Synth:
    """One of the mix's synths: a tap, a strip or an effect."""

    name: str
    node: int
    group: int
    args: dict[str, float]


@dataclass
class _Playing:
    """What the score has started so far."""

    nodes: dict[int, tuple[int, int, int]] = field(default_factory=dict)  # (start, end, channel)
    buffers: dict[int, str] = field(default_factory=dict)  # the file each buffer was read from
    notes: dict[int, _Note] = field(default_factory=dict)
    ducks: Started = field(default_factory=list)
    duckers: Started = field(default_factory=list)
    mix: list[_Synth] = field(default_factory=list)
    moves: list[tuple[int, str, dict[str, float]]] = field(default_factory=list)  # control buses
    mapped: dict[int, dict[str, int]] = field(default_factory=dict)  # node -> control -> bus


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


def _mix_message(message: osc.Message, at: int, playing: _Playing) -> bool:
    """A mix synth, an automation move, or a control mapped to a bus; False if
    the message is about something else."""
    name = str(message.args[0]) if message.args else ""
    if message.address == "/s_new" and name in MIXING:
        node, group = int(message.args[1]), int(message.args[3])
        playing.mix.append(_Synth(name, node, group, _args(message)))
    elif message.address == "/s_new" and name in ("mph_line", "mph_xline"):
        playing.moves.append((at, name, _args(message)))
    elif message.address == "/c_set":
        bus, value = int(message.args[0]), float(message.args[1])
        playing.moves.append((at, "set", {"bus": bus, "to": value}))
    elif message.address == "/n_map":
        node, control, bus = int(message.args[0]), str(message.args[1]), int(message.args[2])
        playing.mapped.setdefault(node, {})[control] = bus
    else:
        return False
    return True


def _hit_message(
    message: osc.Message, at: int, rate: int, buses: _Buses, playing: _Playing
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
        channel = int(args["out"])
        data = buses.pair(channel)
        wave = wave[: max(len(data) - at, 0)]
        data[at : at + len(wave)] += wave
        playing.nodes[int(message.args[1])] = (at, at + len(wave), channel)
    elif message.address == "/n_set":
        start, end, channel = playing.nodes[int(message.args[0])]
        cut = max(at + round(CHOKE_RELEASE_S * rate), start)
        if cut < end:
            buses.pair(channel)[cut:end] = 0.0


def _play(message: osc.Message, at: int, rate: int, buses: _Buses, playing: _Playing) -> None:
    if message.address == "/b_allocRead":
        playing.buffers[int(message.args[0])] = str(message.args[1])
    elif (
        not _sidechain(message, at, playing.ducks, playing.duckers)
        and not _mix_message(message, at, playing)
        and not _note_message(message, at, rate, playing.notes)
    ):
        _hit_message(message, at, rate, buses, playing)


# --- the mix ---------------------------------------------------------------------------


def _controls(playing: _Playing, length: int, rate: int) -> dict[int, np.ndarray]:
    """Each control bus over time: set, or moved in a line (dB) or evenly in
    octaves, holding each value until the next move."""
    found: dict[int, np.ndarray] = {}
    for at, kind, args in playing.moves:
        bus = int(args["bus"])
        if bus == 0 and kind == "set":
            continue  # the score's end marker
        values = found.setdefault(bus, np.zeros(length))
        if kind == "set":
            values[at:] = args["to"]
            continue
        frames = max(round(args["dur"] * rate), 1)
        share = np.arange(frames) / frames
        low, high = args["from"], args["to"]
        line = low * (high / low) ** share if kind == "mph_xline" else low + (high - low) * share
        values[at : at + frames] = line[: max(length - at, 0)]
        values[at + frames :] = high
    return found


def _gain(db: np.ndarray) -> np.ndarray:
    """Linear gain from dB, with -90 dB or lower as silence (REQ-MIX-03)."""
    return np.where(db > -90, 10 ** (np.maximum(db, -90) / 20), 0.0)


@dataclass
class _Controls:
    """The control buses over time, and which synth control reads which bus."""

    buses: dict[int, np.ndarray]
    mapped: dict[int, dict[str, int]]
    length: int

    def of(self, synth: _Synth, name: str, default: float) -> np.ndarray:
        bus = self.mapped.get(synth.node, {}).get(name)
        if bus is not None and bus in self.buses:
            return self.buses[bus]
        return np.full(self.length, synth.args.get(name, default))


def _strip(synth: _Synth, buses: _Buses, controls: _Controls) -> None:
    """Level, balance and sends; the filters are left to the real synth."""
    pan = synth.args.get("pan", 0.0)
    level = _gain(controls.of(synth, "level", 0.0))
    sides = np.stack([level * min(1.0, 1 - pan), level * min(1.0, 1 + pan)], axis=1)
    out = buses.pair(int(synth.args["in"])) * sides
    buses.pair(int(synth.args["out"]))[:] += out
    for k in range(1, mix.MAX_SENDS + 1):
        if f"bus{k}" in synth.args:
            sent = _gain(controls.of(synth, f"send{k}", -90.0))
            buses.pair(int(synth.args[f"bus{k}"]))[:] += out * sent[:, None]


def _later(signal: np.ndarray, frames: int) -> np.ndarray:
    moved = np.zeros_like(signal)
    if frames < len(signal):
        moved[frames:] = signal[: len(signal) - frames]
    return moved


def _delay(synth: _Synth, buses: _Buses, rate: int) -> None:
    source = buses.pair(int(synth.args["in"]))
    out = buses.pair(int(synth.args["out"]))
    gap = max(round(synth.args["time"] * rate), 1)
    echo = source.copy()
    if synth.args.get("pingpong"):
        echo = np.stack([source.mean(axis=1), np.zeros(len(source))], axis=1).astype(np.float32)
    gain, count = 1.0, 1
    while count * gap < len(source) and gain > FADED:
        out += _later(echo, count * gap) * gain
        gain *= synth.args.get("feedback", 0.4)
        if synth.args.get("pingpong"):
            echo = echo[:, ::-1]
        count += 1


def _reverb(synth: _Synth, buses: _Buses, rate: int) -> None:
    source = buses.pair(int(synth.args["in"]))
    out = buses.pair(int(synth.args["out"]))
    first = round(synth.args.get("predelay", 0.02) * rate)
    gap = round(HALL_GAP_S * rate)
    fall = 10 ** (-3 * HALL_GAP_S / synth.args.get("decay", 3.0))
    gain, count, echo = HALL_LEVEL * fall, 1, source
    while first + count * gap < len(source) and gain > FADED:
        out += _later(echo, first + count * gap) * gain
        gain *= fall
        echo = echo[:, ::-1]
        count += 1


def _mixed(playing: _Playing, buses: _Buses, rate: int) -> None:
    """The strips, sends and master, as they were created (after the duckers)."""
    length = len(buses.data)
    controls = _Controls(_controls(playing, length, rate), playing.mapped, length)
    for synth in playing.mix:
        if synth.group == scmix.BYPASS_GROUP:
            continue  # tapped before the dip
        if synth.name == "mph_tap":
            buses.pair(int(synth.args["out"]))[:] += buses.pair(int(synth.args["in"]))
        elif synth.name == "mph_strip":
            _strip(synth, buses, controls)
        elif synth.name == "mph_delay":
            _delay(synth, buses, rate)
        else:
            _reverb(synth, buses, rate)


def fake_scsynth(argv: Sequence[str]) -> Result:
    if "-v" in argv:
        return Result(0, stdout="scsynth 3.13.0 (fake)\n")
    channels = int(argv[argv.index("-o") + 1])
    score_file = Path(argv[argv.index("-N") + 1])
    out, rate = Path(argv[argv.index("-N") + 3]), int(argv[argv.index("-N") + 4])
    bundles = osc.decode_score(score_file.read_bytes())
    length = round(bundles[-1].time_s * rate)
    buses = _Buses(np.zeros((length, channels), dtype=np.float32))
    playing = _Playing()
    for bundle in bundles:
        at = round(bundle.time_s * rate)
        for message in bundle.messages:
            _play(message, at, rate, buses, playing)
    for note in playing.notes.values():
        note.play(buses, rate)
    for tap in (s for s in playing.mix if s.group == scmix.BYPASS_GROUP):
        buses.pair(int(tap.args["out"]))[:] += buses.pair(int(tap.args["in"]))
    taken = _dips(length, rate, playing.ducks)
    for _, args in playing.duckers:
        gain = (1 - np.clip(taken, 0, args["depth"])).astype(np.float32)
        buses.pair(int(args["out"]))[:] *= gain[:, None]
    _mixed(playing, buses, rate)
    audio.write_float(out, buses.data, rate)
    return Result(0)


def fake_sclang(argv: Sequence[str]) -> Result:
    folder = Path(argv[2])
    (folder / "mph_kick.scsyndef").write_bytes(b"SCgf")
    return Result(0, stdout="mph: synthdefs written\n")
