"""Patterns longer than a bar and chords (spec/components/benchmarks.md,
REQ-BEN-17, REQ-BEN-18).

Small scores run through the score rules and the SuperCollider bundles mph
builds, and a chord renders with the fake scsynth, so CI covers both
without real apps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from mph.bench import kit, osc, render, sc, score
from mph.proc import Result
from tests.bench.fake_sc import fake_scsynth
from tests.bench.test_render import cfg_for, runner

KICK = "X . . . X . . . X . . . X . . . | X . . . X . . . X . . . X . x ."
BASS_NOTES = (
    "C2 C2 C3 C2 C2 C2 C3 C2 C2 C2 C3 C2 C2 C2 G2 G2 | "
    "F1 F1 F2 F1 F1 F1 F2 F1 F1 F1 F2 F1 F1 F1 G1 G1"
)
BASS_GATE = "x x x x . x x x . x x x . x x x | x x x x . x x x . x x x . x x x"
ACCENTS = ". . X . . . X . . . X . . . X . | . . X . . . X . . . X . . . X ."
SLIDES = ". . . . . . . . . . . . . . . S | . . . . . . . . . . . . . . . S"
STAB_GATE = ". . . x . . . . . . x - . . . . | . . . x . . . . . . x - . . . ."
TWO_BARS = f"""
id = "BM-TWO"
title = "Two-bar patterns"
bpm = 120
bars = {{bars}}
{{sections}}
[voices.kick]
grid = "{KICK}"
sound = {{{{ role = "kick" }}}}

[voices.bass]
notes  = "{BASS_NOTES}"
gate   = "{BASS_GATE}"
accent = "{ACCENTS}"
slide  = "{SLIDES}"
sound = {{{{ synth = "mph_bass" }}}}

