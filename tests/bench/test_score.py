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


@pytest.mark.req("REQ-BEN-07")
def test_no_synth_kit_take_for_a_score_of_synths() -> None:
    """Rating 156: BM-DRUM-04's synth-kit take sounded exactly like its other takes."""
    synths = VARIED.replace(
        'sound = { role = "kick", words = ["909"] }', 'sound = { synth = "mph_kick" }'
    )
    every = synths.replace('sound = { role = "hat" }', 'sound = { synth = "mph_hat" }')
    assert "synth-kit" in [v.name for v in score.all_variants(score.load_text(synths))]
    assert "synth-kit" not in [v.name for v in score.all_variants(score.load_text(every))]
    assert "synth-kit" not in [v.name for v in score.all_variants(score.load("BM-DRUM-04"))]


PINNED = "ab" * 32
FRESH = (
    VARIED
    + f"""
[variants.fresh-take]
title = "sounds an agent picked"
[variants.fresh-take.voices.kick]
sound = {{ hash = "{PINNED}", why = "a rounder 909 kick" }}
[variants.fresh-take.voices.chh]
sound = {{ synth = "mph_hat", why = "the synth hat sits better" }}
"""
)


@pytest.mark.req("REQ-BEN-16")
def test_a_voice_can_pin_one_catalogue_sound_with_why() -> None:
    loaded = score.load_text(FRESH)
    fresh, variant = score.variant_of(loaded, "fresh-take")
    kick = fresh.voice("kick").sound
    # The voice's search stays, for when the pinned sound isn't there.
    assert (kick.role, kick.words, kick.hash, kick.why) == (
        "kick",
        ("909",),
        PINNED,
        "a rounder 909 kick",
    )
    assert fresh.voice("chh").sound.why == "the synth hat sits better"
    assert not variant.as_scored
    # What hashes and reports record: no reason, and no hash where none is pinned.
    asked = {"role": "kick", "words": ("909",), "synth": "", "pick": 1}
    assert score.recorded(kick) == {**asked, "hash": PINNED}
    assert score.recorded(loaded.voice("kick").sound) == asked
    reworded = score.variant_of(
        score.load_text(FRESH.replace("a rounder", "a fuller")), "fresh-take"
    )
    assert reworded[0].digest() == fresh.digest()  # a reason doesn't change the sound
    other = score.variant_of(score.load_text(FRESH.replace(PINNED, "cd" * 32)), "fresh-take")
    assert other[0].digest() != fresh.digest()
    renoise, _ = score.variant_of(loaded, "renoise")
    assert renoise.digest() == score.variant_of(score.load_text(VARIED), "renoise")[0].digest()
    # The synth-kit take plays synths, pins or not.
    pinned_here = FRESH.replace(
        'sound = { role = "kick", words = ["909"] }',
        f'sound = {{ role = "kick", hash = "{PINNED}", why = "picked" }}',
    )
    kit, _ = score.variant_of(score.load_text(pinned_here), "synth-kit")
    assert (kit.voice("kick").sound.synth, kit.voice("kick").sound.hash) == ("auto", "")


@pytest.mark.req("REQ-BEN-16")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ((PINNED, "abc"), "hash needs the sound's 64 hex digits: 'abc'"),
        ((PINNED, PINNED.upper()), "hash needs the sound's 64 hex digits"),
        (('why = "a rounder 909 kick"', 'why = " "'), "a pinned sound needs a why"),
        (('hash = "', 'synth = "mph_kick", hash = "'), "pins a hash or asks for a synth, not both"),
        (('hash = "', 'role = "", hash = "'), "a pinned sound needs its role"),
        (('why = "a rounder', 'hsah = "", why = "a rounder'), "a sound takes role, words"),
    ],
)
def test_pin_mistakes_are_reported(change: tuple[str, str], message: str) -> None:
    with pytest.raises(ScoreError, match=re.escape(message)):
        score.load_text(FRESH.replace(*change))
    with pytest.raises(ScoreError, match="voice zz: a sound takes"):
        score.load_text(GOOD.replace("[voices.a]", "[voices.zz]") + 'sound = { kind = "kick" }\n')


FRESH_TAKES = (  # Dean's ideas 1 to 13 (2026-11-13); BM-BASS-01 has only its acid synth
    "BM-DRUM-01",
    "BM-DRUM-02",
    "BM-DRUM-03",
    "BM-DRUM-04",
    "BM-DRUM-05",
    "BM-DRUM-06",
    "BM-DRUM-07",
    "BM-DRUM-08",
    "BM-DRUM-09",
    "BM-DRUM-10",
    "BM-AFRO-01",
    "BM-AFRO-02",
    "BM-SC-01",
)


@pytest.mark.req("REQ-BEN-16")
@pytest.mark.parametrize("benchmark_id", FRESH_TAKES)
def test_fresh_takes_pin_sounds_with_reasons(benchmark_id: str) -> None:
    loaded = score.load(benchmark_id)
    fresh, _ = score.variant_of(loaded, "fresh-take")
    changed = [v for v in fresh.voices if v.sound != loaded.voice(v.name).sound]
    # A catalogue sound the other takes don't play, so the take is new to Dean.
    assert any(v.sound.hash for v in changed)
    assert all(v.sound.why for v in changed)  # each change says why


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


# --- triplet grids and feel (REQ-BEN-13) ---------------------------------------------

TRIPLETS = """
id = "BM-T3"
bpm = 120
bars = 2
feel = [41, 31, 28]

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = { role = "kick" }

[voices.bell]
grid = "X . x . x x . x . x . x"
sound = { role = "bell" }

[variants.even]
feel = [1, 1, 1]

[variants.flat]
voices = { bell = { grid = "X . . x . . x x . . x . x . . x" } }
"""


def beats(found: list[score.Event], voice: str, beat_s: float, steps: int = 12) -> list[float]:
    return [round(e.time_s / beat_s, 4) for e in found if e.voice == voice and e.step < steps]


@pytest.mark.req("REQ-BEN-13")
def test_triplet_grids_play_beside_sixteenths_with_the_feel() -> None:
    loaded = score.load_text(TRIPLETS)
    bell = loaded.voice("bell")
    assert (bell.steps, bell.triplets, loaded.voice("kick").steps) == (12, True, 16)
    assert loaded.triplets and loaded.feel == (41.0, 31.0, 28.0)
    found = score.events(loaded)
    # Long, short, short: the second pulse of a beat starts 41% in, the third 72% in.
    assert beats(found, "bell", loaded.beat_s) == [0.0, 0.72, 1.41, 1.72, 2.41, 3.0, 3.72]
    assert beats(found, "kick", loaded.beat_s, 16) == [0.0, 1.0, 2.0, 3.0]  # the four stays put
    second_bar = [e for e in found if e.voice == "bell"][7]
    assert second_bar.step == 12 and second_bar.time_s == pytest.approx(4 * loaded.beat_s)
    assert score.bar_one(loaded, bell) == "X . x . x x . x . x . x"
    assert score.feel_shares(score.EVEN) == pytest.approx([0, 1 / 3, 2 / 3])
    assert score.triplet_time(4, 0.5, score.EVEN) == pytest.approx(0.5 + 0.5 / 3)


@pytest.mark.req("REQ-BEN-13")
@pytest.mark.req("REQ-BEN-07")
def test_variants_can_change_a_drum_grid_or_the_feel() -> None:
    loaded = score.load_text(TRIPLETS)
    even, variant = score.variant_of(loaded, "even")
    assert even.feel == score.EVEN and not variant.as_scored
    thirds = [0.0, 0.6667, 1.3333, 1.6667, 2.3333, 3.0, 3.6667]
    assert beats(score.events(even), "bell", even.beat_s) == thirds
    flat, variant = score.variant_of(loaded, "flat")
    assert flat.voice("bell").steps == 16 and not flat.triplets and not variant.as_scored
    assert score.bar_one(flat, flat.voice("bell")) == "X . . x . . x x . . x . x . . x"
    assert len({loaded.digest(), even.digest(), flat.digest()}) == 3


@pytest.mark.req("REQ-BEN-06")
@pytest.mark.parametrize(
    ("benchmark_id", "digest"),
    [("BM-DRUM-01", "a04d3a85636c9254"), ("BM-BASS-01", "e7de9642d67aaa1b")],
)
def test_score_hashes_stay_put_when_a_new_field_keeps_its_default(
    benchmark_id: str, digest: str
) -> None:
    # Renders and Dean's verdicts are filed under these hashes, so a new score
    # field (feel, sidechain) must leave scores that don't use it hashed as before.
    assert score.load(benchmark_id).digest() == digest


@pytest.mark.req("REQ-BEN-13")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("feel = [41, 31, 28]", "feel = [41, 31]"), "BM-T3: feel needs 3 lengths above 0"),
        (("feel = [41, 31, 28]", "feel = [41, 0, 28]"), "feel needs 3 lengths above 0"),
        (("feel = [41, 31, 28]", 'feel = "swung"'), "feel needs 3 lengths above 0"),
        (("feel = [1, 1, 1]", "feel = [1, true, 1]"), "variant even: feel needs 3 lengths"),
        (('"X . x . x x . x . x . x"', '"X . x . x x . x . x"'), "a grid needs 16 symbols"),
        (('grid = "X . . x . . x x', 'grid = "X . . x . . x'), "a grid needs 16 symbols"),
    ],
)
def test_triplet_mistakes(change: tuple[str, str], message: str) -> None:
    with pytest.raises(ScoreError, match=message):
        score.load_text(TRIPLETS.replace(*change, 1))


@pytest.mark.req("REQ-BEN-13")
def test_only_drum_grids_go_triplet() -> None:
    triplet_gate = ACID.replace(
        'gate   = "x  x  x  .  x   x  x  x  x  x   .  x  x  x  x  x"', 'gate = "' + "x " * 12 + '"'
    )
    with pytest.raises(ScoreError, match="gate row needs 16 steps"):
        score.load_text(triplet_gate)
    changed = ACID + '[variants.tight]\nvoices = { bass = { grid = "' + "x " * 16 + '" } }\n'
    with pytest.raises(ScoreError, match="only a drum grid can change"):
        score.load_text(changed)


@pytest.mark.req("REQ-BEN-13")
def test_north_star_scores_carry_their_feel() -> None:
    afro_01, afro_02 = score.load("BM-AFRO-01"), score.load("BM-AFRO-02")
    assert afro_01.feel == score.EVEN and afro_01.project == afro_02.project == "North star"
    assert afro_02.feel == (41.0, 31.0, 28.0)  # Polak and London's average, as the spec says
    assert score.variant_of(afro_02, "even")[0].feel == score.EVEN
    flattened, _ = score.variant_of(afro_01, "flattened")
    assert {v.name: v.steps for v in flattened.voices} == {
        "kick": 16,
        "chh": 16,
        "bell": 16,
        "shaker": 16,
    }
    names = [v.name for v in score.all_variants(afro_02)]
    # A take per software, the even take, and the fresh take (REQ-BEN-16).
    assert names == ["renoise", "supercollider", "synth-kit", "even", "fresh-take"]
