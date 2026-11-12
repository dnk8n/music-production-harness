"""Rendering one benchmark end to end (REQ-BEN-03, REQ-BEN-06, REQ-BEN-09, REQ-BEN-10).

score -> kit -> sounds (synth voices rendered once by SuperCollider) -> level
gains -> engine -> stems and master -> checks -> delivered AIFF -> ``report.json``.

The engine is Renoise when its bridge tool is installed (ADR-0009): mph
builds a small song with one track per voice, renders it, renders it again,
then renders each track alone for its stem. Without Renoise, SuperCollider
renders everything in one NRT pass, as in step 3. A score with a melodic
voice (BM-BASS-01) always renders in SuperCollider, because its glides and
per-note filter envelopes can't be one-shot samples yet. Either way, each voice is
also rendered alone as a *template* for measuring. Everything for one render
lives in ``data_dir/renders/<id>/<score hash>/``.
"""

from __future__ import annotations

import itertools
import json
import math
import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from mph import __version__, retention
from mph.bench import audio, checks, feedback, kit, levels, rerender, sc
from mph.bench.score import (
    Event,
    Score,
    Variant,
    Voice,
    all_variants,
    events,
    load,
    midi,
    variant_of,
)
from mph.catalogue.scanner import utc_now
from mph.config import Config
from mph.deliver import deliver
from mph.proc import Runner
from mph.renoise import bridge
from mph.renoise import plan as song_plan

TEMPLATE_GAP_S = 2.0  # each template hit gets this long to itself
NOTE_PART = 0.5  # of a step: how much of each note the accent check compares
PROBE_PITCH = 72  # C5: high enough that aliasing would show
PROBE_S = 1.0
PROBE_TAIL_S = 0.2
GLIDE_FROM_S = 0.3  # the slide starts here
GLIDE_HOLD_S = 0.6  # and the note slid into is held this long
GLIDE_MEASURE_S = 0.15  # its last part, once the glide and filter have settled
GLIDE_SEARCH = 2**0.5  # look for the pitch within half an octave of the target
# A pad plays its root alone, undetuned, so the energy between harmonics is aliasing.
PROBE_PARAMS = {
    "cutoff": 8000.0,
    "resonance": 0.9,
    "envmod": 0.0,
    "accent": 0.0,
    "chord": 0.0,
    "detune": 0.0,
}
DELIVERY_CEILING_DBTP = -1.0  # benchmarks aren't mastered: keep them clear of clipping
RENDERS = "renders"
ONESHOT_S = 2.0  # room for a synth voice's one-shot
ONESHOT_FLOOR = 10 ** (-100 / 20)  # trailing silence below this is trimmed
ENGINES = ("auto", "renoise", "supercollider")
RENOISE_TIMEOUT_S = 600.0
RENOISE_NULL_DB = -120.0  # REQ-AUD-05: Renoise renders null-test, not bit-identical


@dataclass(frozen=True, slots=True)
class Outcome:
    folder: Path
    report: dict[str, object]

    @property
    def passed(self) -> bool:
        return bool(self.report["passed"])


@dataclass(slots=True)
class Rendered:
    """What an engine hands back for measuring and delivery."""

    master: audio.Audio
    again: audio.Audio
    stems: dict[str, audio.Audio]
    reference: dict[str, audio.Audio]  # per voice: audio holding its templates
    templates: list[Event]
    tools: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # extra files for the report
    extra_checks: list[checks.Check] = field(default_factory=list)  # engine-specific
    dry: dict[str, audio.Audio] = field(default_factory=dict)  # sidechain targets, unducked


@dataclass(frozen=True, slots=True)
class Setup:
    """Everything shared by both engines for one render."""

    score: Score
    choices: Sequence[kit.Choice]
    rate: int
    folder: Path
    found: Sequence[Event]
    plan: sc.Plan  # also used by Renoise for the break's slices and rate
    sounds: dict[str, Path]  # voice -> sound file (sample in place, or synth one-shot)


def renders_dir(cfg: Config) -> Path:
    return cfg.data_dir / RENDERS


# --- breaks ---------------------------------------------------------------------------


