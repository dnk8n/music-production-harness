from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from mph.bench import score
from mph.bench.score import ScoreError

SPEC = Path(__file__).resolve().parents[2] / "spec" / "components" / "benchmarks.md"
SECTION = re.compile(r"^### (BM-[A-Z]+-\d+): .*?(?=^### |\Z)", re.MULTILINE | re.DOTALL)


def spec_sections() -> dict[str, str]:
    return {m.group(1): m.group(0) for m in SECTION.finditer(SPEC.read_text("utf-8"))}


def spec_grid(section: str) -> dict[str, str]:
    block = re.search(r"```\n(.*?)```", section, re.DOTALL)
    rows = {}
    for line in (block.group(1) if block else "").splitlines():
        name, _, cells = line.partition(" ")
        rows[name] = " ".join(cells.split())
    return rows


@pytest.mark.req("REQ-BEN-04")
@pytest.mark.parametrize("benchmark_id", score.available())
def test_scores_match_the_spec(benchmark_id: str) -> None:
    section = spec_sections()[benchmark_id]
    loaded = score.load(benchmark_id)
    first_line = section.splitlines()[1]
    assert f"{loaded.bpm:g} BPM" in first_line
    swing = re.search(r"swing (\d+)%", first_line)
    assert loaded.swing == (int(swing.group(1)) / 100 if swing else 0.5)
    rows = spec_grid(section)
    if "note" in rows:  # a melodic line: note, accent, slide and gate rows for its one voice
        voice = next(v for v in loaded.voices if v.melodic)
        assert " ".join(voice.notes) == rows.pop("note")
        assert voice.accents == rows.pop("accent")
        assert voice.slides == rows.pop("slide")
        assert voice.grid.split() == rows.pop("gate").split()
    for name, cells in rows.items():
        assert score.bar_one(loaded, loaded.voice(name)) == cells, name


@pytest.mark.req("REQ-BEN-02")
def test_every_score_has_a_written_expectation() -> None:
    sections = spec_sections()
    for benchmark_id in score.available():
        section = sections[benchmark_id]
        assert "Hear:" in section and "**Why:**" in section and "**Verify:**" in section


def test_swing_delays_every_second_sixteenth() -> None:
    step = 0.125
    assert score.swung_time(0, step, 0.58) == 0.0
    assert score.swung_time(1, step, 0.58) == pytest.approx(0.145)
    assert score.swung_time(2, step, 0.58) == pytest.approx(0.25)


def test_velocity_rules() -> None:
    rim = [e for e in score.events(score.load("BM-DRUM-05")) if e.voice == "rim"]
    assert [e.velocity for e in rim[:4]] == [1.0, 0.45, 1.0, 0.45]
    snare = [e for e in score.events(score.load("BM-DRUM-10")) if e.voice == "snare"]
    bar4 = [e.velocity for e in snare if 48 + 12 <= e.step < 64]
    assert bar4 == [0.6, 0.8667, 1.0]  # steps 12, 14, 15 of the 4th bar
    assert [e.velocity for e in snare if e.step < 16] == [0.8, 0.8, 0.8, 0.8]


def test_humanising_is_seeded_and_bounded() -> None:
    first = [e.velocity for e in score.events(score.load("BM-DRUM-08")) if e.voice == "shaker"]
    again = [e.velocity for e in score.events(score.load("BM-DRUM-08")) if e.voice == "shaker"]
    assert first == again
    assert len(set(first)) > 1
    assert all(0.72 <= v <= 0.88 for v in first)


def test_pulses_cross_bar_lines_and_slices_repeat() -> None:
    toms = [e.step for e in score.events(score.load("BM-DRUM-07")) if e.voice == "tom"]
    assert toms[:7] == [0, 3, 6, 9, 12, 15, 18]
    assert 48 in toms  # lines up with the bar again after 3 bars
    chop = [e for e in score.events(score.load("BM-DRUM-09")) if e.voice == "break"]
    assert [e.slice for e in chop[:16]] == [1, 2, 3, 4, 1, 2, 7, 8, 1, 2, 3, 4, 5, 5, 7, 8]
    assert len(chop) == 8 * 8 and chop[1].step == 2


