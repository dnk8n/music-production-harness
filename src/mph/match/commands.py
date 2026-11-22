"""`mph match ...`: describe a match's reference, check its search on a known
answer, and run it (spec/components/matching.md, REQ-MAT-01, REQ-MAT-05,
REQ-MAT-06).

Audio libraries and SuperCollider's helpers load on first use, so the rest of
mph starts as quickly as before.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mph import checkout
from mph.config import Config
from mph.proc import Runner

if TYPE_CHECKING:
    import numpy as np

    from mph.match.fit import Fit, Span
    from mph.match.matchfile import Match
    from mph.match.search import Check, Found

KEPT = 3  # candidates offered for listening (REQ-MAT-06)
EVERY = 10  # generations between progress lines
CHECK_WORK = "check-work"


def dispatch(args: argparse.Namespace, cfg: Config, runner: Runner) -> int:
    from mph.bench.sc import RenderError  # noqa: PLC0415 - heavy imports, on first use
    from mph.match import matchfile  # noqa: PLC0415

    try:
        match = matchfile.load(args.file)
        if args.action == "analyse":
            return cmd_analyse(cfg, match)
        if args.action == "check":
            return cmd_check(cfg, runner, match, runs=args.workers)
        return cmd_run(cfg, runner, match, runs=args.workers)
    except (matchfile.MatchError, RenderError) as exc:
        print(exc, file=sys.stderr)
        return 2


# --- analyse ---------------------------------------------------------------------------


def cmd_analyse(cfg: Config, match: Match) -> int:
    """The reference's span described, to help write the match (REQ-MAT-01)."""
    from mph.match import describe, fit, views  # noqa: PLC0415

    span = fit.reference_span(cfg, match)
    mono = views.mono(span.audio)
    ref = match.reference
    print(f"{ref.what or span.source}: {ref.start_s:g} to {ref.end_s:g} s of it, at {span.rate} Hz")
    print("    from   level  pitch")
    for quarter in describe.quarters(mono, span.rate):
        hz = quarter.pitch_hz
        pitch = f"{hz:7.1f} Hz  {describe.note_name(hz)}" if hz else "      -"
        print(f"  {quarter.at_s:5.2f} s  {quarter.level_db:4.0f} dB  {pitch}")
    print("Its strongest partials where it is loudest:")
    for partial in describe.partials(mono, span.rate):
        name = describe.note_name(partial.hz)
        print(f"  {partial.hz:8.1f} Hz  {name:<9}  {partial.db:5.1f} dB")
    return 0


# --- check -----------------------------------------------------------------------------


def cmd_check(cfg: Config, runner: Runner, match: Match, *, runs: int = 0) -> int:
    """Hide a patch, render it as the reference, and search for it (REQ-MAT-05)."""
    from mph.match import fit, search  # noqa: PLC0415
    from mph.match import runs as kept  # noqa: PLC0415

    rate = fit.reference_rate(cfg, match)
    if rate is None:
        rate = cfg.audio.production_sample_rate
        print(f"The reference isn't on this computer, so the check renders at {rate} Hz.")
    hidden = search.hidden_patch(len(match.knobs), match.seed)
    work = kept.folder_of(cfg, match.project, match.name) / CHECK_WORK
    try:
        batch = fit.batch(cfg, runner, match, rate, work=work, runs=runs)
        reference = batch.render([match.patch(hidden.tolist())])[0]
        found, seconds = _search(fit.Fit.to(match, batch, reference), fit.baseline(match))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    check = search.Check(hidden, found)
    _print_check(match, check, seconds, batch.workers)
    return 0 if check.passed else 1


def _search(fitting: Fit, base: Mapping[str, float]) -> tuple[Found, float]:
    """The search, with a line of progress every few generations."""
    from mph.match import fit, search  # noqa: PLC0415

    match = fitting.match
    began = time.monotonic()

    def progress(found: Found) -> None:
        if found.generations % EVERY == 0:
            minutes = (time.monotonic() - began) / 60
            print(f"  generation {found.generations}: closest {found.total:.3f} after "
                  f"{found.renders:,} renders, {minutes:.1f} min", flush=True)  # fmt: skip

    print(f"Searching {len(match.knobs)} knobs of {match.synth}, up to {match.budget:,} renders.")
    found = search.search(
        len(match.knobs),
        fitting.measure,
        budget=match.budget,
        seed=match.seed,
        weights=dict(match.weights),
        start=fit.start(match, dict(base)),
        each_generation=progress,
    )
    return found, time.monotonic() - began