def _slicing(score: Score, choices: Sequence[kit.Choice]) -> tuple[list[sc.Slice], float, int]:
    for voice, choice in zip(score.voices, choices, strict=True):
        if voice.slices:
            signal, rate = audio.read(Path(choice.path))
            bpm = audio.fit_tempo(choice.bpm, score.bpm)
            points = audio.slice_points(audio.mono(signal), rate, bpm)
            return [sc.Slice(s, n) for s, n in points], score.bpm / bpm, rate
    return [], 1.0, 0


def slice_edges(score: Score, found: Sequence[Event]) -> list[float]:
    return [e.time_s for e in found if e.slice and e.step]


# --- templates ------------------------------------------------------------------------


def _slid_into(own: Sequence[Event], event: Event) -> bool:
    """A note the previous one ties into: it glides in, with no onset of its own."""
    before = [e for e in own if e.time_s < event.time_s]
    return bool(before) and before[-1].tie


def _onsets(own: Sequence[Event]) -> list[Event]:
    return [e for e in own if not (e.pitch and _slid_into(own, e))]


def _kinds(own: Sequence[Event]) -> list[Event]:
    """One event per distinct sound of a voice: one, one per break slice, or one
    per pitch and accent. Every accented note also gets an unaccented twin, so
    the accent can be measured against the same note."""
    seen: dict[tuple[int, int, bool], Event] = {}
    for event in _onsets(own):
        seen.setdefault(event.kind, replace(event, tie=False))
    for event in list(seen.values()):
        if event.accent:
            plain = replace(event, accent=False)
            seen.setdefault(plain.kind, plain)
    return [seen[k] for k in sorted(seen)]


def template_events(score: Score, found: Sequence[Event]) -> list[Event]:
    """Each voice's distinct sounds, alone, at full velocity, one per gap."""
    out = []
    for voice in score.voices:
        kinds = _kinds([e for e in found if e.voice == voice.name])
        out += [
            replace(e, step=0, time_s=n * TEMPLATE_GAP_S, velocity=1.0) for n, e in enumerate(kinds)
        ]
    return sorted(out, key=lambda e: (e.time_s, e.voice))


def template_score(score: Score, templates: Sequence[Event]) -> Score:
    longest = max(e.time_s for e in templates) + TEMPLATE_GAP_S
    voices = tuple(replace(v, chokes=()) for v in score.voices)
    # Templates sit side by side, so a sidechain would duck one with another's hit.
    return replace(score, bars=0, tail_s=longest, voices=voices, sidechain=None)


def spaced_templates(score: Score, found: Sequence[Event]) -> list[Event]:
    """Templates one after another on whole lines, so one Renoise render holds them all."""
    lines = math.ceil(TEMPLATE_GAP_S / score.step_s)
    out: list[Event] = []
    for voice in score.voices:
        for kind in _kinds([e for e in found if e.voice == voice.name]):
            slot = len(out)
            out.append(replace(kind, step=0, time_s=slot * lines * score.step_s, velocity=1.0))
    return out


def _template(
    stems: dict[str, audio.Audio],
    templates: Sequence[Event],
    voice: str,
    kind: tuple[int, int, bool],
    rate: int,
) -> audio.Audio:
    hit = next(e for e in templates if e.voice == voice and e.kind == kind)
    start = hit.sample(rate)
    return stems[voice][start : start + round(TEMPLATE_GAP_S * rate)]


# --- measuring ------------------------------------------------------------------------


