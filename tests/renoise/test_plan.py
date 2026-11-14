from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mph.bench import score
from mph.renoise import luadata
from mph.renoise import plan as song_plan
from tests.renoise import lua

SHUFFLE = """
id = "T"
bpm = 120
bars = 1
swing = 0.6
tail_s = 0.5

[voices.chh]
grid = "x x . . . . . . . . . . . . . ."
sound = { role = "hat" }
chokes = ["ohh"]
seconds = 0.2

[voices.ohh]
grid = "o . . . . . . . . . . . . . . ."
sound = { role = "hat_open" }
"""


def test_place_splits_time_into_line_and_delay() -> None:
    step = 0.125
    assert song_plan.place(0.0, step) == (0, 0)
    assert song_plan.place(0.125 * 1.5, step) == (1, 128)
    assert song_plan.place(0.125 * 2.9999, step) == (3, 0)  # rounds up into the next line
    assert song_plan.placed_time(1, 128, step) == pytest.approx(0.1875)


def test_volume_and_tuning() -> None:
    assert song_plan.volume(1.0) == 0x80
    assert song_plan.volume(0.5) == 0x40
    assert song_plan.volume(1.7) == 0x80
    assert song_plan.tuning(0.0) == (0, 0)
    assert song_plan.tuning(-1.25) == (-1, -32)
    assert song_plan.tuning(2.999) == (3, 0)


@pytest.mark.req("REQ-REN-06")
def test_swing_goes_in_the_delay_column_and_chokes_become_note_offs() -> None:
    loaded = score.load_text(SHUFFLE)
    found = score.events(loaded)
    hats = song_plan.notes(loaded, found, "chh")
    # Step 1 is an offbeat: 60% through its 8th note is 0.2 of a line late.
    assert [(n["line"], n["delay"], n.get("off", False)) for n in hats] == [
        (0, 0, False),
        (1, 51, False),
        (2, 205, True),  # the 0.2 s length limit after the last hit
    ]
    opens = song_plan.notes(loaded, found, "ohh")
    # The closed hat at step 1 chokes the open hat; step 0 shares its line, so no off.
    assert [(n["line"], n["delay"], n.get("off", False)) for n in opens] == [
        (0, 0, False),
        (1, 51, True),
    ]
    assert opens[0]["volume"] == 0x66  # "o" is 80%


@pytest.mark.req("REQ-REN-01")
def test_song_plan_carries_tracks_tempo_and_renders() -> None:
    loaded = score.load_text(SHUFFLE)
    tracks = [
        song_plan.Track("chh", "/s/hat.wav", 0.5),
        song_plan.Track("ohh", "/s/open.wav", 2.0, semitones=0.5, slices=(0, 100)),
    ]
    song = song_plan.song(
        loaded,
        score.events(loaded),
        tracks,
        rate=96000,
        save="/r/T.xrns",
        renders=[{"file": "/r/a.wav"}],
    )
    assert song["bpm"] == 120
    assert song["lpb"] == 4
    assert song["lines"] == 20  # one bar plus 0.5 s of tail at 0.125 s a line
    assert song["rate"] == 96000
    tracks_plan = song["tracks"]
    assert isinstance(tracks_plan, list)
    second = tracks_plan[1]
    assert isinstance(second, dict)
    assert (second["transpose"], second["fine_tune"], second["slices"]) == (0, 64, [0, 100])
    assert song_plan.lines_for(0.0, 0.125) == 1