def _print_check(match: Match, check: Check, seconds: float, runs: int) -> None:
    found = check.found
    print(f"Known-answer check of {match.name}: {'passed' if check.passed else 'NOT passed'}.")
    print(f"  closest {found.total:.3f}, against a first generation's median of "
          f"{found.first_median:.3f} (it passes under {0.1 * found.first_median:.3f})")  # fmt: skip
    print(f"  {_effort(found, seconds, runs)}")
    print("  How far off each knob is, as a share of its range:")
    faint = check.barely_heard()
    for k, (knob, error) in enumerate(zip(match.knobs, check.errors, strict=True)):
        print(f"    {knob.name:<10} {error:6.1%}{'  barely heard' if k in faint else ''}")
    if faint:
        names = [match.knobs[k].name for k in faint]
        them = "it" if len(names) == 1 else "them"
        print(f"  This sound barely shows {_listed(names)}: pinning {them} is guesswork.")


def _effort(found: Found, seconds: float, runs: int) -> str:
    return (f"{found.renders:,} renders over {found.generations} generations in "
            f"{seconds / 60:.1f} min, {runs} scsynth runs at once")  # fmt: skip


def _listed(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


# --- run -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Result:
    """A run's outcome: the baseline and the candidates worth hearing."""

    base: dict[str, float]
    base_sound: np.ndarray
    base_views: dict[str, float]
    base_total: float
    picks: list[tuple[dict[str, float], float, np.ndarray]]  # knobs, total, sound

    @property
    def better(self) -> bool:
        return bool(self.picks)


def cmd_run(cfg: Config, runner: Runner, match: Match, *, runs: int = 0) -> int:
    """Search against the reference and leave what Dean hears (REQ-MAT-06)."""
    from mph.match import fit  # noqa: PLC0415
    from mph.match import runs as kept  # noqa: PLC0415

    span = fit.reference_span(cfg, match)
    base = fit.baseline(match)
    started = datetime.now(UTC)
    folder = kept.new_run(cfg, match.project, match.name, started)
    try:
        batch = fit.batch(cfg, runner, match, span.rate, work=folder / "work", runs=runs)
        fitting = fit.Fit.to(match, batch, span.audio)
        found, seconds = _search(fitting, base)
        result = _result(fitting, found, base)
    finally:
        shutil.rmtree(folder / "work", ignore_errors=True)
    _write(folder, fitting, span, result)
    report = _report(match, span, found, result, runner)
    report |= {
        "seconds": round(seconds, 1),
        "workers": batch.workers,
        "started": started.isoformat(),
    }
    kept.write_report(folder, report)
    kept.prune(cfg, match.project, match.name, folder)
    _print_run(match, found, result, folder, _effort(found, seconds, batch.workers))
    return 0 if result.better else 1


def _result(fitting: Fit, found: Found, base: dict[str, float]) -> Result:
    """The baseline measured as the search measured candidates, and the
    closest candidates, at least search.APART from each other, that beat it."""
    from mph.match import views  # noqa: PLC0415

    match = fitting.match
    picks = found.apart(KEPT)
    patches = [match.patch(point.tolist()) for point, _ in picks]
    sounds = fitting.batch.render([base, *patches])
    base_views = views.distances(fitting.reference, fitting.look(sounds[0]))
    base_total = views.total(base_views, found.scales, dict(match.weights))
    better = [
        (_knobs(match, point), total, sound)
        for (point, total), sound in zip(picks, sounds[1:], strict=True)
        if total < base_total
    ]
    return Result(base, sounds[0], base_views, base_total, better)


def _knobs(match: Match, point: np.ndarray) -> dict[str, float]:
    return {k.name: _round(k.value(float(u))) for k, u in zip(match.knobs, point, strict=True)}


def _round(value: float) -> float:
    """Four significant figures: finer than anyone hears a knob turn."""
    return float(f"{value:.4g}")


def _write(folder: Path, fitting: Fit, span: Span, result: Result) -> None:
    from mph.bench import audio  # noqa: PLC0415

    audio.write_float(folder / "reference.wav", span.audio, span.rate)
    audio.write_float(folder / "baseline.wav", _leveled(result.base_sound, fitting.rms), span.rate)
    for n, (_, _, sound) in enumerate(result.picks, start=1):
        audio.write_float(folder / f"candidate-{n}.wav", _leveled(sound, fitting.rms), span.rate)


def _leveled(sound: np.ndarray, rms: float) -> np.ndarray:
    """A sound at the reference's level, measured in mono as the views are."""
    from mph.match import views  # noqa: PLC0415

    mono = views.mono(sound)
    own = float((mono**2).mean() ** 0.5) if mono.size else 0.0
    return sound * (rms / own) if own > 0 else sound


def pinned(match: Match, base: Mapping[str, float], knobs: Mapping[str, float]) -> dict[str, float]:
    """The voice's params with a candidate pinned: its knobs, less those the
    score keeps (REQ-MAT-07)."""
    return {**base, **{name: value for name, value in knobs.items() if name not in match.keep}}


def _report(
    match: Match, span: Span, found: Found, result: Result, runner: Runner
) -> dict[str, Any]:
    ref = match.reference
    best = result.picks[0][0] if result.picks else {}
    return {
        "match": match.name,
        "project": match.project,
        "file": str(match.file),
        "synth": match.synth,
        "score": match.score,
        "voice": match.voice,
        "reference": {
            "hash": ref.hash,
            "path": ref.path,
            "start_s": ref.start_s,
            "end_s": ref.end_s,
            "what": ref.what,
            "licence": ref.licence,
            "rate": span.rate,
        },
        "play": {
            "notes_hz": list(match.play.notes),
            "at_s": match.play.at_s,
            "length_s": match.play.length_s,
        },
        "knobs": [
            {"name": k.name, "low": k.low, "high": k.high, "log": k.log} for k in match.knobs
        ],
        "params": dict(match.params),
        "keep": list(match.keep),
        "weights": dict(match.weights),
        "budget": match.budget,
        "seed": match.seed,
        "renders": found.renders,
        "generations": found.generations,
        "first_median": found.first_median,
        "scales": found.scales,
        "progress": [round(total, 4) for total in found.progress],
        "baseline": {
            "params": result.base,
            "views": result.base_views,
            "total": result.base_total,
            "file": "baseline.wav",
        },
        "found": {"knobs": _knobs(match, found.best), "views": found.views, "total": found.total},
        "candidates": [
            {"file": f"candidate-{n}.wav", "knobs": knobs, "total": total}
            for n, (knobs, total, _) in enumerate(result.picks, start=1)
        ],
        "pin": pinned(match, result.base, best) if best else {},
        "checkout": checkout.state(runner),
    }


def _print_run(match: Match, found: Found, result: Result, folder: Path, effort: str) -> None:
    print(f"Fitted {match.synth} to {match.reference.what or match.name}: {effort}.")
    if not result.better:
        print(f"  No candidate came closer than the baseline ({result.base_total:.3f}; the "
              f"closest was {found.total:.3f}), so there is nothing to pin.")  # fmt: skip
        return
    knobs, total, _ = result.picks[0]
    closer = 1 - total / result.base_total
    print(f"  The closest is {total:.3f}, {closer:.0%} closer than the baseline's "
          f"{result.base_total:.3f}.")  # fmt: skip
    where = f"[voices.{match.voice}] in {match.score}" if match.score else "the voice"
    print(f"To pin it (REQ-MAT-07), set the params of {where} to:")
    shown = ", ".join(f"{k} = {v:g}" for k, v in pinned(match, result.base, knobs).items())
    print(f"  params = {{ {shown} }}")
    print(f"with a comment naming {match.file.name}, run {folder.name}, and its distance "
          f"{total:.3f} against the baseline's {result.base_total:.3f}.")  # fmt: skip
    heard = ", ".join(f"candidate-{n}.wav" for n in range(1, len(result.picks) + 1))
    print(f"Listen in {folder}: reference.wav, baseline.wav, {heard}.")