def test_digest_covers_extra_inputs() -> None:
    loaded = score.load("BM-DRUM-01")
    assert loaded.digest() == loaded.digest()
    assert loaded.digest({"kit": ["a"]}) != loaded.digest({"kit": ["b"]})
    assert loaded.length_s == pytest.approx(8 * 16 * 60 / 130 / 4 + 1.0)


GOOD = """
id = "BM-T"
bpm = 120
bars = 1
[voices.a]
grid = "x . . . . . . . . . . . . . . ."
"""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (GOOD.replace('grid = "x', 'grid = "q'), "a grid needs"),
        (GOOD + "pulse = 3\n", "exactly one of"),
        (GOOD.replace("bars = 1", "bars = 1\nswing = 0.4"), "swing"),
        (GOOD + 'chokes = ["b"]\n', "chokes unknown"),
        (GOOD.replace('id = "BM-T"', ""), "missing"),
        ("id = ", "Invalid value"),
    ],
)
def test_bad_scores(text: str, message: str) -> None:
    with pytest.raises(ScoreError, match=message):
        score.load_text(text)


def test_unknown_benchmark_and_voice() -> None:
    with pytest.raises(ScoreError, match="no score for BM-NOPE"):
        score.load("BM-NOPE")
    with pytest.raises(ScoreError, match="no voice"):
        score.load_text(GOOD).voice("zz")


VARIED = """
id = "BM-V"
bpm = 120
bars = 1

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = { role = "kick", words = ["909"] }

[voices.chh]
grid = ". . x . . . x . . . x . . . x ."
sound = { role = "hat" }

[variants.other-hat]
title = "the second-best hat, quieter"
voices = { chh = { sound = { pick = 2 }, gain_db = -3 } }

[variants.synth-hat]
title = "a synth hat"
engine = "supercollider"
voices = { chh = { sound = { synth = "mph_hat" } } }
"""


@pytest.mark.req("REQ-BEN-07")
def test_variants_are_standard_plus_declared() -> None:
    loaded = score.load_text(VARIED)
    names = [v.name for v in score.all_variants(loaded)]
    assert names == ["renoise", "supercollider", "synth-kit", "other-hat", "synth-hat"]  # no main
    other, variant = score.variant_of(loaded, "other-hat")
    hat = other.voice("chh")
    assert (hat.sound.role, hat.sound.pick, hat.gain_db) == ("hat", 2, -3.0)
    assert other.voice("kick") == loaded.voice("kick")
    assert variant.engine == "" and other.variant == "other-hat"
    synth, variant = score.variant_of(loaded, "synth-hat")
    assert synth.voice("chh").sound.synth == "mph_hat" and variant.engine == "supercollider"
    kit, _ = score.variant_of(loaded, "synth-kit")
    assert {v.sound.synth for v in kit.voices} == {"auto"}
    renoise, _ = score.variant_of(loaded, "renoise")
    # Each variant is its own render; declaring more variants doesn't change another's hash.
    assert len({renoise.digest(), other.digest(), synth.digest(), kit.digest()}) == 4
    trimmed = score.load_text(VARIED.split("[variants.synth-hat]", maxsplit=1)[0])
    assert score.variant_of(trimmed, "renoise")[0].digest() == renoise.digest()
    assert replace(renoise, project="Elsewhere").digest() == renoise.digest()  # just a listing
    assert [v.as_scored for v in score.all_variants(loaded)] == [True, True, False, False, False]
    assert "synth-kit" not in [v.name for v in score.all_variants(score.load("BM-DRUM-09"))]


def test_variant_mistakes_are_reported() -> None:
    loaded = score.load_text(VARIED)
    with pytest.raises(score.ScoreError, match="no variant 'nope'"):
        score.variant_of(loaded, "nope")
    with pytest.raises(score.ScoreError, match="engine must be one of"):
        score.load_text(VARIED.replace('engine = "supercollider"', 'engine = "tidal"'))
    with pytest.raises(score.ScoreError, match="unknown voices"):
        score.load_text(
            VARIED.replace("voices = { chh = { sound = { pick", "voices = { zz = { sound = { pick")
        )
    clash = score.load_text(VARIED.replace("[variants.other-hat]", "[variants.renoise]"))
    with pytest.raises(score.ScoreError, match="is a standard one"):
        score.all_variants(clash)


