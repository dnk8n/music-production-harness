"""Phrase voices: written lines cued like clips (spec/components/tracks.md, REQ-TRK-08).

A small track, eight bars at 120 BPM with a sung lead of two phrases, runs
through the score rules, excerpts and both engines' fakes, so CI covers
REQ-TRK-08 without real apps. What the lead sings is test_singing.py's.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from mph.bench import audio, render, score
from mph.bench.score import Play, Section
from mph.proc import Result
from mph.renoise import bridge
from tests.bench.fake_renoise import FakeRenoise
from tests.bench.fake_sc import fake_scsynth
from tests.bench.test_render import cfg_for, conn, renoise_ready, runner
from tests.bench.test_tracks import own, rounded, shifted

__all__ = ["conn"]  # a fixture

TRACK = """
id = "BM-PH"
title = "A test track with phrases"
bpm = 120
bars = 8
sections = [
  { name = "Intro", bars = [1, 2], play = ["kick"] },
  { name = "Verse", bars = [3, 6], play = ["kick", "lead:call"] },
  { name = "Out", bars = [7, 8], play = ["kick", "lead"] },
]

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = { role = "kick", synth = "mph_kick" }

[voices.lead]
sound = { synth = "mph_singer" }
cues = [[5, "answer"], [7, "call"]]

[voices.lead.phrases.call]
gate = ". . . . x . . . x - - - . . x x | x - - - - - - - . . . . . . . ."
notes = "- - - - E4 - - - G4 - - - - - A4 G4 | E4 - - - - - - - - - - - - - - -"
accent = ". . . . . . . . X . . . . . . . | . . . . . . . . . . . . . . . ."
slide = ". . . . . . . . . . . . . . S . | . . . . . . . . . . . . . . . ."
syllables = "- - - - o - - - ye - - - - - a - | la - - - - - - - - - - - - - - -"

[voices.lead.phrases.answer]
gate = "x - - - x - - - . . . . . . . ."
notes = "C4 - - - D4 - - - - - - - - - - -"
syllables = "m - - - u - - - - - - - - - - -"
"""
STEP_S = 0.125  # a sixteenth at 120 BPM


def track(text: str = TRACK) -> score.Score:
    return score.load_text(text)


def changed(old: str, new: str) -> score.Score:
    assert old in TRACK, old
    return track(TRACK.replace(old, new))


def notes(found: list[score.Event]) -> list[tuple[int, int, str, bool, float]]:
    """Each of the lead's notes: its step, pitch, syllable, tie and length in steps."""
    return [
        (e.step, e.pitch, e.syllable, e.tie, round(e.length_s / STEP_S, 3))
        for e in own(found, "lead")
    ]


CALL = [  # the call from bar 1: o, a held and accented ye, a slide from A4 to G4, la
    (4, 64, "o", False, 0.5),
    (8, 67, "ye", False, 4.0),
    (14, 69, "a", True, 1.0),
    (15, 67, "a", False, 0.5),
    (16, 64, "la", False, 8.0),
]
ANSWER = [(0, 60, "m", False, 4.0), (4, 62, "u", False, 4.0)]


Note = tuple[int, int, str, bool, float]


def at(bar: int, line: list[Note]) -> list[Note]:
    return [
        (step + (bar - 1) * 16, pitch, sung, tie, held) for step, pitch, sung, tie, held in line
    ]


@pytest.mark.req("REQ-TRK-08")
def test_phrases_are_cued_like_clips_and_each_plays_once() -> None:
    loaded = track()
    lead = loaded.voice("lead")
    assert lead.melodic and [p.name for p in lead.phrases] == ["call", "answer"]
    assert (lead.phrases[0].bars, lead.phrases[1].bars) == (2, 1)
    # The section cues call at 3, the voice answer at 5, then call again at 7.
    assert score.plays(loaded) == [
        Play("lead", 0, 3, 2),
        Play("lead", 1, 5, 1),
        Play("lead", 0, 7, 2),
    ]
    # It rests in bar 6, between the answer and the second call.
    assert notes(score.events(loaded)) == [*at(3, CALL), *at(5, ANSWER), *at(7, CALL)]
    accented = [e.step for e in own(score.events(loaded), "lead") if e.accent]
    assert accented == [40, 104]
    # Its level is measured on the first note of its first phrase.
    first = score.first_note(loaded, lead)
    assert (first.pitch, first.syllable, first.step) == (64, "o", 4)
    assert score.all_variants(loaded) == score.standard_variants(loaded)
    assert [v.name for v in score.all_variants(loaded)] == ["renoise", "supercollider"]


