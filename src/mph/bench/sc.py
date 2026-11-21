"""SuperCollider in non-realtime mode (ADR-0002, REQ-AUD-02, REQ-AUD-05).

``synthdefs.scd`` is compiled by sclang once per change and cached under
``data_dir/synthdefs/<hash>/``. A render is an OSC score (``osc.py``) run by
``scsynth -N``: every hit is a timed ``/s_new``, chokes are ``/n_set gate 0``,
and each voice writes its own pair of output channels, so stems come out of
one deterministic pass. A melodic note is a ``/s_new`` with its pitch and
accent, released by ``/n_set gate 0``; a slide is an ``/n_set`` of the held
note's pitch, so nothing retriggers (BM-BASS-01).

A clip (spec/components/tracks.md, REQ-TRK-02) is a ``/s_new`` that plays
the voice's whole recording from the clip's first frame, for as long as the
clip plays, with a short fade at each end.

Voices play in one group and the sidechain (REQ-BEN-12) in a group after
it: every trigger hit starts an ``mph_duck`` envelope on a private bus, at
the hit's own sample, and an ``mph_ducker`` per target scales that target's
channels by it. A chord (REQ-BEN-18) is a synth for each of its notes,
started, slid and released together. A score with a mix routes its voices
through strips and sends (``scmix.py``), in two passes: stems, sidechain
targets and master first, then the master alone. Processes are spawned only
through ``mph.proc.Runner``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from mph.bench import scmix
from mph.bench.kit import Choice
from mph.bench.osc import Arg, Bundle, Message, encode_score
from mph.bench.scmix import (
    AUTO_NODE,
    DUCK_BUS,
    HEAD,
    SIDECHAIN_GROUP,
    TAIL,
    VOICES_GROUP,
    Layout,
)
from mph.bench.score import MAX_CHORD, Event, Score, Voice, clip_start_s
from mph.components import SC_APP
from mph.proc import Runner

MAC_SCSYNTH = f"{SC_APP}/Contents/Resources/scsynth"
MAC_SCLANG = f"{SC_APP}/Contents/MacOS/sclang"
FIRST_NODE = 1000
RANDOM_GENERATORS = 64  # scsynth default (-r); concurrent noisy hits stay well under it
SLICE_FADE_S = 0.001  # click-free slice edges (BM-DRUM-09)
CLIP_FADE_S = 0.002  # click-free clip cuts (REQ-TRK-02)
WIRE_BUFFERS = 1024  # the acid voice's additive saw needs more than the default 64
# What scsynth prints when a synth couldn't start: a note, or part of the mix,
# would be missing from the render. ("/n_set ... not found" is harmless: a
# release sent to a note that had already ended.)
NOT_STARTED = ("FAILURE IN SERVER /s_new", "alloc failed")
DONE = "compiled.ok"
CHORD_NODE = 10_000_000  # a chord's other notes: 10,000,000 + 8 x its first note's node + k


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Tools:
    scsynth: str
    sclang: str


def find_tools(runner: Runner) -> Tools | None:
    """scsynth and sclang from PATH, else from inside SuperCollider.app.

    Both lookups go through the runner (``which`` of an absolute path checks
    that file), so a fake runner never sees the real disk (REQ-ENG-03).
    """
    scsynth = runner.which("scsynth") or runner.which(MAC_SCSYNTH)
    sclang = runner.which("sclang") or runner.which(MAC_SCLANG)
    return Tools(scsynth, sclang) if scsynth and sclang else None


def synthdef_source() -> str:
    return resources.files("mph.bench").joinpath("synthdefs.scd").read_text("utf-8")


def compile_synthdefs(runner: Runner, tools: Tools, data_dir: Path) -> Path:
    """The folder of compiled .scsyndef files, compiling only when the source changed."""
    source = synthdef_source()
    folder = data_dir / "synthdefs" / hashlib.blake2b(source.encode(), digest_size=8).hexdigest()
    if (folder / DONE).is_file():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "synthdefs.scd"
    script.write_text(source, "utf-8")
    result = runner.run([tools.sclang, str(script), str(folder)])
    if not result.ok or not any(folder.glob("*.scsyndef")):
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise RenderError(f"sclang could not compile the synth voices: {detail}")
    (folder / DONE).write_text("ok\n", "utf-8")
    return folder


# --- the score ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Slice:
    start: int  # frame in the break file
    frames: int


@dataclass(frozen=True, slots=True)
class Plan:
    """Everything a render needs besides the events themselves."""

    score: Score
    choices: Sequence[Choice]
    rate: int
    synthdefs: Path
    slices: Sequence[Slice] = ()
    slice_rate: float = 1.0  # playback-rate factor that fits the break to the tempo
    break_rate: int = 0  # the break file's own sample rate
    gains: Sequence[float] = ()  # linear, one per voice (REQ-BEN-09); empty means 1
    clip_rates: Mapping[str, int] = field(default_factory=dict)  # clip voice -> recording rate
    master_only: bool = False  # a mix's second pass: the master alone (REQ-MIX-04)

    @property
    def layout(self) -> Layout:
        return Layout(self.score, self.master_only)

    @property
    def channels(self) -> int:
        return self.layout.channels


def _hit_args(plan: Plan, index: int, voice: Voice, event: Event, node: int) -> list[Arg]:
    choice = plan.choices[index]
    gain = plan.gains[index] if plan.gains else 1.0
    args: list[Arg] = ["out", plan.layout.out(index), "amp", event.velocity * gain]
    # A synth voice also takes the score's params for it, such as a djembe's stroke.
    params = choice.params if choice.is_sample else {**choice.params, **dict(voice.params)}
    for name, value in sorted(params.items()):
        args += [name, float(value)]
    if not choice.is_sample:
        return [*args, "seed", index + 1, "rid", node % RANDOM_GENERATORS]
    args += ["buf", index]
    if event.clip:
        clip = voice.clips[event.clip - 1]
        start = round(clip_start_s(plan.score, voice, clip) * plan.clip_rates[voice.name])
        args += ["start", start, "dur", event.length_s, "attack", CLIP_FADE_S]
        args += ["release", CLIP_FADE_S]
    elif event.slice:
        piece = plan.slices[event.slice - 1]
        # A slice stops where the next one starts, so slices never overlap.
        seconds = min(piece.frames / plan.break_rate / plan.slice_rate, 2 * plan.score.step_s)
        args += ["start", piece.start, "dur", seconds, "rate", plan.slice_rate]
        args += ["attack", SLICE_FADE_S, "release", SLICE_FADE_S]
    elif voice.seconds:
        args += ["dur", voice.seconds]
    return args


def hz(pitch: int) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def _note_args(plan: Plan, index: int, voice: Voice, event: Event, pitch: int) -> list[Arg]:
    gain = plan.gains[index] if plan.gains else 1.0
    args: list[Arg] = ["out", plan.layout.out(index), "amp", event.velocity * gain]
    for name, value in sorted({**plan.choices[index].params, **dict(voice.params)}.items()):
        args += [name, float(value)]
    return [*args, "freq", hz(pitch), "acc", int(event.accent)]


def chord_nodes(node: int, size: int) -> tuple[int, ...]:
    """The nodes of a chord's notes: the event's own, then one id per other note."""
    return (node, *(CHORD_NODE + node * MAX_CHORD + k for k in range(1, size)))


def _note(
    plan: Plan, index: int, event: Event, node: int, held: dict[str, tuple[int, ...]]
) -> tuple[list[Message], list[Bundle]]:
    """A melodic note, or a chord of them (REQ-BEN-18): new synths, or a glide
    on the held ones. Returns the messages for now and a release bundle for
    later, unless the note ties on."""
    voice = plan.score.voices[index]
    shape = voice.chord or (0,)
    if event.voice in held:  # slid into: no retrigger
        nodes = held.pop(event.voice)
        now = [
            Message("/n_set", (n, "freq", hz(event.pitch + step), "acc", int(event.accent)))
            for n, step in zip(nodes, shape, strict=True)
        ]
    else:
        name = plan.choices[index].synthdef
        nodes = chord_nodes(node, len(shape))
        now = [
            Message(
                "/s_new",
                (
                    name,
                    n,
                    TAIL,
                    VOICES_GROUP,
                    *_note_args(plan, index, voice, event, event.pitch + step),
                ),
            )
            for n, step in zip(nodes, shape, strict=True)
        ]
    if event.tie:
        held[event.voice] = nodes
        return now, []
    end = round((event.time_s + event.length_s) * plan.rate) / plan.rate
    return now, [Bundle(end, tuple(Message("/n_set", (n, "gate", 0)) for n in nodes))]


def reduction(depth_db: float) -> float:
    """How much of the level a dip of ``depth_db`` takes away (REQ-BEN-12)."""
    return 1.0 - 10 ** (-depth_db / 20)


def _duckers(plan: Plan, index: dict[str, int]) -> list[Message]:
    sidechain = plan.score.sidechain
    if sidechain is None or not sidechain.active:
        return []
    depth = reduction(sidechain.depth_db)
    return [
        Message(
            "/s_new",
            (
                "mph_ducker",
                AUTO_NODE,
                TAIL,
                SIDECHAIN_GROUP,
                *("out", plan.layout.out(index[target]), "bus", DUCK_BUS, "depth", depth),
            ),
        )
        for target in sidechain.targets
    ]


def _duck(plan: Plan, event: Event) -> list[Message]:
    """The dip a trigger hit starts, if this voice is the sidechain's trigger."""
    sidechain = plan.score.sidechain
    if sidechain is None or not sidechain.active or event.voice != sidechain.trigger:
        return []
    args: list[Arg] = ["bus", DUCK_BUS, "depth", reduction(sidechain.depth_db)]
    args += ["attack", sidechain.attack_ms / 1000, "hold", sidechain.hold_ms / 1000]
    args += ["release", sidechain.release_ms / 1000]
    return [Message("/s_new", ("mph_duck", AUTO_NODE, HEAD, SIDECHAIN_GROUP, *args))]


