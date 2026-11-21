"""Sung syllables (spec/components/benchmarks.md, REQ-BEN-19).

A small sung line runs through the score rules, the kinds of sound the
checks and Renoise take from it, and the SuperCollider bundles mph builds,
so CI covers REQ-BEN-19 without real apps. Real singing is measured in
test_real_supercollider.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mph.bench import kit, osc, render, sc, score
from tests.bench.test_patterns import own
from tests.bench.test_render import cfg_for, runner

LINE = """
id = "BM-SING"
title = "A sung line"
bpm = 120
bars = 1

[voices.lead]
notes     = "E4 -  -  -  -  -  -  -  G4 -  A4 G4 -  -  E4 -"
gate      = "x  -  .  .  .  .  .  .  x  .  x  x  -  .  x  ."
accent    = ".  .  .  .  .  .  .  .  X  .  .  .  .  .  .  ."
slide     = ".  .  .  .  .  .  .  .  .  .  S  .  .  .  .  ."
syllables = "o  -  -  -  -  -  -  -  ye -  la -  -  -  m  -"
sound = {{ synth = "{synth}" }}
chord = [0, 4]
"""


def line(synth: str = "mph_singer") -> score.Score:
    return score.load_text(LINE.format(synth=synth))


def changed(old: str, new: str) -> score.Score:
    text = LINE.format(synth="mph_singer")
    assert text.count(old) == 1, old
    return score.load_text(text.replace(old, new))


@pytest.mark.req("REQ-BEN-19")
def test_each_note_sings_its_syllable_and_a_slide_keeps_it() -> None:
    sung = [(e.step, e.pitch, e.syllable, e.tie) for e in own(score.events(line()), "lead")]
    assert sung == [
        (0, 64, "o", False),
        (8, 67, "ye", False),
        (10, 69, "la", True),  # slides into G4,
        (11, 67, "la", False),  # which goes on singing la
        (14, 64, "m", False),  # a hum
    ]
    # Another syllable is another sound: its own template, and its own sample in Renoise.
    kinds = [
        (e.pitch, e.accent, e.syllable) for e in render._kinds(own(score.events(line()), "lead"))
    ]
    assert kinds == [
        (64, False, "m"),
        (64, False, "o"),
        (67, False, "ye"),
        (67, True, "ye"),
        (69, False, "la"),
    ]


@pytest.mark.req("REQ-BEN-19")
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("ye -  la", "xe -  la", "the note at bar 1, step 9 sings 'xe'; a syllable is a vowel"),
        ("ye -  la", "yy -  la", "sings 'yy'"),
        ("ye -  la", "-  -  la", "the note at bar 1, step 9 sings '-'"),
        (
            "ye -  la -",
            "ye o  la -",
            "no note starts at bar 1, step 10, so its syllable is '-', not 'o'",
        ),
        ("la -  -", "la a  -", "no note starts at bar 1, step 12"),  # slid into: it starts nothing
        ('m  -"', 'm"', "accent, slide and syllables rows span the gate's bars"),
    ],
)
def test_every_note_that_starts_has_a_syllable_and_nothing_else_does(
    old: str, new: str, message: str
) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed(old, new)


@pytest.mark.req("REQ-BEN-19")
def test_a_line_without_syllables_sings_nothing_and_needs_notes_to_sing() -> None:
    plain = changed('syllables = "o  -  -  -  -  -  -  -  ye -  la -  -  -  m  -"\n', "")
    assert {e.syllable for e in score.events(plain)} == {""}
    assert sc.syllable_args("") == []
    with pytest.raises(score.ScoreError, match="accent, slide and syllables rows need notes"):
        score.load_text(
            'id = "BM-X"\nbpm = 120\nbars = 1\n[voices.kick]\n'
            'grid = "x . . . x . . . x . . . x . . ."\nsyllables = "a"\n'
            'sound = { role = "kick" }\n'
        )


@pytest.mark.req("REQ-BEN-19")
def test_syllables_are_numbered_as_the_singing_synths_read_them() -> None:
    assert sc.syllable_args("a") == ["vowel", 0, "onset", 0]
    assert sc.syllable_args("ye") == ["vowel", 1, "onset", 2]
    assert sc.syllable_args("bu") == ["vowel", 4, "onset", 8]
    assert sc.syllable_args("m") == ["vowel", 5, "onset", 0]  # the hum, after the five vowels
    assert sc.syllable_args("mo") == ["vowel", 3, "onset", 5]
    # The synths' tables are in the same order (synthdefs.scd).
    defs = sc.synthdef_source()
    onsets = defs[defs.index("~onsets = [") : defs.index("];", defs.index("~onsets = ["))]
    named = [line.rsplit("// ", 1)[1].strip() for line in onsets.splitlines()[1:] if "//" in line]
    assert named == ["none", *score.ONSETS[1:]]


def _args(message: osc.Message) -> dict[str, object]:
    return dict(zip(map(str, message.args[4::2]), message.args[5::2], strict=True))


@pytest.mark.req("REQ-BEN-19")
@pytest.mark.req("REQ-AUD-05")
def test_every_note_seeds_a_random_generator_of_its_own(tmp_path: Path) -> None:
    loaded = line()
    plan = sc.Plan(loaded, kit.choose(loaded, None, tmp_path), 48000, tmp_path / "defs")
    bundles = sc.build(plan, score.events(loaded))
    starts = [_args(m) for b in bundles for m in b.messages if m.address == "/s_new"]
    sung = [a for a in starts if "vowel" in a]
    assert len(sung) == 2 * 4  # four notes (one slid into), two parts of a chord each
    assert [(a["vowel"], a["onset"], a["part"]) for a in sung[:4]] == [
        (3, 0, 0),
        (3, 0, 1),
        (1, 2, 0),
        (1, 2, 1),
    ]
    assert {a["seed"] for a in sung} == {1}  # the voice's seed: every note of a sound is the same
    rids = [int(a["rid"]) for a in sung]  # type: ignore[call-overload]
    assert min(rids) >= sc.RANDOM_GENERATORS  # above the drums' 64
    assert len(set(rids)) == len(rids)  # notes that sound together each have their own


@pytest.mark.req("REQ-BEN-19")
def test_generators_are_lent_one_per_sounding_note() -> None:
    generators = sc.Generators()
    first = generators.take(1000, 0.0)
    second = generators.take(1001, 0.0)
    assert (first, second) == (sc.RANDOM_GENERATORS, sc.RANDOM_GENERATORS + 1)
    generators.give_back(1000, 1.0)
    assert generators.take(1002, 1.0 + sc.NOTE_ROOM_S - 0.01) == sc.RANDOM_GENERATORS + 2
    assert generators.take(1003, 1.0 + sc.NOTE_ROOM_S) == first  # free again
    crowd = sc.Generators()
    for node in range(sc.GENERATORS - sc.RANDOM_GENERATORS):
        crowd.take(node, 0.0)
    with pytest.raises(sc.RenderError, match=r"more than 960 notes sound at once at 0\.00 s"):
        crowd.take(5000, 0.0)


@pytest.mark.req("REQ-BEN-19")
def test_the_probes_quiet_a_singing_voice() -> None:
    assert {"breath", "waver", "hiss", "vibrato"} <= set(render.PROBE_PARAMS)
    assert all(render.PROBE_PARAMS[k] == 0.0 for k in ("breath", "waver", "hiss", "vibrato"))
    assert render.STEADY_PARAMS == {"vibrato": 0.0, "waver": 0.0}  # the glide probe's
    defs = sc.synthdef_source()
    for synth in ("mph_singer", "mph_vocoder"):
        controls = defs[defs.index(f"SynthDef(\\{synth},") :].split("|")[1]
        assert all(
            f"{name} =" in controls
            for name in ("vowel", "onset", "breath", "waver", "seed", "rid", "part")
        )


@pytest.mark.req("REQ-BEN-19")
@pytest.mark.parametrize("synth", ["mph_singer", "mph_vocoder"])
def test_a_sung_line_renders_and_passes_its_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synth: str
) -> None:
    loaded = line(synth)
    monkeypatch.setattr(render, "load", lambda _: loaded)
    outcome = render.run("BM-SING", cfg_for(tmp_path), runner(), None, engine="supercollider")
    failures = [m for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []
