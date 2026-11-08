"""SuperCollider in non-realtime mode (ADR-0002, REQ-AUD-02, REQ-AUD-05).

``synthdefs.scd`` is compiled by sclang once per change and cached under
``data_dir/synthdefs/<hash>/``. A render is an OSC score (``osc.py``) run by
``scsynth -N``: every hit is a timed ``/s_new``, chokes are ``/n_set gate 0``,
and each voice writes its own pair of output channels, so stems come out of
one deterministic pass. Processes are spawned only through ``mph.proc.Runner``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from mph.bench.kit import Choice
from mph.bench.osc import Arg, Bundle, Message, encode_score
from mph.bench.score import Event, Score, Voice
from mph.components import SC_APP
from mph.proc import Runner

MAC_SCSYNTH = f"{SC_APP}/Contents/Resources/scsynth"
MAC_SCLANG = f"{SC_APP}/Contents/MacOS/sclang"
FIRST_NODE = 1000
RANDOM_GENERATORS = 64  # scsynth default (-r); concurrent noisy hits stay well under it
SLICE_FADE_S = 0.001  # click-free slice edges (BM-DRUM-09)
DONE = "compiled.ok"


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Tools:
    scsynth: str
    sclang: str


def find_tools(runner: Runner) -> Tools | None:
    scsynth = runner.which("scsynth") or (MAC_SCSYNTH if Path(MAC_SCSYNTH).is_file() else None)
    sclang = runner.which("sclang") or (MAC_SCLANG if Path(MAC_SCLANG).is_file() else None)
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

    @property
    def channels(self) -> int:
        return 2 * len(self.choices)


def _hit_args(plan: Plan, index: int, voice: Voice, event: Event, node: int) -> list[Arg]:
    choice = plan.choices[index]
    args: list[Arg] = ["out", 2 * index, "amp", event.velocity]
    for name, value in sorted(choice.params.items()):
        args += [name, float(value)]
    if not choice.is_sample:
        return [*args, "seed", index + 1, "rid", node % RANDOM_GENERATORS]
    args += ["buf", index]
    if event.slice:
        piece = plan.slices[event.slice - 1]
        # A slice stops where the next one starts, so slices never overlap.
        seconds = min(piece.frames / plan.break_rate / plan.slice_rate, 2 * plan.score.step_s)
        args += ["start", piece.start, "dur", seconds, "rate", plan.slice_rate]
        args += ["attack", SLICE_FADE_S, "release", SLICE_FADE_S]
    elif voice.seconds:
        args += ["dur", voice.seconds]
    return args


def build(plan: Plan, events: Sequence[Event]) -> list[Bundle]:
    """OSC bundles for one render. Times are whole samples, so hits land exactly."""
    index = {voice.name: n for n, voice in enumerate(plan.score.voices)}
    setup = [Message("/d_loadDir", (str(plan.synthdefs),))]
    setup += [
        Message("/b_allocRead", (n, choice.path))
        for n, choice in enumerate(plan.choices)
        if choice.is_sample
    ]
    bundles = [Bundle(0.0, tuple(setup))]
    latest: dict[str, int] = {}  # voice -> its most recent node, for chokes
    for node, event in enumerate(events, start=FIRST_NODE):
        n = index[event.voice]
        voice = plan.score.voices[n]
        messages = [
            Message("/n_set", (latest[target], "gate", 0))
            for target in voice.chokes
            if target in latest
        ]
        name = plan.choices[n].synthdef
        messages.append(
            Message("/s_new", (name, node, 1, 0, *_hit_args(plan, n, voice, event, node)))
        )
        bundles.append(Bundle(event.sample(plan.rate) / plan.rate, tuple(messages)))
        latest[event.voice] = node
    end = round(plan.score.length_s * plan.rate) / plan.rate
    bundles.append(Bundle(end, (Message("/c_set", (0, 0)),)))
    return bundles


def render(runner: Runner, tools: Tools, plan: Plan, events: Sequence[Event], out: Path) -> Path:
    """Run scsynth NRT; returns the multichannel 32-bit float WAV (two channels a voice)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    score_file = out.with_suffix(".osc")
    score_file.write_bytes(encode_score(build(plan, events)))
    argv = [tools.scsynth, "-o", str(plan.channels), "-N", str(score_file), "_", str(out)]
    result = runner.run([*argv, str(plan.rate), "WAV", "float"])
    if not result.ok or not out.is_file():
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise RenderError(f"scsynth render failed: {detail}")
    return out


def version(runner: Runner, tools: Tools) -> str:
    result = runner.run([tools.scsynth, "-v"])
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.ok else "unknown"