def measure(
    score: Score,
    found: Sequence[Event],
    *,
    stems: dict[str, audio.Audio],
    reference: dict[str, audio.Audio],
    templates: Sequence[Event],
    rate: int,
    as_scored: bool = True,
) -> list[checks.Check]:
    matches: dict[str, list[audio.Match]] = {}
    peaks: dict[str, float] = {}
    for voice in score.voices:
        onsets = _onsets([e for e in found if e.voice == voice.name])
        matches[voice.name] = []
        for kind in sorted({e.kind for e in onsets}):
            tpl = _template(reference, templates, voice.name, kind, rate)
            peaks[voice.name] = max(peaks.get(voice.name, 0.0), float(np.abs(tpl).max()))
            group = [e for e in onsets if e.kind == kind]
            matches[voice.name] += audio.match_hits(stems[voice.name], tpl, group, rate)
    found_checks = [checks.timing(matches), checks.velocity(matches)]
    found_checks += checks.swing(score, matches)
    found_checks += checks.feel(score, matches)
    found_checks += checks.chokes(score, found, stems, peaks, rate)
    found_checks += checks.humanise(score, matches)
    for voice in (v for v in score.voices if v.melodic):
        own = [e for e in found if e.voice == voice.name]
        pairs = [
            (
                _template(reference, templates, voice.name, t.kind, rate),
                _template(reference, templates, voice.name, replace(t, accent=False).kind, rate),
            )
            for t in templates
            if t.voice == voice.name and t.accent
        ]
        # Declared variants push controls to the ends of their range, where an accent
        # needn't get brighter (the filter is already open), so only the controls as
        # scored are held to it.
        found_checks += checks.accents(
            voice.name,
            pairs,
            rate,
            round(score.step_s * NOTE_PART * rate),
            brighter=as_scored,
        )
        slid = [e for e in own if _slid_into(own, e)]
        found_checks += checks.slides(voice.name, stems[voice.name], slid, rate)
    for voice in score.voices:
        if voice.slices:
            edges = slice_edges(score, [e for e in found if e.voice == voice.name])
            found_checks.append(checks.edges(voice.name, stems[voice.name], edges, rate))
    return found_checks


# --- engines --------------------------------------------------------------------------


def pick_engine(requested: str, home: Path, *, only: str = "") -> str:
    """The engine to render in. ``only`` says why the score needs SuperCollider,
    if it does (``Score.supercollider_only``)."""
    if requested not in ENGINES:
        raise sc.RenderError(f"engine must be one of {', '.join(ENGINES)}")
    if only:
        if requested == "renoise":
            raise sc.RenderError(
                f"this benchmark has {only}, which only SuperCollider can play so far"
            )
        return "supercollider"
    if requested == "supercollider":
        return requested
    state = bridge.status(home)
    if requested == "renoise" and state != "ready":
        raise sc.RenderError(
            "Renoise can't render yet: run `mph renoise install`, then restart Renoise"
        )
    return "renoise" if state == "ready" else "supercollider"


def can_render(variant: Variant, home: Path, *, only: str = "") -> str:
    """Why this take can't render here, or "" when it can (REQ-BEN-07)."""
    if variant.engine != "renoise":
        return ""  # SuperCollider, or the engine REQ-BEN-10 picks, which falls back to it
    if only:
        return f"{only} renders only in SuperCollider so far"
    if bridge.status(home) != "ready":
        return "Renoise isn't set up here (run `mph renoise install`)"
    return ""


def take(benchmark_id: str, variant: str, engine: str, home: Path) -> tuple[Score, Variant, str]:
    """A take's score, its variant, and the engine it renders in."""
    loaded = load(benchmark_id)
    score, chosen_variant = variant_of(loaded, variant or first_take(loaded, home, engine).name)
    chosen = pick_engine(chosen_variant.engine or engine, home, only=score.supercollider_only)
    return score, chosen_variant, chosen


def take_inputs(cfg: Config, score: Score, engine: str) -> str:
    """What shapes this take, as its report records it (REQ-BEN-14)."""
    return rerender.inputs(score, engine, asdict(cfg.audio))


def first_take(score: Score, home: Path, engine: str = "auto") -> Variant:
    """The first take that can render here, in the engine asked for if one was."""
    takes = [
        v for v in all_variants(score) if not can_render(v, home, only=score.supercollider_only)
    ]
    if engine != "auto":
        takes = [v for v in takes if v.engine == engine] or [v for v in takes if not v.engine]
    return next(iter(takes), all_variants(score)[0])


def _fit(data: audio.Audio, frames: int) -> audio.Audio:
    """Trim or zero-pad to an exact length, so renders line up sample for sample."""
    if len(data) >= frames:
        return data[:frames]
    return np.vstack([data, np.zeros((frames - len(data), data.shape[1]), dtype=data.dtype)])