@pytest.mark.req("REQ-TRK-08")
def test_a_phrase_stops_where_a_clip_would_and_a_held_note_ends_there() -> None:
    # The voice's own cue at 4 cuts the call short after one bar: its la never sings.
    cut = changed('cues = [[5, "answer"], [7, "call"]]', 'cues = [[4, "answer"], [7, "call"]]')
    assert score.plays(cut)[:2] == [Play("lead", 0, 3, 1), Play("lead", 1, 4, 1)]
    assert notes(score.events(cut))[:6] == [*at(3, CALL[:4]), *at(4, ANSWER)]
    # A section that leaves the voice out stops it, and a note held into it ends there.
    held = track(
        TRACK.replace(
            '{ name = "Out", bars = [7, 8], play = ["kick", "lead"] }',
            '{ name = "Out", bars = [7, 7], play = ["kick", "lead"] },\n'
            '  { name = "End", bars = [8, 8], play = ["kick"] }',
        ).replace('[[5, "answer"], [7, "call"]]', '[[5, "answer"], [7, "hold"]]')
        + "\n[voices.lead.phrases.hold]\n"
        'gate = "x - - - - - - - - - - - - - - - | - - - - - - - - - - - - - - - -"\n'
        'notes = "C4 - - - - - - - - - - - - - - - | - - - - - - - - - - - - - - - -"\n'
        'syllables = "o - - - - - - - - - - - - - - - | - - - - - - - - - - - - - - - -"\n'
    )
    assert score.plays(held)[-1] == Play("lead", 2, 7, 1)
    assert notes(score.events(held))[-1] == (96, 60, "o", False, 16.0)  # one bar, not two
    # And at the end of the score.
    ending = changed('[[5, "answer"], [7, "call"]]', '[[5, "answer"], [8, "call"]]')
    assert score.plays(ending)[-1] == Play("lead", 0, 8, 1)
    assert notes(score.events(ending))[-4:] == at(8, CALL[:4])


@pytest.mark.req("REQ-TRK-08")
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            'sound = { synth = "mph_singer" }',
            'sound = { synth = "mph_singer" }\nnotes = "C4"',
            "a phrase voice writes its gate, notes, accent, slide and syllables rows in each",
        ),
        (
            '[voices.lead.phrases.answer]\ngate = "x - - - x - - - . . . . . . . ."\n',
            "[voices.lead.phrases.answer]\n",
            "voice lead, phrase answer: a melodic voice needs a gate row",
        ),
        (
            "[voices.lead.phrases.answer]\n",
            "[voices.lead.phrases.answer]\nchord = [0, 4, 7]\n",
            "voice lead, phrase answer takes the rows accent, gate, notes, slide, "
            "syllables, not chord",
        ),
        (
            "[voices.lead.phrases.answer]",
            '[voices.lead.phrases."an:swer"]',
            "voice lead, phrase an:swer: a phrase's name can't hold ':'",
        ),
        (
            'notes = "C4 - - - D4',
            'notes = "C4 - - - D4 - - - - - - - - - - - | - - - - D4',
            "voice lead, phrase answer: notes needs 16 entries a bar, over as many bars as "
            "the gate",
        ),
        (
            'cues = [[5, "answer"], [7, "call"]]',
            'cues = [[5, "verse"], [7, "call"]]',
            "BM-PH: lead cues 'verse', which isn't one of its phrases",
        ),
        (
            'play = ["kick", "lead:call"]',
            'play = ["kick", "lead:verse"]',
            "section 'Verse' cues 'verse', which isn't one of lead's phrases",
        ),
        (
            'cues = [[5, "answer"], [7, "call"]]',
            'cues = [[5, "answer"], [5, "call"]]',
            "BM-PH: lead cues two phrases in one bar",
        ),
        (
            'cues = [[5, "answer"], [7, "call"]]',
            'cues = [[5, "answer"]]',
            "section 'Out' lets lead carry on, but none of its phrases is playing there",
        ),
    ],
)
def test_phrases_are_written_and_cued_as_a_melodic_voice_and_a_clip_are(
    old: str, new: str, message: str
) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed(old, new)


@pytest.mark.req("REQ-TRK-08")
def test_phrases_need_sections_and_a_table_each() -> None:
    bare = TRACK.split("sections", maxsplit=1)[0] + TRACK.split("]\n\n", maxsplit=1)[1]
    with pytest.raises(score.ScoreError, match="sections cue phrases, so lead needs sections"):
        track(bare.replace('cues = [[5, "answer"], [7, "call"]]\n', ""))
    with pytest.raises(score.ScoreError, match="phrases names lines, like"):
        track(
            'id = "BM-X"\nbpm = 120\nbars = 1\n'
            'sections = [{ name = "A", bars = [1, 1], play = ["lead"] }]\n'
            '[voices.lead]\nsound = { synth = "mph_singer" }\nphrases = "call"\n'
        )


@pytest.mark.req("REQ-TRK-03")
@pytest.mark.req("REQ-TRK-08")
def test_an_excerpt_keeps_the_first_phrase_and_the_ones_it_cues() -> None:
    whole = track()
    part = score.excerpt(whole, 4, 8, id="BM-PH-X")
    lead = part.voice("lead")
    # The call is playing at bar 4, so it is cued again from its second bar.
    assert part.sections[0] == Section("Verse", 1, 3, ("kick", "lead:call from bar 2"))
    assert [p.name for p in lead.phrases] == ["call", "call from bar 2", "answer"]
    assert lead.cues == ((2, "answer"), (4, "call"))
    assert score.plays(part) == [
        Play("lead", 1, 1, 1),
        Play("lead", 2, 2, 1),
        Play("lead", 0, 4, 2),
    ]
    # It plays exactly what the track plays in those bars, and is levelled on the same note.
    inside = [e for e in own(score.events(whole), "lead") if e.step >= 48]
    assert rounded(own(score.events(part), "lead")) == shifted(inside, 3, 16, whole.bar_s)
    assert score.first_note(part, lead) == score.first_note(whole, whole.voice("lead"))
    # One that cues nothing keeps the first phrase, and rests.
    resting = score.excerpt(whole, 1, 2, id="BM-PH-I")
    assert [p.name for p in resting.voice("lead").phrases] == ["call"]
    assert own(score.events(resting), "lead") == []


@pytest.mark.req("REQ-TRK-08")
@pytest.mark.req("REQ-BEN-19")
def test_an_excerpt_leaves_out_a_held_note_and_restarts_a_slid_one() -> None:
    # G4 slides on into bar 2's E4, which goes on singing a.
    tied = TRACK.replace(
        'slide = ". . . . . . . . . . . . . . S . | .',
        'slide = ". . . . . . . . . . . . . . S S | .',
    ).replace(
        'syllables = "- - - - o - - - ye - - - - - a - | la',
        'syllables = "- - - - o - - - ye - - - - - a - | -',
    )
    assert notes(score.events(track(tied)))[2:5] == [
        (46, 69, "a", True, 1.0),
        (47, 67, "a", True, 1.0),
        (48, 64, "a", False, 8.0),
    ]
    # From bar 2, E4 starts afresh, still on a.
    slid = score.excerpt(track(tied), 4, 8, id="BM-PH-X")
    assert notes(score.events(slid))[0] == (0, 64, "a", False, 8.0)
    # A note held over the bar line into the excerpt is left out, as a pattern's is.
    over = TRACK.replace(
        'gate = ". . . . x . . . x - - - . . x x | x',
        'gate = ". . . . x . . . x - - - . . x x | -',
    ).replace(
        'syllables = "- - - - o - - - ye - - - - - a - | la',
        'syllables = "- - - - o - - - ye - - - - - a - | -',
    )
    assert notes(score.events(track(over)))[3] == (47, 67, "a", False, 9.0)
    left = score.excerpt(track(over), 4, 8, id="BM-PH-X")
    assert notes(score.events(left))[0] == (16, 60, "m", False, 4.0)  # the answer, next


@pytest.mark.req("REQ-TRK-08")
def test_only_the_phrases_an_excerpt_plays_shape_its_hash() -> None:
    def cut(loaded: score.Score) -> str:
        return score.excerpt(loaded, 3, 4, id="BM-PH-Y").digest()

    before = cut(track())
    # The answer doesn't play in bars 3 and 4; the call does.
    assert cut(changed('notes = "C4 - - - D4', 'notes = "B3 - - - D4')) == before
    assert cut(changed("A4 G4 | E4", "A4 G4 | F4")) != before
    assert track().digest() != changed('notes = "C4 - - - D4', 'notes = "B3 - - - D4').digest()


def _scores(monkeypatch: pytest.MonkeyPatch) -> dict[str, score.Score]:
    whole = track()
    found = {"BM-PH": whole, "BM-PH-X": score.excerpt(whole, 4, 8, id="BM-PH-X")}
    monkeypatch.setattr(render, "load", found.__getitem__)
    return found