@pytest.mark.lua
@pytest.mark.req("REQ-REN-01")
@pytest.mark.req("REQ-REN-06")
def test_the_tool_builds_the_planned_song(tmp_path: Path) -> None:
    loaded = score.load_text(SHUFFLE.replace("bars = 1", "bars = 5"))
    hat, opened = tmp_path / "hat.wav", tmp_path / "open.wav"
    hat.write_bytes(b"x")
    opened.write_bytes(b"x")
    tracks = [
        song_plan.Track("chh", str(hat), 0.5),
        song_plan.Track("ohh", str(opened), 2.0, semitones=-1.25, slices=(0, 100)),
    ]
    found = [
        e if e.voice == "chh" else score.Event("ohh", e.step, e.time_s, 1.0, 2)
        for e in score.events(loaded)
    ]
    song = song_plan.song(loaded, found, tracks, rate=96000, save="x", renders=[])
    path = tmp_path / "plan.lua"
    path.write_text(luadata.document(song), "utf-8")

    built = lua.run("build", str(path))

    assert (built["bpm"], built["lpb"]) == (120, 4)
    assert built["patterns"] == [64, 20]  # 5 bars and a tail, 4 bars a pattern
    assert built["instruments"] == 2
    first, second = built["tracks"]
    assert first["name"] == "chh"
    assert first["sample"] == str(hat)
    assert first["volume"] == 0.5
    assert (first["volume_column"], first["delay_column"]) == (True, True)
    assert first["devices"][-1] == {"name": "Audio/Effects/Native/Digital Filter", "active": False}
    assert first["interpolation"] == 4  # sinc
    assert (second["transpose"], second["fine_tune"]) == (-1, -32)
    assert second["slices"] == [1, 101]  # Renoise counts frames from 1
    assert second["samples"] == 3  # the sample and its two slice aliases
    notes = [(n["track"], n["line"], n["note"], n["volume"], n["delay"]) for n in built["notes"]]
    assert (1, 1, 48, 0x66, 51) in notes  # swung hat
    assert (2, 0, 50, 0x80, 0) in notes  # slice 2 plays its alias's note
    assert (2, 1, 120, 255, 51) in notes  # choke: note-off in the delay column
    assert (1, 65, 48, 0x66, 51) in notes  # bar 5 lands in the second pattern


TRIPLETS = """
id = "T3"
bpm = 120
bars = 5
swing = 0.6
tail_s = 0.5
feel = [41, 31, 28]

[voices.chh]
grid = ". x . . . . . . . . . . . . . ."
sound = { role = "hat" }

[voices.bell]
grid = "X . x . x x . x . x . x"
sound = { role = "bell" }
"""


@pytest.mark.req("REQ-BEN-13")
@pytest.mark.req("REQ-REN-06")
def test_a_triplet_song_runs_at_12_lines_a_beat() -> None:
    loaded = score.load_text(TRIPLETS.replace("bars = 5", "bars = 1"))
    found = score.events(loaded)
    assert (song_plan.lpb(loaded), song_plan.pattern_lines(loaded)) == (12, 192)
    bell = [(n["line"], n["delay"]) for n in song_plan.notes(loaded, found, "bell")]
    # Pulses of a beat start on lines 0, 4 and 8 when even. The feel's long first
    # pulse moves the second to 4.92 lines and the third to 8.64: the delay column.
    assert bell == [(0, 0), (8, 164), (16, 236), (20, 164), (28, 236), (36, 0), (44, 164)]
    even = replace(loaded, feel=score.EVEN)
    lines = [n["line"] for n in song_plan.notes(even, score.events(even), "bell")]
    assert lines == [0, 8, 16, 20, 28, 36, 44]
    assert all(n["delay"] == 0 for n in song_plan.notes(even, score.events(even), "bell"))
    # A sixteenth is 3 lines; swing still moves it in the delay column (0.6 of 2 steps).
    assert [(n["line"], n["delay"]) for n in song_plan.notes(loaded, found, "chh")] == [(3, 154)]
    song = song_plan.song(loaded, found, [], rate=96000, save="x", renders=[])
    assert (song["lpb"], song["patterns"], song["lines"]) == (12, [{"lines": 60}], 60)
    straight = score.load_text(SHUFFLE)
    assert (song_plan.lpb(straight), song_plan.pattern_lines(straight)) == (4, 64)


@pytest.mark.lua
@pytest.mark.req("REQ-BEN-13")
def test_the_tool_builds_a_triplet_song(tmp_path: Path) -> None:
    loaded = score.load_text(TRIPLETS)
    hat, bell = tmp_path / "hat.wav", tmp_path / "bell.wav"
    hat.write_bytes(b"x")
    bell.write_bytes(b"x")
    tracks = [song_plan.Track("chh", str(hat), 1.0), song_plan.Track("bell", str(bell), 1.0)]
    song = song_plan.song(loaded, score.events(loaded), tracks, rate=96000, save="x", renders=[])
    path = tmp_path / "plan.lua"
    path.write_text(luadata.document(song), "utf-8")

    built = lua.run("build", str(path))

    assert (built["bpm"], built["lpb"]) == (120, 12)
    assert built["patterns"] == [192, 60]  # 5 bars and a tail, 4 bars a pattern
    notes = [(n["track"], n["line"], n["delay"]) for n in built["notes"]]
    assert (2, 8, 164) in notes  # the bell's third pulse, leaning late
    assert (2, 0, 0) in notes and (2, 192, 0) in notes  # bar 5 starts the second pattern
    assert (1, 3, 154) in notes  # a swung sixteenth