def _render_supercollider(runner: Runner, tools: sc.Tools, setup: Setup) -> Rendered:
    score, plan, found, work = setup.score, setup.plan, setup.found, setup.folder / "work"
    rendered, _ = audio.read(sc.render(runner, tools, plan, found, work / "render.wav"))
    again, _ = audio.read(sc.render(runner, tools, plan, found, work / "again.wav"))
    templates = template_events(score, found)
    ref_plan = replace(plan, score=template_score(score, templates))
    ref_file = sc.render(runner, tools, ref_plan, templates, work / "templates.wav")
    names = [v.name for v in score.voices]
    stems = audio.split(rendered, names)
    dry, ducking = _sidechain(runner, tools, setup, stems)
    return Rendered(
        master=audio.mixdown(stems),
        again=audio.mixdown(audio.split(again, names)),
        stems=stems,
        reference=audio.split(audio.read(ref_file)[0], names),
        templates=templates,
        tools={"scsynth": sc.version(runner, tools)},
        extra_checks=probe(runner, tools, setup.plan, work) + ducking,
        dry=dry,
    )


def _sidechain(
    runner: Runner, tools: sc.Tools, setup: Setup, stems: dict[str, audio.Audio]
) -> tuple[dict[str, audio.Audio], list[checks.Check]]:
    """REQ-BEN-12: the targets again with the sidechain bypassed, and the dip
    measured as the ducked targets against those."""
    score, plan = setup.score, setup.plan
    chain = score.sidechain
    if chain is None or not chain.active:
        return {}, []
    bypassed = replace(plan, score=replace(score, sidechain=replace(chain, depth_db=0.0)))
    out = setup.folder / "work" / "dry.wav"
    rendered = audio.split(
        audio.read(sc.render(runner, tools, bypassed, setup.found, out))[0],
        [v.name for v in score.voices],
    )
    dry = {name: rendered[name] for name in chain.targets}
    hits = [e.sample(plan.rate) / plan.rate for e in setup.found if e.voice == chain.trigger]
    found = []
    for target in chain.targets:
        found += checks.sidechain(chain, target, stems[target], dry[target], hits, rate=plan.rate)
    # The whole mix, ducked and as bypass would have it, weighted as loudness is.
    ducked = audio.k_weighted(audio.mixdown(stems), plan.rate)
    bypass = audio.k_weighted(audio.mixdown({**stems, **dry}), plan.rate)
    under, after = (
        audio.window_changes(ducked, bypass, hits, rate=plan.rate, start_s=lo, end_s=hi)
        for lo, hi in (checks.MIX_UNDER_KICK_S, checks.MIX_AFTER_KICK_S)
    )
    return dry, found + checks.sidechain_mix(under, after, len(hits))


def _probe_render(
    runner: Runner, tools: sc.Tools, plan: sc.Plan, voice: Voice, *, notes: list[Event], out: Path
) -> audio.Audio:
    """Some notes from one voice, alone, with that voice's controls set as given."""
    voices = tuple(voice if v.name == voice.name else v for v in plan.score.voices)
    length = max(e.time_s + e.length_s for e in notes) + PROBE_TAIL_S
    score = replace(plan.score, voices=voices, bars=0, tail_s=length)
    data, _ = audio.read(sc.render(runner, tools, replace(plan, score=score), notes, out))
    return audio.split(data, [v.name for v in voices])[voice.name]


