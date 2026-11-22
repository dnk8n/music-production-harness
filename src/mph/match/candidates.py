"""Candidates rendered as the track renders them (spec/components/matching.md, REQ-MAT-02).

A generation of candidates renders in a few scsynth runs at once, each run
playing its share one after another, a slot each: the reference's span,
then room for the release. Every note is a synth of its own, with its part
and a random generator of its own, seeded the same for every candidate,
so candidates differ only in their knobs. Processes are spawned only
through ``mph.proc.Runner``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mph.bench import audio, sc
from mph.bench.osc import Arg, Bundle, Message, encode_score
from mph.match.matchfile import Play
from mph.proc import Runner

FIRST_NODE = 1000
NOTES_EACH = 16  # node ids (and random generators) a candidate may use, one per note
TAIL = 1  # /s_new add action: after the group's other nodes
ROOT = 0  # the root group
FIRST_GENERATOR = sc.RANDOM_GENERATORS  # the notes' random generators, as a render's
SEED = 1

Patch = Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Batch:
    """How to render candidates of one synth playing one set of notes."""

    runner: Runner
    tools: sc.Tools
    synthdefs: Path
    synth: str
    play: Play
    rate: int
    span_s: float  # each candidate's audio that is compared: the reference's span
    tail_s: float  # room after the span for a release, so slots never overlap
    work: Path
    workers: int = 1

    @property
    def slot_s(self) -> float:
        return self.span_s + self.tail_s

    def render(self, patches: Sequence[Patch]) -> list[np.ndarray]:
        """Each patch's sound, stereo, as long as the reference's span."""
        return self.each(patches, lambda sound: sound)

    def each[T](self, patches: Sequence[Patch], use: Callable[[np.ndarray], T]) -> list[T]:
        """``use`` of each patch's sound, called as each run finishes, in the
        patches' order. A run's audio is gone once its share has been used."""
        if not patches:
            return []
        shares = _shares(len(patches), max(1, self.workers))
        jobs = [(k, patches[first:last]) for k, (first, last) in enumerate(shares)]
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            done = list(pool.map(lambda job: [use(s) for s in self._run(*job)], jobs))
        return [found for share in done for found in share]

    def bundles(self, patches: Sequence[Patch]) -> list[Bundle]:
        """The OSC score of one run: each candidate in its own slot."""
        bundles = [Bundle(0.0, (Message("/d_loadDir", (str(self.synthdefs),)),))]
        for n, patch in enumerate(patches):
            start = self._sample(n * self.slot_s + self.play.at_s)
            stop = self._sample(n * self.slot_s + self.play.at_s + self.play.length_s)
            nodes = [FIRST_NODE + n * NOTES_EACH + part for part in range(len(self.play.notes))]
            starts = tuple(
                Message("/s_new", (self.synth, node, TAIL, ROOT, *self._args(patch, n, part, hz)))
                for part, (node, hz) in enumerate(zip(nodes, self.play.notes, strict=True))
            )
            bundles.append(Bundle(start, starts))
            bundles.append(
                Bundle(stop, tuple(Message("/n_set", (node, "gate", 0)) for node in nodes))
            )
        bundles.append(
            Bundle(self._sample(len(patches) * self.slot_s), (Message("/c_set", (0, 0)),))
        )
        return bundles

    def _args(self, patch: Patch, n: int, part: int, hz: float) -> list[Arg]:
        """A note's controls. Neighbouring slots draw from different random
        generators, so a release that runs on can't disturb the next one's."""
        args: list[Arg] = ["out", 0, "amp", 1.0, "freq", hz, "acc", 0, "seed", SEED]
        args += ["rid", FIRST_GENERATOR + (n % 2) * NOTES_EACH + part, "part", part]
        for name, value in sorted(patch.items()):
            args += [name, float(value)]
        return args

    def _sample(self, seconds: float) -> float:
        """A time on a whole sample, so every slot starts exactly where it is read."""
        return round(seconds * self.rate) / self.rate

    def _run(self, share: int, patches: Sequence[Patch]) -> list[np.ndarray]:
        out = self.work / f"share-{share}.wav"
        score_file = out.with_suffix(".osc")
        out.parent.mkdir(parents=True, exist_ok=True)
        score_file.write_bytes(encode_score(self.bundles(patches)))
        argv = [self.tools.scsynth, "-o", "2", "-w", str(sc.WIRE_BUFFERS), "-r", str(sc.GENERATORS)]
        argv += ["-N", str(score_file), "_", str(out), str(self.rate), "WAV", "float"]
        result = self.runner.run(argv)
        score_file.unlink(missing_ok=True)
        said = f"{result.stdout}\n{result.stderr}"
        if not result.ok or not out.is_file() or any(s in said for s in sc.NOT_STARTED):
            out.unlink(missing_ok=True)
            raise sc.RenderError(f"scsynth couldn't render the candidates: {said.strip()[-500:]}")
        data, _ = audio.read(out)
        out.unlink()
        frames = round(self.span_s * self.rate)
        found = []
        for n in range(len(patches)):
            first = round(n * self.slot_s * self.rate)
            piece = data[first : first + frames]
            found.append(np.pad(piece, ((0, frames - len(piece)), (0, 0))))
        return found


def _shares(count: int, workers: int) -> list[tuple[int, int]]:
    """Split ``count`` candidates into at most ``workers`` runs of near-equal size."""
    runs = min(count, workers)
    edges = [round(k * count / runs) for k in range(runs + 1)]
    return [(edges[k], edges[k + 1]) for k in range(runs)]