def _setup(plan: Plan) -> tuple[list[Message], list[Bundle]]:
    """The start of a render: synth definitions, sounds, groups, the sidechain's
    duckers and, in a mix, its synths and automation."""
    index = {voice.name: n for n, voice in enumerate(plan.score.voices)}
    setup = [Message("/d_loadDir", (str(plan.synthdefs),))]
    setup += [
        Message("/b_allocRead", (n, choice.path))
        for n, choice in enumerate(plan.choices)
        if choice.is_sample
    ]
    layout = plan.layout
    setup += [*scmix.groups(layout), *_duckers(plan, index)]
    if not layout.mixed:
        return setup, []
    scmix.check(plan.score)
    now, later = scmix.automation(layout, plan.rate)
    return [*setup, *scmix.synths(layout), *now], later


def build(plan: Plan, events: Sequence[Event]) -> list[Bundle]:
    """OSC bundles for one render. Times are whole samples, so hits land exactly."""
    index = {voice.name: n for n, voice in enumerate(plan.score.voices)}
    setup, moves = _setup(plan)
    bundles = [Bundle(0.0, tuple(setup)), *moves]
    latest: dict[str, int] = {}  # voice -> its most recent node, for chokes
    held: dict[str, tuple[int, ...]] = {}  # melodic voice -> the notes sliding into the next
    for node, event in enumerate(events, start=FIRST_NODE):
        n = index[event.voice]
        voice = plan.score.voices[n]
        at = event.sample(plan.rate) / plan.rate
        if event.pitch:
            now, later = _note(plan, n, event, node, held)
            bundles += [Bundle(at, tuple(now)), *later]
            continue
        messages = [
            Message("/n_set", (latest[target], "gate", 0))
            for target in voice.chokes
            if target in latest
        ]
        name = plan.choices[n].synthdef
        args = _hit_args(plan, n, voice, event, node)
        messages.append(Message("/s_new", (name, node, TAIL, VOICES_GROUP, *args)))
        messages += _duck(plan, event)
        bundles.append(Bundle(at, tuple(messages)))
        latest[event.voice] = node
    end = round(plan.score.length_s * plan.rate) / plan.rate
    bundles.append(Bundle(end, (Message("/c_set", (0, 0)),)))
    return bundles


