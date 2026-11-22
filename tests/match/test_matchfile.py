"""Match files (spec/components/matching.md, REQ-MAT-01)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from mph.match import fit, matchfile
from mph.match.matchfile import Knob, MatchError

EXAMPLES = Path(__file__).parents[2] / "examples" / "projects"

HORN = """
synth = "mph_horn"
score = "BM-LTFS-05"
voice = "horn"
why = "the song's own horn"
budget = 600
seed = 4
tail_s = 2.0
keep = ["bend"]

[reference]
hash = "4ed4ea4d21f7"
start_s = 18.0
end_s = 28.0
what = "the train horn in the song's horn stem"
licence = "private"

[play]
notes = ["E3", 196.0]
at_s = 0.5
length_s = 8.5

[knobs]
swell = [2, 6.5]
brass = [300, 3000, "log"]
bend = [-5, 0]

[params]
width = 0.5

[weights]
pitch = 2
"""


def write(tmp_path: Path, text: str, name: str = "horn.toml") -> Path:
    folder = tmp_path / "love-train" / matchfile.MATCHES
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text)
    return path


@pytest.mark.req("REQ-MAT-01")
def test_a_match_names_its_synth_reference_notes_and_knobs(tmp_path: Path) -> None:
    match = matchfile.load(write(tmp_path, HORN))
    assert (match.name, match.project, match.synth) == ("horn", "love-train", "mph_horn")
    assert (match.score, match.voice, match.budget, match.seed) == ("BM-LTFS-05", "horn", 600, 4)
    assert match.reference.hash == "4ed4ea4d21f7"
    assert match.reference.length_s == 10.0
    assert match.play.notes[0] == pytest.approx(164.81, abs=0.01)  # a name...
    assert match.play.notes[1:] == (196.0,)  # ...or Hz
    assert [k.name for k in match.knobs] == ["swell", "brass", "bend"]
    assert match.knobs[1] == Knob("brass", 300.0, 3000.0, log=True)
    assert match.params == (("width", 0.5),)
    assert (match.weight("pitch"), match.weight("mel")) == (2.0, 1.0)
    assert match.pinned == ("swell", "brass")  # bend is the score's to keep


@pytest.mark.req("REQ-MAT-01")
def test_knobs_turn_over_their_range_in_steps_or_in_ratios() -> None:
    swell = Knob("swell", 2.0, 6.0)
    brass = Knob("brass", 300.0, 3000.0, log=True)
    assert [swell.value(u) for u in (0.0, 0.5, 1.0, 1.5)] == [2.0, 4.0, 6.0, 6.0]
    assert brass.value(0.5) == pytest.approx(math.sqrt(300 * 3000))  # halfway in ratio
    assert brass.unit(brass.value(0.3)) == pytest.approx(0.3)
    assert (swell.unit(4.0), swell.unit(9.0), brass.unit(0.0)) == (0.5, 1.0, 0.0)


@pytest.mark.req("REQ-MAT-01")
def test_a_patch_is_the_held_params_and_the_knobs(tmp_path: Path) -> None:
    match = matchfile.load(write(tmp_path, HORN))
    patch = match.patch([0.5, 0.0, 1.0])
    assert patch == {"width": 0.5, "swell": 4.25, "brass": pytest.approx(300.0), "bend": 0.0}
    assert match.units({"swell": 4.25, "brass": 3000.0}) == [0.5, 1.0, 0.5]  # bend: the middle


@pytest.mark.req("REQ-MAT-01")
@pytest.mark.parametrize(
    ("change", "says"),
    [
        (('synth = "mph_horn"', ""), "say which synth"),
        (('synth = "mph_horn"', 'synth = "mph_nothing"'), "no SynthDef called mph_nothing"),
        (("seed = 4", "seed = 4\ncolour = 1"), "unknown keys ['colour']"),
        (('brass = [300, 3000, "log"]', "brass = [3000, 300]"), "knob brass needs a range"),
        (('brass = [300, 3000, "log"]', 'brass = [0, 3000, "log"]'), "above 0"),
        (('brass = [300, 3000, "log"]', 'brass = [300, 3000, "steps"]'), "knob brass"),
        (('brass = [300, 3000, "log"]', "brass = 300"), "knob brass"),
        (("swell = [2, 6.5]", "colour = [0, 1]"), "mph_horn has no control colour"),
        (("swell = [2, 6.5]", "freq = [100, 200]"), "freq is set by every render"),
        (("width = 0.5", "colour = 0.5"), "no control colour"),
        (('hash = "4ed4ea4d21f7"', ""), "by hash or by path"),
        (('hash = "4ed4ea4d21f7"', 'hash = "4e"\npath = "horn.wav"'), "by hash or by path"),
        (("end_s = 28.0", "end_s = 18.0"), "start_s before end_s"),
        (('notes = ["E3", 196.0]', "notes = []"), "needs notes"),
        (('notes = ["E3", 196.0]', 'notes = ["H9"]'), "[play]"),
        (("length_s = 8.5", "length_s = 0"), "length_s above 0"),
        (("length_s = 8.5", "length_s = 9.6"), "past the reference's span"),
        (("pitch = 2", "colour = 2"), "the views are"),
    ],
)
def test_a_match_says_what_is_wrong_with_it(
    tmp_path: Path, change: tuple[str, str], says: str
) -> None:
    old, new = change
    assert old in HORN
    with pytest.raises(MatchError) as caught:
        matchfile.load(write(tmp_path, HORN.replace(old, new, 1)))
    assert says in str(caught.value)


@pytest.mark.req("REQ-MAT-01")
def test_an_unreadable_match_says_so(tmp_path: Path) -> None:
    with pytest.raises(MatchError, match=r"horn\.toml"):
        matchfile.load(write(tmp_path, "synth = "))
    with pytest.raises(MatchError, match=r"missing\.toml"):
        matchfile.load(tmp_path / "missing.toml")
    with pytest.raises(MatchError, match="at least one knob"):
        matchfile.load(write(tmp_path, HORN.split("[knobs]", maxsplit=1)[0]))


@pytest.mark.req("REQ-MAT-01")
def test_a_table_must_be_a_table(tmp_path: Path) -> None:
    with pytest.raises(MatchError, match=r"knobs must be a table, like \[knobs\]"):
        matchfile.parse({"synth": "mph_horn", "knobs": 3}, tmp_path / "horn.toml")


@pytest.mark.req("REQ-MAT-01")
def test_a_match_outside_a_matches_folder_belongs_to_its_own_folder(tmp_path: Path) -> None:
    path = tmp_path / "loose" / "horn.toml"
    path.parent.mkdir()
    path.write_text(HORN)
    assert matchfile.load(path).project == "loose"


@pytest.mark.req("REQ-MAT-01")
def test_the_synths_controls_come_from_its_synthdef() -> None:
    controls = matchfile.synth_controls("mph_horn")
    assert controls["brass"] == 1300.0 and controls["swell"] == 4.5
    assert {"far", "gone", "rise", "fade"} <= set(controls)  # the swell's shape (#68)


@pytest.mark.req("REQ-MAT-01")
@pytest.mark.parametrize(
    "path", sorted(EXAMPLES.glob("*/matches/*.toml")), ids=lambda p: f"{p.parents[1].name}/{p.stem}"
)
def test_the_example_matches_load_and_reach_their_baseline(path: Path) -> None:
    match = matchfile.load(path)
    base = {**matchfile.synth_controls(match.synth), **fit.baseline(match)}
    outside = [k.name for k in match.knobs if not k.low <= base[k.name] <= k.high]
    assert outside == []  # the hand-made patch is one the search can reach
    assert match.reference.hash  # by hash: the repo holds no audio (REQ-ENG-10)
