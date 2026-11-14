"""Arranged scores: sections, clips of a recording and excerpts (spec/components/tracks.md).

A small track, eight bars at 120 BPM over a recording of noise, runs through
the score rules, the Renoise plan, the Lua build and both engines' fakes, so
CI covers REQ-TRK-01 to 04 without real apps. The remix scores themselves
(spec/tracks/love-train.md) load here too, and their blocks are checked
against the whole track.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph.bench import audio, checks, ideas, kit, render, sc, score
from mph.bench.score import Clip, Play, Section
from mph.catalogue import db
from mph.renoise import bridge, luadata
from mph.renoise import plan as song_plan
from tests.bench.fake_renoise import FakeRenoise
from tests.bench.test_render import add_sample, cfg_for, renoise_ready, runner
from tests.renoise import lua

FAKE_HASH = "ab" * 32  # a recording that only has to be pinned, not found
INTRO = '{ name = "Intro", bars = [1, 2], play = ["kick", "hat"] }'
TRACK = """
id = "BM-TT"
title = "A test track"
bpm = 120
bars = 8
swing = 0.58
sections = [
  {intro},
  {{ name = "Drop", bars = [3, 4], play = ["song:a"] }},
  {{ name = "Drop", bars = [5, 6], play = ["song", "kick"] }},
  {{ name = "Out", bars = [7, 8], play = ["kick", "hat", "song:b"] }},
]

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = {{ role = "kick" }}

[voices.hat]
grid = "x x x x x x x x x x x x x x x x"
sound = {{ role = "hat" }}
humanise = 0.1

[voices.song]
sound = {{ role = "track", hash = "{song}", why = "a recording to cut" }}