def probe(runner: Runner, tools: sc.Tools, plan: sc.Plan, work: Path) -> list[checks.Check]:
    """Each melodic voice alone, in two short renders: a high, open, resonant
    note, measured for energy between its harmonics (aliasing, REQ-AUD-04);
    and the score's first slide, with the note slid into held until it
    settles, measured for pitch."""
    found = []
    for voice in (v for v in plan.score.voices if v.melodic):
        opened = replace(
            voice, params=tuple(sorted({**dict(voice.params), **PROBE_PARAMS}.items()))
        )
        note = Event(voice.name, 0, 0.0, 1.0, pitch=PROBE_PITCH, length_s=PROBE_S)
        stem = _probe_render(
            runner, tools, plan, opened, notes=[note], out=work / f"probe-{voice.name}.wav"
        )
        level = audio.inharmonic_db(stem, plan.rate, sc.hz(PROBE_PITCH), (0.2, PROBE_S - 0.05))
        found.append(checks.aliasing(voice.name, level))
        own = [e for e in events(plan.score) if e.voice == voice.name]
        pairs = [(a, b) for a, b in itertools.pairwise(own) if a.tie]
        if not pairs:
            continue
        first, second = pairs[0]
        notes = [
            replace(first, step=0, time_s=0.0, velocity=1.0),
            replace(second, step=1, time_s=GLIDE_FROM_S, velocity=1.0, length_s=GLIDE_HOLD_S),
        ]
        stem = _probe_render(
            runner, tools, plan, voice, notes=notes, out=work / f"glide-{voice.name}.wav"
        )
        end = GLIDE_FROM_S + GLIDE_HOLD_S
        settled = stem[round((end - GLIDE_MEASURE_S) * plan.rate) : round(end * plan.rate)]
        target = sc.hz(second.pitch)
        heard = audio.pitch_hz(settled, plan.rate, target / GLIDE_SEARCH, target * GLIDE_SEARCH)
        found.append(
            checks.glide(voice.name, abs(1200 * math.log2(heard / target)) if heard else math.inf)
        )
    return found


def renoise_tracks(setup: Setup) -> list[song_plan.Track]:
    plan = setup.plan
    tracks = []
    for n, (voice, choice) in enumerate(zip(setup.score.voices, setup.choices, strict=True)):
        semitones, starts = 0.0, tuple[int, ...]()
        if voice.slices:
            semitones = 12 * math.log2(plan.slice_rate)
            starts = tuple(s.start for s in plan.slices)
        tracks.append(
            song_plan.Track(
                name=voice.name,
                sample=str(setup.sounds[choice.voice]),
                gain=plan.gains[n] if plan.gains else 1.0,
                semitones=semitones,
                slices=starts,
            )
        )
    return tracks


def renoise_job(setup: Setup) -> tuple[dict[str, Any], list[Event]]:
    score, folder, work = setup.score, setup.folder, setup.folder / "work"
    tracks = renoise_tracks(setup)
    # The first render of a freshly built song starts ~30 samples late, so warm up first.
    warm_up: dict[str, Any] = {"file": str(work / "warm-up.wav")}
    renders: list[Any] = [
        warm_up,
        {"file": str(work / "render.wav")},
        {"file": str(work / "again.wav")},
    ]
    renders += [
        {"file": str(work / f"stem-{v.name}.wav"), "solo": n}
        for n, v in enumerate(score.voices, start=1)
    ]
    main = song_plan.song(
        score,
        setup.found,
        tracks,
        rate=setup.rate,
        save=str(folder / f"{score.id}.xrns"),
        renders=renders,
    )
    templates = spaced_templates(score, setup.found)
    solo_score = template_score(score, templates)
    reference = song_plan.song(
        solo_score,
        templates,
        tracks,
        rate=setup.rate,
        save=str(work / "templates.xrns"),
        renders=[warm_up, {"file": str(work / "templates.wav")}],
        seconds=solo_score.length_s,
    )
    return {"songs": [main, reference]}, templates


def _render_renoise(
    cfg: Config,
    runner: Runner,
    home: Path,
    setup: Setup,
    *,
    clock: bridge.Clock | None,
    session: bridge.Session | None,
) -> Rendered:
    score, work = setup.score, setup.folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    job, templates = renoise_job(setup)
    name = f"{score.id}-{setup.folder.name}"
    outcome = bridge.run_job(
        cfg, runner, home, name, job, timeout_s=RENOISE_TIMEOUT_S, clock=clock, session=session
    )
    frames = round(score.length_s * setup.rate)

    def take(path: Path, length: int) -> audio.Audio:
        data, rate = audio.read(path)
        if rate != setup.rate:
            raise sc.RenderError(f"Renoise rendered {path.name} at {rate} Hz, not {setup.rate}")
        return _fit(data, length)

    solo_frames = round(template_score(score, templates).length_s * setup.rate)
    reference = take(work / "templates.wav", solo_frames)
    return Rendered(
        master=take(work / "render.wav", frames),
        again=take(work / "again.wav", frames),
        stems={v.name: take(work / f"stem-{v.name}.wav", frames) for v in score.voices},
        reference={v.name: reference for v in score.voices},
        templates=templates,
        tools={"renoise": str(outcome.get("renoise", "unknown"))},
        files={"project": f"{score.id}.xrns"},
    )


# --- sounds and levels ----------------------------------------------------------------


def _trim(data: audio.Audio, rate: int) -> audio.Audio:
    loud = np.nonzero(np.abs(data).max(axis=1) > ONESHOT_FLOOR)[0]
    end = int(loud[-1]) + 1 if loud.size else 0
    return data[: max(end, round(0.01 * rate))]


def synth_oneshots(runner: Runner, tools: sc.Tools, plan: sc.Plan, folder: Path) -> dict[str, Path]:
    """Each synth voice rendered once, alone, at full velocity and unit gain."""
    score = plan.score
    synths = [c.voice for c in plan.choices if not c.is_sample]
    if not synths:
        return {}
    melodic = {v.name: v for v in score.voices if v.melodic}
    first = {e.voice: e for e in reversed(events(score))}  # each voice's first note
    hits = [
        Event(
            name,
            0,
            0.0,
            1.0,
            pitch=midi(melodic[name].notes[0]),
            # at least a step; a held pad note whole, so its level is measured settled
            length_s=max(score.step_s, first[name].length_s if name in first else 0.0),
        )
        if name in melodic
        else Event(name, 0, 0.0, 1.0)
        for name in synths
    ]
    solo = replace(plan, score=replace(template_score(score, hits), tail_s=ONESHOT_S), gains=())
    rendered, _ = audio.read(sc.render(runner, tools, solo, hits, folder / "work" / "sounds.wav"))
    stems = audio.split(rendered, [v.name for v in score.voices])
    out = {}
    for name in synths:
        path = folder / "sounds" / f"{name}.wav"
        audio.write_float(path, _trim(stems[name], plan.rate), plan.rate)
        out[name] = path
    return out


def level_gains(score: Score, sounds: dict[str, Path]) -> dict[str, float]:
    gains = {}
    for voice in score.voices:
        data, rate = audio.read(sounds[voice.name])
        gains[voice.name] = levels.gain_db(voice, levels.hit_loudness(data, rate))
    return gains


# --- the whole render -----------------------------------------------------------------


def _ceiling_gain(master: audio.Audio, rate: int) -> float:
    peak = audio.levels(master, rate)["true_peak_dbtp"]
    return min(0.0, DELIVERY_CEILING_DBTP - peak)


def _prepare(
    cfg: Config, runner: Runner, score: Score, choices: list[kit.Choice], engine: str
) -> tuple[Setup, sc.Tools | None, dict[str, float]]:
    tools = sc.find_tools(runner)
    needs_sc = engine == "supercollider" or any(not c.is_sample for c in choices)
    if needs_sc and tools is None:
        raise sc.RenderError("SuperCollider isn't installed: run `mph install supercollider`")
    rate = cfg.audio.production_sample_rate
    slices, slice_rate, break_rate = _slicing(score, choices)
    synthdefs = sc.compile_synthdefs(runner, tools, cfg.data_dir) if tools else Path()
    plan = sc.Plan(score, choices, rate, synthdefs, slices, slice_rate, break_rate)
    staging = renders_dir(cfg) / score.id / "staging"
    oneshots = synth_oneshots(runner, tools, plan, staging) if tools else {}
    sounds = {c.voice: Path(c.path) if c.is_sample else oneshots[c.voice] for c in choices}
    gains = level_gains(score, sounds)
    digest = score.digest(
        {
            "kit": [c.fingerprint() for c in choices],
            "rate": rate,
            "synthdefs": synthdefs.name if needs_sc else "",
            "slices": [(s.start, s.frames) for s in slices],
            "levels": gains,
            "engine": engine,
        }
    )
    folder = renders_dir(cfg) / score.id / digest
    for name, path in list(sounds.items()):
        if path.is_relative_to(staging):
            target = folder / path.relative_to(staging)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
            sounds[name] = target
    shutil.rmtree(staging, ignore_errors=True)
    plan = replace(plan, gains=tuple(levels.linear(gains[v.name]) for v in score.voices))
    return Setup(score, choices, rate, folder, events(score), plan, sounds), tools, gains