def render(runner: Runner, tools: Tools, plan: Plan, events: Sequence[Event], out: Path) -> Path:
    """Run scsynth NRT; returns the multichannel 32-bit float WAV: two channels a
    voice, then in a mix two for each sidechain target and two for the master
    (or only the master's two, in a mix's second pass)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    score_file = out.with_suffix(".osc")
    score_file.write_bytes(encode_score(build(plan, events)))
    memory = scmix.memory_kb(plan.score, plan.rate)
    argv = [tools.scsynth, "-o", str(plan.channels), "-w", str(WIRE_BUFFERS), "-m", str(memory)]
    argv += ["-N", str(score_file), "_", str(out)]
    result = runner.run([*argv, str(plan.rate), "WAV", "float"])
    if not result.ok or not out.is_file():
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise RenderError(f"scsynth render failed: {detail}")
    said = f"{result.stdout}\n{result.stderr}".splitlines()
    missed = [line.strip() for line in said if any(s in line for s in NOT_STARTED)]
    if missed:
        raise RenderError(
            f"scsynth couldn't start every synth ({len(missed)} failed), so the render "
            f"would be missing sounds; the first said: {missed[0]}"
        )
    return out


def version(runner: Runner, tools: Tools) -> str:
    result = runner.run([tools.scsynth, "-v"])
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.ok else "unknown"