[voices.song.clips]
a = [1, 4]
b = [9, 10]
c = [5, 6]
"""
SONG_SECONDS = 24.0  # 12 bars: clip b, bars 9 and 10, ends at 20 s


def track_text(song: str = FAKE_HASH, intro: str = INTRO) -> str:
    return TRACK.format(song=song, intro=intro)


def track(song: str = FAKE_HASH, intro: str = INTRO) -> score.Score:
    return score.load_text(track_text(song, intro))


def changed(old: str, new: str, song: str = FAKE_HASH) -> score.Score:
    text = track_text(song)
    assert old in text, old
    return score.load_text(text.replace(old, new))


def bar_of(event: score.Event, voice: score.Voice) -> int:
    return event.step // voice.steps + 1


def own(found: list[score.Event], voice: str) -> list[score.Event]:
    return [e for e in found if e.voice == voice]


def shifted(found: list[score.Event], bars: int, steps: int, bar_s: float) -> list[score.Event]:
    return [
        replace(e, step=e.step - bars * steps, time_s=round(e.time_s - bars * bar_s, 9))
        for e in found
    ]


def rounded(found: list[score.Event]) -> list[score.Event]:
    return [replace(e, time_s=round(e.time_s, 9)) for e in found]


# --- sections (REQ-TRK-01) -----------------------------------------------------------------


@pytest.mark.req("REQ-TRK-01")
def test_sections_say_which_voices_play_in_which_bars() -> None:
    loaded = track()
    assert [(s.name, s.first, s.last) for s in loaded.sections] == [
        ("Intro", 1, 2),
        ("Drop", 3, 4),
        ("Drop", 5, 6),
        ("Out", 7, 8),
    ]
    found = score.events(loaded)
    kick, hat = loaded.voice("kick"), loaded.voice("hat")
    assert sorted({bar_of(e, kick) for e in own(found, "kick")}) == [1, 2, 5, 6, 7, 8]
    assert sorted({bar_of(e, hat) for e in own(found, "hat")}) == [1, 2, 7, 8]
    # The patterns run on underneath: the hats come back in bar 7 with the
    # velocities and humanising they have there when every bar plays.
    every_bar = score.events(replace(loaded, sections=()))
    assert own(found, "hat") == [e for e in own(every_bar, "hat") if bar_of(e, hat) in (1, 2, 7, 8)]


@pytest.mark.req("REQ-TRK-01")
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("bars = [3, 4]", "bars = [4, 4]", "'Drop' starts at bar 4, .* should start at bar 3"),
        ("bars = [3, 4]", "bars = [2, 4]", "'Drop' starts at bar 2, .* should start at bar 3"),
        ("bars = [7, 8]", "bars = [7, 7]", "the sections end at bar 7, not 8"),
        ('play = ["song", "kick"]', 'play = ["song", "snare"]', "names an unknown voice 'snare'"),
        ('play = ["song", "kick"]', 'play = ["kick", "kick"]', "lists a voice twice"),
        ('{ name = "Out", ', '{ name = " ", ', "section 4 needs a name, its bars and the voices"),
        ("bars = [7, 8]", "bars = [8, 7]", "section 4 \\(Out\\) needs a first and last bar"),
        ('play = ["song", "kick"]', 'play = "kick"', "section 3: play lists voice names"),
    ],
)
def test_sections_follow_each_other_and_name_their_voices(old: str, new: str, message: str) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed(old, new)


# --- clips (REQ-TRK-02) --------------------------------------------------------------------


@pytest.mark.req("REQ-TRK-02")
def test_a_section_cues_a_clip_that_plays_until_something_stops_it() -> None:
    loaded = track()
    # a plays on through the section that lists the song on its own; b is cut by the end.
    assert score.plays(loaded) == [Play("song", 0, 3, 4), Play("song", 1, 7, 2)]
    clips = [e for e in score.events(loaded) if e.voice == "song"]
    assert [(e.time_s, e.velocity, e.length_s, e.clip) for e in clips] == [
        (4.0, 1.0, 8.0, 1),
        (12.0, 1.0, 4.0, 2),
    ]
    left_out = changed('play = ["song", "kick"]', 'play = ["kick"]')
    assert score.plays(left_out)[0] == Play("song", 0, 3, 2)  # a section left it out
    recued = changed('play = ["song", "kick"]', 'play = ["song:c", "kick"]')
    assert score.plays(recued)[:2] == [Play("song", 0, 3, 2), Play("song", 2, 5, 2)]
    longer = changed("b = [9, 10]", "b = [9, 12]")
    assert score.plays(longer)[-1] == Play("song", 1, 7, 2)  # the end of the score
    # Its place in the recording: bar 1 starts at the first sample, or at bar_one_s.
    song = loaded.voice("song")
    assert score.clip_start_s(loaded, song, song.clips[1]) == 16.0
    later = replace(song, bar_one_s=0.5)
    assert score.clip_start_s(loaded, later, song.clips[1]) == 16.5


@pytest.mark.req("REQ-TRK-02")
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (', hash = "' + FAKE_HASH + '"', "", "a clip voice pins its recording by its hash"),
        ("[voices.song.clips]", "humanise = 0.1\n[voices.song.clips]", "takes no notes, veloc"),
        ("[voices.song.clips]", 'grid = "x"\n[voices.song.clips]', "exactly one of grid, pul"),
        ("[voices.song.clips]", "bar_one_s = -1.0\n[voices.song.clips]", "bar_one_s can't be"),
        ("c = [5, 6]", '"c:d" = [5, 6]', "a clip's name can't hold ':'"),
        ("c = [5, 6]", "c = [6, 5]", "voice song, clip c needs a first and last bar"),
        ('play = ["kick", "hat"]', 'play = ["kick:a", "hat"]', "cues 'a' on kick, which plays no"),
        ('play = ["song:a"]', 'play = ["song:z"]', "cues 'z', which isn't one of song's clips"),
        ('play = ["song:a"]', 'play = ["song"]', "'Drop' lets song carry on, .* like song:a"),
        ("[voices.kick]", '[voices.kick]\nchokes = ["song"]', "kick chokes song, but a clip"),
    ],
)
def test_clip_voices_play_their_recording_as_it_is(old: str, new: str, message: str) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed(old, new)


@pytest.mark.req("REQ-TRK-02")
def test_clips_need_sections_and_get_no_synth_kit_take() -> None:
    text = track_text()
    plain = text[: text.index("sections = [")] + text[text.index("[voices.kick]") :]
    with pytest.raises(score.ScoreError, match="sections cue clips, so song needs sections"):
        score.load_text(plain)
    with pytest.raises(score.ScoreError, match="clips names bars, like"):
        score.load_text(text[: text.index("[voices.song.clips]")] + "clips = 4\n")
    assert [v.name for v in score.all_variants(track())] == ["renoise", "supercollider"]


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def add_song(
    conn: sqlite3.Connection, tmp_path: Path, bpm: str = "120", seconds: float = SONG_SECONDS
) -> str:
    """A recording of noise in the catalogue: every bar of it sounds different."""
    tags = [("bpm", bpm)] if bpm else []
    name = f"Song {bpm or 'untimed'}.wav"
    return add_sample(conn, tmp_path / "lib", name, tags, form="track", seconds=seconds, noise=True)


@pytest.mark.req("REQ-TRK-02")
def test_a_clip_voice_takes_its_recording_or_stops(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    song = add_song(conn, tmp_path)
    chosen = {c.voice: c for c in kit.choose(track(song), conn, data)}
    assert (chosen["song"].digest, chosen["song"].bpm) == (song, 120.0)
    assert chosen["song"].reason == "picked: a recording to cut"
    assert chosen["kick"].synthdef == "mph_kick"  # the kit searches and falls back as ever
    with pytest.raises(kit.KitError, match="plays clips of abababababab, which isn't in the cat"):
        kit.choose(track(), conn, data)
    with pytest.raises(kit.KitError, match=f"{song[:12]}, which needs the catalogue"):
        kit.choose(track(song), None, data)
    untimed = add_song(conn, tmp_path, bpm="")
    with pytest.raises(kit.KitError, match="has no known tempo to place its clips by"):
        kit.choose(track(untimed), conn, data)


@pytest.mark.req("REQ-TRK-02")
def test_clips_play_at_the_recordings_tempo_and_inside_it(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    fast = track(add_song(conn, tmp_path, bpm="121"))
    with pytest.raises(sc.RenderError, match="never stretched, so the score must be 121 BPM, not"):
        render.clip_rates(fast, kit.choose(fast, conn, data))
    short = add_song(conn, tmp_path, bpm="120.004", seconds=16.0)  # 8 bars, a hair off 120
    inside = changed("b = [9, 10]", "b = [7, 8]", short)
    assert render.clip_rates(inside, kit.choose(inside, conn, data)) == {"song": 44100}
    past = track(short)
    with pytest.raises(
        sc.RenderError, match=r"clip b runs to 20.00 s, past the end .* \(16.00 s\)"
    ):
        render.clip_rates(past, kit.choose(past, conn, data))


# --- excerpts (REQ-TRK-03) -----------------------------------------------------------------


@pytest.mark.req("REQ-TRK-03")
def test_an_excerpt_plays_the_tracks_own_bars() -> None:
    whole = track()
    part = score.excerpt(whole, 4, 7, id="BM-TT-X", title="Bars 4-7")
    assert (part.id, part.title, part.bars, part.lead_bars) == ("BM-TT-X", "Bars 4-7", 4, 3)
    assert part.excerpt == score.Excerpt("BM-TT", 4, 7)
    assert part.sections == (
        Section("Drop", 1, 1, ("song:a from bar 2",)),  # a is playing: cued from where it is
        Section("Drop", 2, 3, ("song", "kick")),
        Section("Out", 4, 4, ("kick", "hat", "song:b")),
    )
    # Only the clips it plays, in the order it cues them; c never plays here.
    assert part.voice("song").clips == (Clip("a from bar 2", 2, 4), Clip("b", 9, 10))
    assert score.plays(part) == [Play("song", 0, 1, 3), Play("song", 1, 4, 1)]
    # Each pattern plays exactly what the track plays in those bars.
    steps, bar_s = whole.voice("kick").steps, whole.bar_s
    for name in ("kick", "hat"):
        voice = whole.voice(name)
        inside = [e for e in own(score.events(whole), name) if 4 <= bar_of(e, voice) <= 7]
        assert rounded(own(score.events(part), name)) == shifted(inside, 3, steps, bar_s)
    resting = score.excerpt(whole, 1, 2, id="BM-TT-I")
    assert resting.voice("song").clips == (Clip("a", 1, 4),)  # still a clip voice, resting
    assert score.plays(resting) == []
    assert score.all_variants(part) == score.all_variants(whole)


@pytest.mark.req("REQ-TRK-03")
def test_only_the_tracks_own_bars_shape_an_excerpts_hash() -> None:
    def cut(loaded: score.Score) -> str:
        return score.excerpt(loaded, 4, 7, id="BM-TT-X").digest()

    before = cut(track())
    assert cut(track(intro=INTRO.replace('"kick", "hat"', '"kick"'))) == before
    assert cut(changed("c = [5, 6]", "c = [3, 4]")) == before
    assert cut(changed("b = [9, 10]", "b = [10, 11]")) != before
    assert cut(changed('play = ["kick", "hat", "song:b"]', 'play = ["kick", "song:b"]')) != before
    assert score.excerpt(track(), 4, 6, id="BM-TT-X").digest() != before


@pytest.mark.req("REQ-TRK-03")
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('id = "X"\nbpm = 120\nexcerpt = { of = "BM-LTDJ-05", bars = [1, 4] }', "can't set bpm"),
        ('id = "X"\nexcerpt = { bars = [1, 4] }', 'needs an id, and excerpt = { of = "<id>"'),
        ('excerpt = { of = "BM-LTDJ-05", bars = [1, 4] }', "needs an id"),
        ('id = "X"\nexcerpt = { of = "BM-LTDJ-05", bars = [4] }', "X: the excerpt needs a first"),
        ('id = "X"\nexcerpt = { of = "BM-LTDJ-05", bars = [230, 250] }', "aren't all in BM-LTD"),
        ('id = "X"\nexcerpt = { of = "BM-LTDJ-01", bars = [1, 4] }', "BM-LTDJ-01 is an excerpt"),
        ('id = "X"\nexcerpt = { of = "BM-NONE", bars = [1, 4] }', "no score for BM-NONE"),
    ],
)
def test_an_excerpt_names_its_track_and_bars_and_nothing_else(text: str, message: str) -> None:
    with pytest.raises(score.ScoreError, match=message):
        score.load_text(text)


BLOCKS = {  # block: (its bars of the whole track, what its clips play, as (clip, bar, bars))
    "BM-LTDJ-01": ((25, 48), [("train", 5, 4), ("groove", 9, 16)]),
    "BM-LTDJ-02": ((65, 96), [("verse", 1, 16), ("chorus", 17, 16)]),
    "BM-LTDJ-03": (
        (125, 152),
        [("verse-2 from bar 81", 1, 4), ("verse-3", 5, 12), ("train", 17, 4), ("chorus", 21, 8)],
    ),
    "BM-LTDJ-04": ((177, 208), [("verse-4", 1, 16), ("outro", 17, 4), ("last-train", 29, 4)]),
}


@pytest.mark.req("REQ-TRK-03")
@pytest.mark.parametrize("block", sorted(BLOCKS))
def test_remix_ones_blocks_are_cut_from_the_whole_track(block: str) -> None:
    whole, part = score.load("BM-LTDJ-05"), score.load(block)
    (first, last), cues = BLOCKS[block]
    assert part.excerpt == score.Excerpt("BM-LTDJ-05", first, last)
    assert (part.bars, part.lead_bars, part.bpm, part.swing) == (
        last - first + 1,
        first - 1,
        120,
        0.58,
    )
    assert part.project == whole.project == "Love Train remix 1: Detroit x Jersey"
    song = part.voice("source")
    assert [(song.clips[p.clip].name, p.bar, p.bars) for p in score.plays(part)] == cues
    steps, bar_s = 16, whole.bar_s
    for voice in (v for v in whole.voices if not v.clips):
        inside = [
            e for e in own(score.events(whole), voice.name) if first <= bar_of(e, voice) <= last
        ]
        assert rounded(own(score.events(part), voice.name)) == shifted(
            inside, first - 1, steps, bar_s
        )


@pytest.mark.req("REQ-TRK-02")
def test_remix_ones_whole_track_is_a_re_edit_of_the_source() -> None:
    whole = score.load("BM-LTDJ-05")
    assert (whole.bars, whole.bpm, len(whole.sections)) == (240, 120, 22)
    assert whole.length_s == 481.0  # 8:00 and the tail
    source = whole.voice("source")
    assert source.sound.hash.startswith("406fc403707b")
    cued = [(source.clips[p.clip].name, p.bar) for p in score.plays(whole)]
    assert cued[0] == ("train", 29) and ("train", 141) in cued and cued[-1] == ("last-train", 205)
    assert [v.name for v in score.all_variants(whole)] == ["renoise", "supercollider"]


# --- sections in Renoise (REQ-TRK-04) ------------------------------------------------------


@pytest.mark.req("REQ-TRK-04")
def test_each_section_starts_a_named_pattern() -> None:
    loaded = track()
    lines = song_plan.lines_for(loaded.length_s, song_plan.seconds_per_line(loaded))
    assert song_plan.patterns(loaded, lines) == [
        {"lines": 32, "section": "Intro"},
        {"lines": 32, "section": "Drop"},
        {"lines": 32, "section": "Drop"},
        {"lines": 32, "section": "Out"},
        {"lines": 8},  # the tail
    ]
    long = replace(loaded, sections=(replace(loaded.sections[0], last=6), loaded.sections[3]))
    found = song_plan.patterns(long, lines)
    assert found[:3] == [
        {"lines": 64, "section": "Intro"},
        {"lines": 32},
        {"lines": 32, "section": "Out"},
    ]
    plain = score.load("BM-DRUM-02")
    assert all("section" not in p for p in song_plan.patterns(plain, 200))


@pytest.mark.req("REQ-TRK-04")
def test_a_clip_is_one_note_and_a_clip_cut_short_gets_a_note_off() -> None:
    loaded = track()
    notes = song_plan.notes(loaded, score.events(loaded), "song")
    assert notes == [
        {"line": 32, "delay": 0, "volume": 128, "clip": 1},
        {"line": 96, "delay": 0, "volume": 128, "clip": 2},
    ]
    left_out = changed('play = ["song", "kick"]', 'play = ["kick"]')
    offs = [n for n in song_plan.notes(left_out, score.events(left_out), "song") if n.get("off")]
    assert offs == [{"line": 64, "delay": 0, "volume": 0, "off": True}]
    part = score.excerpt(loaded, 4, 7, id="BM-TT-X")
    ends = song_plan.notes(part, score.events(part), "song")
    assert [(n["line"], n.get("clip"), n.get("off")) for n in ends] == [
        (0, 1, None),
        (48, 2, None),
        (64, None, True),  # b stops with the excerpt, not in its tail
    ]
    too_many = song_plan.Track("song", "", 1.0, clips=tuple(("x.wav", str(n)) for n in range(73)))
    with pytest.raises(ValueError, match="song has 73 clips; a Renoise instrument fits 72"):
        song_plan.song(loaded, score.events(loaded), [too_many], rate=48000, save="x", renders=[])


def _build(tmp_path: Path, loaded: score.Score, tracks: list[song_plan.Track]) -> dict[str, object]:
    song = song_plan.song(loaded, score.events(loaded), tracks, rate=48000, save="x", renders=[])
    path = tmp_path / "plan.lua"
    path.write_text(luadata.document(song), "utf-8")
    built: dict[str, object] = lua.run("build", str(path))
    return built


def _files(tmp_path: Path, *names: str) -> list[str]:
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(str(path))
    return paths


@pytest.mark.lua
@pytest.mark.req("REQ-TRK-02")
@pytest.mark.req("REQ-TRK-04")
def test_the_tool_names_sections_and_puts_each_clip_on_its_own_key(tmp_path: Path) -> None:
    loaded = track()
    kick, hat, a, b, c = _files(tmp_path, "kick.wav", "hat.wav", "a.wav", "b.wav", "c.wav")
    clips = ((a, "a"), (b, "b"), (c, "c"))
    tracks = [
        song_plan.Track("kick", kick, 1.0),
        song_plan.Track("hat", hat, 1.0),
        song_plan.Track("song", "", 0.5, clips=clips),
    ]
    built = _build(tmp_path, loaded, tracks)
    assert built["patterns"] == [32, 32, 32, 32, 8]
    assert built["sections"] == [
        {"position": 1, "line": 0, "name": "Intro"},
        {"position": 2, "line": 32, "name": "Drop"},
        {"position": 3, "line": 64, "name": "Drop"},
        {"position": 4, "line": 96, "name": "Out"},
    ]
    song = built["tracks"][2]  # type: ignore[index]
    assert song["zones"] == [
        {
            "name": name,
            "sample": path,
            "base_note": key,
            "note_range": [key, key],
            "volume": 0.5,
            "autoseek": True,
            "autofade": True,
        }
        for (path, name), key in zip(clips, (48, 49, 50), strict=True)
    ]
    notes = [(n["line"], n["note"]) for n in built["notes"] if n["track"] == 3]  # type: ignore[attr-defined]
    assert notes == [(32, 48), (96, 49)]  # C-4 cues a, C#4 cues b
    # The template song's clip track loads nothing: clips have no templates.
    empty = _build(tmp_path, loaded, [song_plan.Track("song", "", 1.0, clips=())])
    assert not empty["tracks"][0]["zones"]  # type: ignore[index]


# --- both engines --------------------------------------------------------------------------


def _scores(monkeypatch: pytest.MonkeyPatch, song: str) -> dict[str, score.Score]:
    whole = track(song)
    found = {
        "BM-TT": whole,
        "BM-TT-X": score.excerpt(whole, 4, 7, id="BM-TT-X"),
        "BM-TT-Y": score.excerpt(whole, 3, 4, id="BM-TT-Y"),  # clips only: the kit rests
    }
    monkeypatch.setattr(render, "load", found.__getitem__)
    return found


def _measured(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {m["name"]: m for m in report["measurements"]}  # type: ignore[attr-defined]


OFFSET = "clips: song worst offset from the recording (ms)"
MATCH = "clips: song worst match to the recording (dB left after cancelling)"


@pytest.mark.req("REQ-TRK-02")
@pytest.mark.req("REQ-TRK-03")
@pytest.mark.parametrize("benchmark_id", ["BM-TT", "BM-TT-X", "BM-TT-Y"])
def test_supercollider_plays_clips_from_the_recording(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark_id: str
) -> None:
    _scores(monkeypatch, add_song(conn, tmp_path))
    cfg = cfg_for(tmp_path)
    outcome = render.run(benchmark_id, cfg, runner(), conn, engine="supercollider")
    report = outcome.report
    failures = [m for m in _measured(report).values() if not m["passed"]]
    assert failures == []
    assert _measured(report)[OFFSET]["value"] <= 0.1  # type: ignore[operator]
    assert _measured(report)[MATCH]["value"] <= -40  # type: ignore[operator]
    song = next(k for k in report["kit"] if k["voice"] == "song")  # type: ignore[attr-defined]
    assert (song["sound"], song["gain_db"], song["why"]) == (
        "Song 120.wav",
        0.0,
        "picked: a recording to cut",
    )
    if benchmark_id == "BM-TT":
        assert report["sections"][1] == {"name": "Drop", "bars": [3, 4]}  # type: ignore[index]
    else:
        assert report["excerpt"]["of"] == "BM-TT"  # type: ignore[index]


LATE_S = 294 / 44100  # 6.67 ms: whole samples at 44.1 and 48 kHz, as an engine's error is


@pytest.mark.req("REQ-TRK-02")
@pytest.mark.parametrize(
    ("moved_s", "failing"),
    [(2.0, MATCH), (LATE_S, OFFSET)],  # the next bar of the recording; a cue 6.67 ms off
)
def test_a_clip_from_the_wrong_place_fails_its_check(
    conn: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    moved_s: float,
    failing: str,
) -> None:
    _scores(monkeypatch, add_song(conn, tmp_path))
    right = score.clip_start_s
    monkeypatch.setattr(sc, "clip_start_s", lambda s, v, c: right(s, v, c) + moved_s)
    outcome = render.run("BM-TT", cfg_for(tmp_path), runner(), conn, engine="supercollider")
    found = _measured(outcome.report)
    assert not outcome.passed
    assert not found[failing]["passed"]
    if failing == OFFSET:
        assert found[OFFSET]["value"] == pytest.approx(1000 * LATE_S, abs=0.01)
        assert found[MATCH]["passed"]  # the right bars, just late


@pytest.mark.lua
@pytest.mark.req("REQ-TRK-02")
@pytest.mark.req("REQ-TRK-03")
@pytest.mark.req("REQ-TRK-04")
def test_renoise_cuts_each_clip_once_and_plays_it_from_one_note(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scores(monkeypatch, add_song(conn, tmp_path))
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    cut: dict[str, soundfile._SoundFileInfo] = {}  # work/ is cleared once a render is done
    cut_clips = render.cut_clips

    def cut_and_look(setup: render.Setup) -> dict[str, list[Path]]:
        found = cut_clips(setup)
        cut.update(
            {str(p.relative_to(setup.folder)): soundfile.info(str(p)) for p in found["song"]}
        )
        return found

    monkeypatch.setattr(render, "cut_clips", cut_and_look)
    outcome = render.run("BM-TT", cfg, runner(), conn, home=home, clock=fake.clock())
    assert outcome.report["engine"] == "renoise"
    assert [m for m in _measured(outcome.report).values() if not m["passed"]] == []
    main, templates = fake.performed[0]["songs"]
    assert [p.get("section") for p in main["patterns"]] == ["Intro", "Drop", "Drop", "Out", None]
    song = main["tracks"][2]
    assert (song["sample"], [c["name"] for c in song["clips"]]) == ("", ["a", "b", "c"])
    assert [Path(c["file"]) for c in song["clips"]] == [
        outcome.folder / "work" / "clips" / "song" / f"0{n}.wav" for n in (1, 2, 3)
    ]
    sizes = [(c.samplerate, c.frames, c.subtype) for c in cut.values()]
    assert sizes == [(44100, bars * 2 * 44100, "FLOAT") for bars in (4, 2, 2)]  # a, b, c
    assert templates["tracks"][2]["clips"] == []  # nothing to load where nothing plays
    # Renoise gets time for every second it renders: the warm-up, two mixes and a
    # stem per voice of the song, then the templates' warm-up and render.
    song_s, template_s = 136 * 0.125, templates["lines"] * 0.125
    expected = render.RENOISE_TIMEOUT_S + 6 * song_s + 2 * template_s
    assert render.renoise_timeout_s(fake.performed[0]) == pytest.approx(expected)
    # An excerpt's song holds only the clips it plays.
    part = render.run("BM-TT-X", cfg, runner(), conn, home=home, clock=fake.clock())
    assert [m for m in _measured(part.report).values() if not m["passed"]] == []
    clips = fake.performed[1]["songs"][0]["tracks"][2]["clips"]
    assert [c["name"] for c in clips] == ["a from bar 2", "b"]


@pytest.mark.req("REQ-TRK-01")
def test_a_template_render_ignores_sections(tmp_path: Path) -> None:
    loaded = track()
    found = score.events(loaded)
    templates = render.template_events(loaded, found)
    assert {e.voice for e in templates} == {"kick", "hat"}  # clips are checked another way
    solo = render.template_score(loaded, templates)
    assert (solo.sections, solo.lead_bars) == ((), 0)
    assert render.template_score(loaded, []).tail_s == render.TEMPLATE_GAP_S


@pytest.mark.req("REQ-TRK-02")
def test_a_silent_stem_cancels_nothing_so_its_clip_fails() -> None:
    wanted = np.ones((100, 1), np.float32)
    lag, left, level = audio.aligned(np.zeros((1000, 2), np.float32), wanted, 400, 10)
    assert (left, level) == (0.0, -math.inf)
    found = checks.clips("song", [(3, 1000 * lag / 48000, left, level)])
    assert [(c.value, c.passed) for c in found][1] == (0.0, False)
    assert checks.clips("song", []) == []  # a clip voice that rests has nothing to check


@pytest.mark.req("REQ-TRK-03")
def test_chokes_and_humanising_rest_with_their_voices() -> None:
    block = score.load("BM-LTDJ-02")  # the source alone: the kit rests, the hats too
    found = score.events(block)
    assert {e.voice for e in found} == {"source"}
    silent = {v.name: np.zeros((48000, 2), np.float32) for v in block.voices}
    assert checks.chokes(block, found, silent, {}, 48000) == []
    resting = score.excerpt(track(), 3, 4, id="BM-TT-Y")
    assert checks.humanise(resting, {"kick": [], "hat": []}) == []


@pytest.mark.req("REQ-TRK-03")
def test_any_score_can_be_excerpted() -> None:
    whole = score.load("BM-DRUM-02")
    part = score.excerpt(whole, 2, whole.bars, id="BM-DRUM-02-X")
    assert (part.sections, part.lead_bars, part.bars) == ((), 1, whole.bars - 1)
    later = [e for e in score.events(whole) if e.time_s >= whole.bar_s - 1e-9]
    assert rounded(score.events(part)) == sorted(
        shifted(later, 1, 16, whole.bar_s), key=lambda e: (e.time_s, e.voice)
    )


@pytest.mark.req("REQ-TRK-03")
def test_the_listing_reads_the_track_specs(tmp_path: Path) -> None:
    found = {benchmark: project for benchmark, _, project in ideas.specced()}
    assert found["BM-LTDJ-05"] == found["BM-LTDJ-01"] == "Love Train remix 1: Detroit x Jersey"
    assert "BM-DRUM-02" in found
    assert ideas.specced(tmp_path / "missing.md") == []