# --- melodic voices (BM-BASS-01) --------------------------------------------------------

ACID = """
id = "BM-T"
bpm = 120
bars = 1
[voices.bass]
notes  = "C2 C2 C3 C2 D#2 C2 G2 C2 C2 A#2 C2 C3 C2 F2 C2 G2"
accent = ".  .  X  .  .   .  .  .  .  .   .  .  .  .  .  ."
slide  = ".  .  .  .  S   .  .  .  .  S   .  .  .  .  .  S"
gate   = "x  x  x  .  x   x  x  x  x  x   .  x  x  x  x  x"
sound = { synth = "mph_acid" }
params = { cutoff = 420.0 }
"""


def test_note_names() -> None:
    assert [score.midi(n) for n in ("C2", "D#2", "Bb1", "A4", "C-1")] == [36, 39, 34, 69, 0]
    with pytest.raises(ScoreError, match="not a note: 'H2'"):
        score.midi("H2")


@pytest.mark.req("REQ-BEN-04")
def test_a_melodic_voice_plays_notes_with_303_slides() -> None:
    loaded = score.load_text(ACID)
    assert loaded.melodic and loaded.voice("bass").params == (("cutoff", 420.0),)
    found = {e.step: e for e in score.events(loaded)}
    assert sorted(found) == [0, 1, 2, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15]  # the gate
    step = loaded.step_s
    assert found[2].pitch == 48 and found[2].accent and not found[1].accent
    assert found[0].length_s == pytest.approx(step / 2)  # an ordinary note: half a step
    # A slide into a played step ties on, held for the whole step.
    assert found[4].tie and found[4].length_s == pytest.approx(step)
    # A slide before a rest just holds; so does one on the last step of the score.
    assert not found[9].tie and found[9].length_s == pytest.approx(step)
    assert not found[15].tie and found[15].length_s == pytest.approx(step)
    assert {e.kind for e in (found[2], found[0])} == {(0, 48, True), (0, 36, False)}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('notes  = "C2 C2', 'notes  = "C2'), "notes needs 16 entries"),
        (('notes  = "C2', 'notes  = "X9'), "not a note"),
        (('gate   = "x  x  x  .  x   x  x  x  x  x   .  x  x  x  x  x"', ""), "needs a gate row"),
        (('slide  = ".', 'slide  = "Q'), "a row needs 16 symbols"),
    ],
)
def test_melodic_mistakes(change: tuple[str, str], message: str) -> None:
    with pytest.raises(ScoreError, match=message):
        score.load_text(ACID.replace(*change, 1))
    drum = (
        'id = "X"\nbpm = 120\nbars = 1\n[voices.k]\ngrid = "'
        + "x " * 16
        + '"\naccent = "'
        + "X " * 16
        + '"\n'
    )
    with pytest.raises(ScoreError, match="accent and slide rows need notes"):
        score.load_text(drum)


@pytest.mark.req("REQ-BEN-07")
def test_melodic_variants_change_synth_controls() -> None:
    loaded = score.load("BM-BASS-01")
    names = [v.name for v in score.all_variants(loaded)]
    assert names == ["supercollider", "cutoff-low", "cutoff-high", "resonance-high"]
    low, variant = score.variant_of(loaded, "cutoff-low")
    params = dict(low.voice("acid").params)
    assert params["cutoff"] == 120.0 and params["resonance"] == 0.6  # only the cutoff changes
    assert variant.title.startswith("cutoff turned right down")
    assert low.digest() != loaded.digest()
    with pytest.raises(ScoreError, match="unknown voices"):
        score.load_text(ACID + "[variants.x]\nvoices = { nope = { params = { cutoff = 1.0 } } }\n")


@pytest.mark.req("REQ-BEN-11")
@pytest.mark.parametrize("benchmark_id", score.available())
def test_every_score_names_the_project_it_is_written_under(benchmark_id: str) -> None:
    text = SPEC.read_text("utf-8")
    heading = text.index(f"### {benchmark_id}:")
    section = re.findall(r"^## (.+)$", text[:heading], re.MULTILINE)[-1]
    assert score.load(benchmark_id).project == section