def run(
    benchmark_id: str,
    cfg: Config,
    runner: Runner,
    conn: sqlite3.Connection | None,
    *,
    engine: str = "auto",
    home: Path | None = None,
    clock: bridge.Clock | None = None,
    variant: str = "",
    session: bridge.Session | None = None,
) -> Outcome:
    """Render one take; with no variant named, the benchmark's first (REQ-BEN-07).
    A take that passed keeps its sounds (REQ-BEN-15)."""
    home = Path.home() if home is None else home
    score, chosen_variant, chosen = take(benchmark_id, variant, engine, home)
    made, verdict = feedback.standing(renders_dir(cfg), conn, score.id, chosen_variant.name)
    choices = kit.choose(score, conn, cfg.data_dir, keep=rerender.kept_sounds(made, verdict))
    setup, tools, gains = _prepare(cfg, runner, score, choices, chosen)
    folder, rate = setup.folder, setup.rate
    if chosen == "renoise":
        rendered = _render_renoise(cfg, runner, home, setup, clock=clock, session=session)
    else:
        assert tools is not None  # noqa: S101 - _prepare refuses otherwise
        rendered = _render_supercollider(runner, tools, setup)
    for name, stem in rendered.stems.items():
        audio.write_float(folder / "stems" / f"{name}.wav", stem, rate)
    audio.write_float(folder / "master.wav", rendered.master, rate)
    found_checks = measure(
        score,
        setup.found,
        stems={**rendered.stems, **rendered.dry},  # note timing, before any ducking
        reference=rendered.reference,
        templates=rendered.templates,
        rate=rate,
        as_scored=chosen_variant.as_scored,
    )
    found_checks += rendered.extra_checks
    null_limit = RENOISE_NULL_DB if chosen == "renoise" else None
    found_checks.append(
        checks.determinism(rendered.master, rendered.again, null_limit_db=null_limit)
    )
    measured = audio.levels(rendered.master, rate)
    found_checks.append(checks.lead_in(measured))
    gain_db = _ceiling_gain(rendered.master, rate)
    a = cfg.audio
    delivered = deliver(
        rendered.master * (10 ** (gain_db / 20)),
        rate,
        folder / f"{score.id}.{a.delivery_format}",
        to_rate=a.delivery_sample_rate,
        bits=a.delivery_bit_depth,
        fmt=a.delivery_format,
    )
    report = {
        "id": score.id,
        "title": score.title,
        "variant": score.variant,
        "variant_title": chosen_variant.title,
        "score_hash": folder.name,
        "inputs": take_inputs(cfg, score, chosen),
        "rendered_at": utc_now(),
        "engine": chosen,
        "tools": {"mph": __version__, **rendered.tools},
        "production_rate": rate,
        "bpm": score.bpm,
        "bars": score.bars,
        "swing": score.swing,
        "kit": [
            {
                "voice": c.voice,
                "sound": c.label,
                "hash": c.digest,
                "licence": c.licence,
                "why": c.reason,
                "request": kit.request_of(score.voice(c.voice)),
                "gain_db": gains[c.voice],
            }
            for c in choices
        ],
        "measurements": [c.to_dict() for c in found_checks],
        "passed": all(c.passed for c in found_checks),
        "levels": measured,
        "delivery_gain_db": round(gain_db, 2),
        "licence_marker": kit.licence_marker(choices),
        "files": {
            "master": "master.wav",
            "stems": [f"stems/{v.name}.wav" for v in score.voices],
            "delivery": delivered.name,
            **rendered.files,
        },
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    retention.after_render(cfg.data_dir, folder, cfg.retention)  # REQ-RET-01, REQ-RET-03
    return Outcome(folder, report)
