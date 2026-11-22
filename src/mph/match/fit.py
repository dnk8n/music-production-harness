"""A match made ready to fit: its reference, its baseline and its candidates
measured (spec/components/matching.md, REQ-MAT-02, REQ-MAT-03, REQ-MAT-06)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mph.bench import audio, sc
from mph.bench import score as scores
from mph.catalogue import db, provenance
from mph.config import Config
from mph.match import views
from mph.match.candidates import Batch, Patch
from mph.match.matchfile import Match, MatchError, synth_controls
from mph.proc import Runner


@dataclass(frozen=True, slots=True)
class Span:
    """The part of the reference a match fits, and where it was read from."""

    audio: np.ndarray  # frames x channels
    rate: int
    source: str  # the reference's hash or path, as the match file names it


def reference_file(cfg: Config, match: Match) -> Path:
    """Where the reference's bytes are on this computer."""
    where = match.file.name
    if match.reference.path:
        path = match.file.parent / match.reference.path
        if not path.is_file():
            raise MatchError(f"{where}: the reference {path} isn't there")
        return path
    catalogue = cfg.data_dir / db.FILENAME
    if not catalogue.is_file():
        raise MatchError(f"{where}: the reference is a catalogue sound, and {catalogue} is missing")
    with closing(db.connect(catalogue)) as conn:
        try:
            digest = provenance.resolve(conn, match.reference.hash)
        except provenance.ProvenanceError as exc:
            raise MatchError(f"{where}: [reference] {exc}") from exc
        found = provenance.locate(conn, cfg.data_dir, digest)
    if found is None:
        raise MatchError(
            f"{where}: the reference {digest[:12]} is in the catalogue but not on this "
            "computer; `mph catalogue refresh` makes a derived sound again"
        )
    return found


def reference_span(cfg: Config, match: Match) -> Span:
    """The reference's span, read exactly as stored, at its own rate (REQ-AUD-01)."""
    path = reference_file(cfg, match)
    rate, frames = audio.info(path)
    first, count = round(match.reference.start_s * rate), round(match.reference.length_s * rate)
    if first + count > frames:
        raise MatchError(
            f"{match.file.name}: the reference is {frames / rate:.2f} s long, so it has no "
            f"span to {match.reference.end_s:g} s"
        )
    data = audio.read_frames(path, first, count)
    if not np.any(data):
        raise MatchError(f"{match.file.name}: the reference is silent from "
                         f"{match.reference.start_s:g} to {match.reference.end_s:g} s")  # fmt: skip
    return Span(data, rate, match.reference.hash or match.reference.path)


def reference_rate(cfg: Config, match: Match) -> int | None:
    """The reference's sample rate, or None when it isn't on this computer."""
    try:
        return audio.info(reference_file(cfg, match))[0]
    except MatchError:
        return None


def baseline(match: Match) -> dict[str, float]:
    """The voice's params as its score has them now: the patch made by hand
    (REQ-MAT-06). Empty when the match names no score."""
    if not match.score:
        return {}
    try:
        voice = scores.load(match.score).voice(match.voice)
    except scores.ScoreError as exc:
        raise MatchError(f"{match.file.name}: {exc}") from exc
    if voice.sound.synth != match.synth:
        raise MatchError(
            f"{match.file.name}: {match.score} plays {match.voice} on "
            f"{voice.sound.synth or 'a sample'}, not {match.synth}"
        )
    return {name: float(value) for name, value in voice.params}


def start(match: Match, base: dict[str, float]) -> list[float] | None:
    """The baseline as a point of the knobs' space, for the search's first
    generation; None without a score. A knob the voice leaves out is at the
    synth's own default."""
    if not match.score:
        return None
    return match.units({**synth_controls(match.synth), **base})


def workers(asked: int) -> int:
    """scsynth runs at once: as asked, or the computer's cores less one."""
    return asked if asked > 0 else max(1, (os.cpu_count() or 2) - 1)


def batch(
    cfg: Config, runner: Runner, match: Match, rate: int, *, work: Path, runs: int = 0
) -> Batch:
    """How the match's candidates render: SuperCollider, at ``rate``."""
    tools = sc.find_tools(runner)
    if tools is None:
        raise MatchError("SuperCollider isn't installed: run `mph install supercollider`")
    synthdefs = sc.compile_synthdefs(runner, tools, cfg.data_dir)
    return Batch(
        runner=runner,
        tools=tools,
        synthdefs=synthdefs,
        synth=match.synth,
        play=match.play,
        rate=rate,
        span_s=match.reference.length_s,
        tail_s=match.tail_s,
        work=work,
        workers=workers(runs),
    )


@dataclass(frozen=True, slots=True)
class Fit:
    """A match's candidates, rendered and measured against its reference."""

    match: Match
    batch: Batch
    reference: views.Sound
    rms: float  # the reference's level, which every candidate is scaled to

    @classmethod
    def to(cls, match: Match, batch: Batch, reference: np.ndarray) -> Fit:
        mono = views.mono(reference)
        return cls(match, batch, views.look(mono, batch.rate, match.play.notes), views.rms_of(mono))

    def look(self, sound: np.ndarray) -> views.Sound:
        """A candidate's views, at the reference's level."""
        level = views.scaled_to(views.mono(sound), self.rms)
        return views.look(level, self.batch.rate, self.match.play.notes)

    def measure(self, points: np.ndarray) -> list[dict[str, float]]:
        """Each point's raw distance in every view: what the search calls."""
        return self.distances([self.match.patch(point.tolist()) for point in points])

    def distances(self, patches: Sequence[Patch]) -> list[dict[str, float]]:
        return self.batch.each(patches, lambda s: views.distances(self.reference, self.look(s)))