[voices.stab]
notes = "{" ".join(["E4"] * 16)} | {" ".join(["A4"] * 16)}"
gate  = "{STAB_GATE}"
chord = [0, 3, 7, 10]
sound = {{{{ synth = "mph_stab" }}}}
"""
SECTIONS = """sections = [
  { name = "A", bars = [1, 3], play = ["kick", "bass", "stab"] },
  { name = "B", bars = [4, 4], play = ["kick"] },
]
"""


def two_bars(bars: int = 4, sections: str = "") -> score.Score:
    return score.load_text(TWO_BARS.format(bars=bars, sections=sections))


def changed(old: str, new: str) -> score.Score:
    text = TWO_BARS.format(bars=4, sections="")
    assert text.count(old) == 1, old
    return score.load_text(text.replace(old, new))


def own(found: list[score.Event], voice: str) -> list[score.Event]:
    return [e for e in found if e.voice == voice]


# --- patterns longer than a bar (REQ-BEN-17) -------------------------------------------------


@pytest.mark.req("REQ-BEN-17")
def test_a_pattern_of_two_bars_alternates_from_bar_one() -> None:
    loaded = two_bars()
    kick, bass = loaded.voice("kick"), loaded.voice("bass")
    assert (kick.steps, kick.length, bass.steps, bass.length) == (16, 32, 16, 32)
    found = score.events(loaded)
    kicks = [e.step for e in own(found, "kick")]
    assert kicks == [0, 4, 8, 12, 16, 20, 24, 28, 30, 32, 36, 40, 44, 48, 52, 56, 60, 62]
    pitches = {e.step: e.pitch for e in own(found, "bass")}
    assert (pitches[1], pitches[17], pitches[33], pitches[49]) == (36, 29, 36, 29)  # C2, F1
    # Each bar's last note slides into the next bar's first; the score's last has
    # nowhere to go.
    assert [e.step for e in own(found, "bass") if e.tie] == [15, 31, 47]
    assert score.bar_one(loaded, kick) == "X . . . X . . . X . . . X . . ."


@pytest.mark.req("REQ-BEN-17")
@pytest.mark.req("REQ-TRK-01")
@pytest.mark.req("REQ-TRK-03")
def test_an_excerpt_plays_the_bar_of_the_pattern_its_track_plays() -> None:
    whole = two_bars(sections=SECTIONS)
    # The slide out of bar 3 has nowhere to go in bar 4, where the bass rests,
    # so that note ends instead of holding on.
    assert [e.step for e in own(score.events(whole), "bass") if e.tie] == [15, 31]
    part = score.excerpt(whole, 2, 3, id="BM-TWO-X")
    found = own(score.events(part), "bass")
    assert found[0].step == 0 and found[0].pitch == 29  # the pattern's second bar: F1
    assert [e.step for e in found if e.tie] == [15]


@pytest.mark.req("REQ-BEN-17")
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "X . . . X . . . X . . . X . x .",
            "X . . . X . . . X . . . X . x",
            "16 symbols from .* in each bar \\(12 for triplets\\), with bars split by",
        ),
        (" | F1 F1 F2 F1 F1 F1 F2 F1 F1 F1 F2 F1 F1 F1 G1 G1", "", "notes needs 16 entries a bar"),
        (ACCENTS, ACCENTS[:-2], "a row needs 16 symbols a bar from"),
        (BASS_GATE, BASS_GATE[:-2], "a melodic gate row needs 16 steps a bar"),
        (SLIDES, SLIDES[:31], "accent and slide rows span the gate's bars"),
    ],
)
def test_long_patterns_keep_their_bars_even(old: str, new: str, message: str) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed(old, new)


@pytest.mark.req("REQ-BEN-17")
def test_a_melodic_gate_plays_at_least_one_note() -> None:
    silent = " | ".join([" ".join(["."] * 16)] * 2)
    with pytest.raises(score.ScoreError, match="a melodic gate row needs at least one note"):
        changed(STAB_GATE, silent)


# --- chords (REQ-BEN-18) ---------------------------------------------------------------------


@pytest.mark.req("REQ-BEN-18")
def test_a_chord_moves_one_shape_with_the_line() -> None:
    loaded = two_bars()
    assert loaded.voice("stab").chord == (0, 3, 7, 10)
    stabs = own(score.events(loaded), "stab")
    assert [(e.step, e.pitch) for e in stabs[:4]] == [(3, 64), (10, 64), (19, 69), (26, 69)]
    assert score.first_note(loaded, loaded.voice("stab")).pitch == 64


@pytest.mark.req("REQ-BEN-18")
@pytest.mark.parametrize(
    ("chord", "message"),
    [
        ("[]", "chord lists 1 to 8 different semitones"),
        ("[0, 3, 3]", "chord lists 1 to 8 different semitones"),
        ("[0, 3.5]", "chord lists 1 to 8 different semitones"),
        ("[0, 1, 2, 3, 4, 5, 6, 7, 8]", "chord lists 1 to 8 different semitones"),
        ("[0, 40]", "a chord's notes are -24 to 36 semitones away"),
        ('"minor"', "chord lists 1 to 8 different semitones"),
    ],
)
def test_a_chord_is_a_short_list_of_semitones(chord: str, message: str) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed("chord = [0, 3, 7, 10]", f"chord = {chord}")


@pytest.mark.req("REQ-BEN-18")
def test_only_a_melodic_voice_plays_chords() -> None:
    with pytest.raises(score.ScoreError, match="accent and slide rows need notes, as chords do"):
        score.load_text(
            'id = "X"\nbpm = 120\nbars = 1\n[voices.kick]\n'
            'grid = "x . . . x . . . x . . . x . . ."\nchord = [0, 7]\n'
        )


def _messages(tmp_path: Path, loaded: score.Score) -> list[tuple[float, osc.Message]]:
    plan = sc.Plan(loaded, kit.choose(loaded, None, tmp_path), 48000, tmp_path / "defs")
    return [(b.time_s, m) for b in sc.build(plan, score.events(loaded)) for m in b.messages]


def _freq(message: osc.Message) -> float:
    return float(dict(zip(message.args[4::2], message.args[5::2], strict=True))["freq"])


@pytest.mark.req("REQ-BEN-18")
def test_each_note_of_a_chord_is_its_own_synth_started_and_released_together(
    tmp_path: Path,
) -> None:
    messages = _messages(tmp_path, two_bars())
    stabs = [(t, m) for t, m in messages if m.address == "/s_new" and m.args[0] == "mph_stab"]
    first = stabs[:4]
    assert len(stabs) == 4 * 8 and len({t for t, _ in first}) == 1  # four notes at once
    nodes = [int(m.args[1]) for _, m in first]
    assert tuple(nodes) == sc.chord_nodes(nodes[0], 4)
    assert [_freq(m) for _, m in first] == pytest.approx([sc.hz(n) for n in (64, 67, 71, 74)])
    releases = [m.args for _, m in messages if m.address == "/n_set" and m.args[0] in nodes]
    assert releases == [(n, "gate", 0) for n in nodes]


@pytest.mark.req("REQ-BEN-18")
def test_a_chord_slides_every_note_together(tmp_path: Path) -> None:
    loaded = two_bars()
    fifths = replace(loaded.voice("bass"), chord=(0, 7))
    messages = _messages(tmp_path, replace(loaded, voices=(fifths,)))
    glides = [m.args for _, m in messages if m.address == "/n_set" and "freq" in m.args]
    assert len(glides) == 2 * 3  # three slides in four bars, both notes each time
    assert [g[2] for g in glides[:2]] == pytest.approx([sc.hz(29), sc.hz(36)])  # F1 and C2
    starts = [m for _, m in messages if m.address == "/s_new" and m.args[0] == "mph_bass"]
    assert len(starts) == 2 * (len(own(score.events(loaded), "bass")) - 3)  # no retrigger


@pytest.mark.req("REQ-BEN-18")
@pytest.mark.req("REQ-BEN-09")
def test_a_chord_is_level_matched_whole_and_probed_one_note_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = two_bars()
    monkeypatch.setattr(render, "load", lambda _: loaded)
    played: dict[str, int] = {}  # the file each render writes -> stab synths it started

    def counting(argv: Sequence[str]) -> Result:
        if "-N" in argv:
            bundles = osc.decode_score(Path(argv[argv.index("-N") + 1]).read_bytes())
            stabs = sum(m.args[:1] == ("mph_stab",) for b in bundles for m in b.messages)
            played[Path(argv[argv.index("-N") + 3]).name] = stabs
        return fake_scsynth(argv)

    outcome = render.run(
        "BM-TWO", cfg_for(tmp_path), runner(scsynth=counting), None, engine="supercollider"
    )
    failures = [m for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []
    assert played["sounds.wav"] == 4  # the whole chord, for its level
    assert played["probe-stab.wav"] == 1  # one note of it alone, for aliasing