def _failures(report: dict[str, object]) -> list[dict[str, object]]:
    return [m for m in report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]


@pytest.mark.req("REQ-TRK-08")
@pytest.mark.parametrize("benchmark_id", ["BM-PH", "BM-PH-X"])
def test_supercollider_sings_the_phrases_where_they_are_cued(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark_id: str
) -> None:
    _scores(monkeypatch)
    outcome = render.run(benchmark_id, cfg_for(tmp_path), runner(), conn, engine="supercollider")
    assert _failures(outcome.report) == []
    names = {m["name"] for m in outcome.report["measurements"]}  # type: ignore[attr-defined]
    probed = {
        "aliasing: lead inharmonic energy (dB)",
        "slide: lead pitch error once a glide settles (cents)",
    }
    assert {"accent: lead louder (dB)", *probed} <= names


@pytest.mark.req("REQ-TRK-08")
@pytest.mark.req("REQ-REN-09")
def test_renoise_plays_the_phrases_from_an_instrument_per_syllable(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scores(monkeypatch)
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    outcome = render.run("BM-PH", cfg, runner(), conn, home=home, clock=fake.clock())
    assert outcome.report["engine"] == "renoise"
    assert _failures(outcome.report) == []
    song = fake.performed[0]["songs"][0]
    lead = next(t for t in song["tracks"] if t["name"] == "lead")
    assert lead["label"] == "lead o"
    assert [m["name"] for m in lead["more"]] == [
        "lead ye",
        "lead ye accent",
        "lead a",
        "lead la",
        "lead m",
        "lead u",
    ]


HOT = 8.0  # the talk box's samples peaked up to 7.4 times full scale


def _hot_scsynth(argv: Sequence[str]) -> Result:
    """scsynth for a synth that renders far past full scale, as the talk box does."""
    done = fake_scsynth(argv)
    if "-N" in argv:
        out = Path(argv[argv.index("-N") + 3])
        data, rate = audio.read(out)
        audio.write_float(out, data * HOT, rate)
    return done


@pytest.mark.req("REQ-REN-09")
def test_renoise_lowers_hot_samples_together_and_gives_the_gain_back(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scores(monkeypatch)
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    hot = runner(scsynth=_hot_scsynth)
    outcome = render.run("BM-PH", cfg, hot, conn, home=home, clock=fake.clock())
    # Clipped as they loaded, the accents would lose most of their lift (BM-LTFS-01).
    assert _failures(outcome.report) == []
    lead = next(t for t in fake.performed[0]["songs"][0]["tracks"] if t["name"] == "lead")
    files = [z["file"] for z in lead["zones"]] + [
        z["file"] for m in lead["more"] for z in m["zones"]
    ]
    peaks = [float(np.abs(audio.read(Path(f))[0]).max()) for f in files]
    assert max(peaks) == pytest.approx(1.0)  # the hottest sample at full scale...
    assert min(peaks) < 0.9  # ...and the others lowered with it, not each to full scale
    sounds = sorted((outcome.folder / "sounds" / "lead").glob("*.wav"))
    assert sorted(Path(f) for f in files) == sounds


WITH_BASS = (
    TRACK.replace('play = ["kick"] }', 'play = ["kick", "bass"] }')
    + """
[voices.bass]
notes = "C2 -  -  -  -  -  -  -  G1 -  -  -  -  -  -  -"
gate  = "x  -  -  -  .  .  .  .  x  -  -  -  .  .  .  ."
sound = { synth = "mph_bass" }
"""
)


@pytest.mark.req("REQ-REN-09")
def test_renoise_builds_a_block_where_a_singing_voice_rests(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A melodic voice that sings nothing in a block has nothing to sample, so
    its track is a plain, silent one, and the song still builds while another
    melodic voice plays (the group in the free remix's first block)."""
    whole = track(WITH_BASS)
    intro = score.excerpt(whole, 1, 2, id="BM-PH-I")  # the bass plays, the lead rests
    monkeypatch.setattr(render, "load", {"BM-PH-I": intro}.__getitem__)
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    outcome = render.run("BM-PH-I", cfg, runner(), conn, home=home, clock=fake.clock())
    assert outcome.report["engine"] == "renoise"
    assert _failures(outcome.report) == []
    tracks = {t["name"]: t for t in fake.performed[0]["songs"][0]["tracks"]}
    assert tracks["bass"]["zones"] and tracks["bass"]["notes"]
    assert tracks["lead"]["notes"] == []
    assert tracks["lead"]["sample"] and "zones" not in tracks["lead"]
