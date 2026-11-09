from __future__ import annotations

import re
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
    for name, cells in spec_grid(section).items():
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
    assert names == ["main", "supercollider", "renoise", "synth-kit", "other-hat", "synth-hat"]
    other, variant = score.variant_of(loaded, "other-hat")
    hat = other.voice("chh")
    assert (hat.sound.role, hat.sound.pick, hat.gain_db) == ("hat", 2, -3.0)
    assert other.voice("kick") == loaded.voice("kick")
    assert variant.engine == "" and other.variant == "other-hat"
    synth, variant = score.variant_of(loaded, "synth-hat")
    assert synth.voice("chh").sound.synth == "mph_hat" and variant.engine == "supercollider"
    kit, _ = score.variant_of(loaded, "synth-kit")
    assert {v.sound.synth for v in kit.voices} == {"auto"}
    main, _ = score.variant_of(loaded, "main")
    # Each variant is its own render; declaring more variants doesn't change main's hash.
    assert len({main.digest(), other.digest(), synth.digest(), kit.digest()}) == 4
    trimmed = score.load_text(VARIED.split("[variants.synth-hat]", maxsplit=1)[0])
    assert score.variant_of(trimmed, "main")[0].digest() == main.digest()
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
