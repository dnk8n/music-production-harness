"""Rendering one benchmark end to end (REQ-BEN-03, REQ-BEN-06).

score -> kit -> SuperCollider NRT (the render, plus each voice alone as a
template) -> stems and master -> checks -> delivered AIFF -> ``report.json``.
Everything for one render lives in ``data_dir/renders/<id>/<score hash>/``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from mph import __version__
from mph.bench import audio, checks, kit, sc
from mph.bench.score import Event, Score, events, load
from mph.catalogue.scanner import utc_now
from mph.config import Config
from mph.deliver import deliver
from mph.proc import Runner

TEMPLATE_GAP_S = 2.0  # each template hit gets this long to itself
DELIVERY_CEILING_DBTP = -1.0  # benchmarks aren't mastered: keep them clear of clipping
RENDERS = "renders"


@dataclass(frozen=True, slots=True)
class Outcome:
    folder: Path
    report: dict[str, object]

    @property
    def passed(self) -> bool:
        return bool(self.report["passed"])


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


# --- the whole render -----------------------------------------------------------------


def _ceiling_gain(master: audio.Audio, rate: int) -> float:
    peak = audio.levels(master, rate)["true_peak_dbtp"]
    return min(0.0, DELIVERY_CEILING_DBTP - peak)


def run(benchmark_id: str, cfg: Config, runner: Runner, conn: sqlite3.Connection | None) -> Outcome:
    tools = sc.find_tools(runner)
    if tools is None:
        raise sc.RenderError("SuperCollider isn't installed: run `mph install supercollider`")
    score = load(benchmark_id)
    choices = kit.choose(score, conn, cfg.data_dir)
    rate = cfg.audio.production_sample_rate
    slices, slice_rate, break_rate = _slicing(score, choices)
    synthdefs = sc.compile_synthdefs(runner, tools, cfg.data_dir)
    digest = score.digest(
        {
            "kit": [c.fingerprint() for c in choices],
            "rate": rate,
            "synthdefs": synthdefs.name,
            "slices": [(s.start, s.frames) for s in slices],
        }
    )
    folder = renders_dir(cfg) / score.id / digest
    plan = sc.Plan(score, choices, rate, synthdefs, slices, slice_rate, break_rate)
    found = events(score)
    rendered, _ = audio.read(sc.render(runner, tools, plan, found, folder / "work" / "render.wav"))
    again, _ = audio.read(sc.render(runner, tools, plan, found, folder / "work" / "again.wav"))
    templates = template_events(score, found)
    ref_plan = replace(plan, score=template_score(score, templates))
    ref_file = sc.render(runner, tools, ref_plan, templates, folder / "work" / "templates.wav")
    names = [v.name for v in score.voices]
    stems = audio.split(rendered, names)
    reference = audio.split(audio.read(ref_file)[0], names)
    for name, stem in stems.items():
        audio.write_float(folder / "stems" / f"{name}.wav", stem, rate)
    master = audio.mixdown(stems)
    audio.write_float(folder / "master.wav", master, rate)
    found_checks = measure(
        score, found, stems=stems, reference=reference, templates=templates, rate=rate
    )
    found_checks.append(checks.determinism(rendered, again))
    levels = audio.levels(master, rate)
    found_checks.append(checks.lead_in(levels))
    gain_db = _ceiling_gain(master, rate)
    a = cfg.audio
    delivered = deliver(
        master * (10 ** (gain_db / 20)),
        rate,
        folder / f"{score.id}.{a.delivery_format}",
        to_rate=a.delivery_sample_rate,
        bits=a.delivery_bit_depth,
        fmt=a.delivery_format,
    )
    report = {
        "id": score.id,
        "title": score.title,
        "score_hash": digest,
        "rendered_at": utc_now(),
        "tools": {"mph": __version__, "scsynth": sc.version(runner, tools)},
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
            }
            for c in choices
        ],
        "measurements": [c.to_dict() for c in found_checks],
        "passed": all(c.passed for c in found_checks),
        "levels": levels,
        "delivery_gain_db": round(gain_db, 2),
        "licence_marker": kit.licence_marker(choices),
        "files": {
            "master": "master.wav",
            "stems": [f"stems/{n}.wav" for n in names],
            "delivery": delivered.name,
        },
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return Outcome(folder, report)
