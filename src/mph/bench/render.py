"""Rendering one benchmark end to end (REQ-BEN-03, REQ-BEN-06, REQ-BEN-09, REQ-BEN-10).

score -> kit -> sounds (synth voices rendered once by SuperCollider) -> level
gains -> engine -> stems and master -> checks -> delivered AIFF -> ``report.json``.

The engine is Renoise when its bridge tool is installed (ADR-0009): mph
builds a small song with one track per voice, renders it, renders it again,
then renders each track alone for its stem. Without Renoise, SuperCollider
renders everything in one NRT pass, as in step 3. Either way, each voice is
also rendered alone as a *template* for measuring. Everything for one render
lives in ``data_dir/renders/<id>/<score hash>/``.
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np

from mph import __version__
from mph.bench import audio, checks, kit, levels, sc
from mph.bench.score import Event, Score, events, load
from mph.catalogue.scanner import utc_now
from mph.config import Config
from mph.deliver import deliver
from mph.proc import Runner
from mph.renoise import bridge
from mph.renoise import plan as song_plan

TEMPLATE_GAP_S = 2.0  # each template hit gets this long to itself
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


def template_events(score: Score, found: Sequence[Event]) -> list[Event]:
    """Each voice's distinct sounds (one, or one per break slice), alone, at full velocity."""
    out = []
    for voice in score.voices:
        kinds = sorted({e.slice for e in found if e.voice == voice.name})
        out += [Event(voice.name, 0, n * TEMPLATE_GAP_S, 1.0, k) for n, k in enumerate(kinds)]
    return sorted(out, key=lambda e: (e.time_s, e.voice))


def template_score(score: Score, templates: Sequence[Event]) -> Score:
    longest = max(e.time_s for e in templates) + TEMPLATE_GAP_S
    voices = tuple(replace(v, chokes=()) for v in score.voices)
    return replace(score, bars=0, tail_s=longest, voices=voices)


def spaced_templates(score: Score, found: Sequence[Event]) -> list[Event]:
    """Templates one after another on whole lines, so one Renoise render holds them all."""
    lines = math.ceil(TEMPLATE_GAP_S / score.step_s)
    out: list[Event] = []
    for voice in score.voices:
        for kind in sorted({e.slice for e in found if e.voice == voice.name}):
            slot = len(out)
            out.append(Event(voice.name, 0, slot * lines * score.step_s, 1.0, kind))
    return out


def _template(
    stems: dict[str, audio.Audio], templates: Sequence[Event], voice: str, kind: int, rate: int
) -> audio.Audio:
    hit = next(e for e in templates if e.voice == voice and e.slice == kind)
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
) -> list[checks.Check]:
    matches: dict[str, list[audio.Match]] = {}
    peaks: dict[str, float] = {}
    for voice in score.voices:
        own = [e for e in found if e.voice == voice.name]
        matches[voice.name] = []
        for kind in sorted({e.slice for e in own}):
            tpl = _template(reference, templates, voice.name, kind, rate)
            peaks[voice.name] = max(peaks.get(voice.name, 0.0), float(np.abs(tpl).max()))
            group = [e for e in own if e.slice == kind]
            matches[voice.name] += audio.match_hits(stems[voice.name], tpl, group, rate)
    found_checks = [checks.timing(matches), checks.velocity(matches)]
    found_checks += checks.swing(score, matches)
    found_checks += checks.chokes(score, found, stems, peaks, rate)
    found_checks += checks.humanise(score, matches)
    for voice in score.voices:
        if voice.slices:
            edges = slice_edges(score, [e for e in found if e.voice == voice.name])
            found_checks.append(checks.edges(voice.name, stems[voice.name], edges, rate))
    return found_checks


# --- engines --------------------------------------------------------------------------


def pick_engine(requested: str, home: Path) -> str:
    if requested not in ENGINES:
        raise sc.RenderError(f"engine must be one of {', '.join(ENGINES)}")
    if requested == "supercollider":
        return requested
    state = bridge.status(home)
    if requested == "renoise" and state != "ready":
        raise sc.RenderError(
            "Renoise can't render yet: run `mph renoise install`, then restart Renoise"
        )
    return "renoise" if state == "ready" else "supercollider"


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
    return Rendered(
        master=audio.mixdown(stems),
        again=audio.mixdown(audio.split(again, names)),
        stems=stems,
        reference=audio.split(audio.read(ref_file)[0], names),
        templates=templates,
        tools={"scsynth": sc.version(runner, tools)},
    )


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
    renders: list[Any] = [{"file": str(work / "render.wav")}, {"file": str(work / "again.wav")}]
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
        renders=[{"file": str(work / "templates.wav")}],
        seconds=solo_score.length_s,
    )
    return {"songs": [main, reference]}, templates


def _render_renoise(
    cfg: Config, runner: Runner, home: Path, setup: Setup, clock: bridge.Clock | None
) -> Rendered:
    score, work = setup.score, setup.folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    job, templates = renoise_job(setup)
    name = f"{score.id}-{setup.folder.name}"
    outcome = bridge.run_job(cfg, runner, home, name, job, timeout_s=RENOISE_TIMEOUT_S, clock=clock)
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
    hits = [Event(name, 0, 0.0, 1.0) for name in synths]
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
) -> Outcome:
    home = Path.home() if home is None else home
    chosen = pick_engine(engine, home)
    score = load(benchmark_id)
    choices = kit.choose(score, conn, cfg.data_dir)
    setup, tools, gains = _prepare(cfg, runner, score, choices, chosen)
    folder, rate = setup.folder, setup.rate
    if chosen == "renoise":
        rendered = _render_renoise(cfg, runner, home, setup, clock)
    else:
        assert tools is not None  # noqa: S101 - _prepare refuses otherwise
        rendered = _render_supercollider(runner, tools, setup)
    for name, stem in rendered.stems.items():
        audio.write_float(folder / "stems" / f"{name}.wav", stem, rate)
    audio.write_float(folder / "master.wav", rendered.master, rate)
    found_checks = measure(
        score,
        setup.found,
        stems=rendered.stems,
        reference=rendered.reference,
        templates=rendered.templates,
        rate=rate,
    )
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
        "score_hash": folder.name,
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
    return Outcome(folder, report)
