from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

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


# --- sampled synths (REQ-REN-09) ------------------------------------------------------

ACID = """
id = "M"
bpm = 120
bars = 1

[voices.bass]
notes  = "C2 C2 C3 C3 F1 F1 F2 F1 G1 A1 G2 G1 C2 C2 C3 C2"
gate   = "x  .  x  -  x  .  .  .  x  x  .  .  .  .  .  ."
accent = ".  .  X  .  .  .  .  .  .  .  .  .  .  .  .  ."
slide  = ".  .  .  .  .  .  .  .  S  .  .  .  .  .  .  ."
sound = { synth = "mph_bass" }
"""


@pytest.mark.req("REQ-REN-09")
def test_each_key_plays_the_nearest_sample() -> None:
    assert song_plan.key_ranges([36]) == [(0, 119)]
    assert song_plan.key_ranges([36, 24, 29]) == [(0, 26), (27, 32), (33, 119)]
    assert song_plan.key_ranges([24, 25]) == [(0, 24), (25, 119)]


@pytest.mark.req("REQ-REN-09")
def test_a_glide_covers_its_interval_in_the_synths_glide_time() -> None:
    line_s = 0.0625  # 120 BPM at 8 lines a beat: a tick is 1/16 of that
    assert song_plan.glide_speed(12, 0.05, line_s) == 15  # 192 sixteenths over 12.8 ticks
    assert song_plan.glide_speed(-12, 0.05, line_s) == 15  # down as fast as up
    assert song_plan.glide_speed(2, 0.001, line_s) == 32  # under a tick: all in one
    assert song_plan.glide_speed(0, 0.05, line_s) == 1  # the same note: the slowest glide
    assert song_plan.glide_speed(48, 0.0, line_s) == 0xFF  # no glide time: at once
    assert song_plan.glide_speed(48, 0.001, line_s) == 0xFF  # as fast as 0Gxx goes


@pytest.mark.req("REQ-REN-09")
def test_melodic_notes_carry_accents_glides_and_note_offs() -> None:
    loaded = score.load_text(ACID)
    assert (song_plan.lpb(loaded), song_plan.seconds_per_line(loaded)) == (8, 0.0625)
    families = (
        song_plan.Family((song_plan.Zone("c2.wav", 24),)),
        song_plan.Family((song_plan.Zone("c3.wav", 36),), accent=True),
    )
    synth = song_plan.Synth(families, 0.02, 0.05)
    found = song_plan.melodic_notes(
        loaded, score.events(loaded), song_plan.Track("bass", "", 1.0, synth=synth)
    )
    shown = [
        (n["line"], n.get("note", "off" if n.get("off") else None), n.get("sound"), n.get("glide"))
        for n in found
    ]
    assert shown == [
        (0, 24, None, None),
        (1, "off", None, None),  # a plain note's gate: half a sixteenth
        (4, 36, 1, None),  # accented, and held through "-": the next note ends it
        (8, 17, None, None),
        (9, "off", None, None),
        (16, 19, None, None),  # slid: held its whole step, tied into the next
        (18, 21, None, song_plan.glide_speed(2, 0.05, 0.0625)),  # glides, no new note
        (19, "off", None, None),
    ]
    # A synth with no accent samples plays every note from its one instrument.
    plain = song_plan.Track("bass", "", 1.0, synth=replace(synth, families=families[:1]))
    notes = song_plan.melodic_notes(loaded, score.events(loaded), plain)
    assert not any(n.get("sound") for n in notes)


LEAD = """
[voices.lead]
notes  = "C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3"
gate   = "x  .  .  .  x  .  .  .  x  .  .  .  x  .  .  ."
accent = "X  .  .  .  .  .  .  .  .  .  .  .  .  .  .  ."
sound = { synth = "mph_acid" }
"""


@pytest.mark.req("REQ-REN-09")
def test_accent_instruments_come_after_every_track() -> None:
    loaded = score.load_text(ACID + LEAD)
    synth = song_plan.Synth(
        (
            song_plan.Family((song_plan.Zone("a.wav", 24),)),
            song_plan.Family((song_plan.Zone("b.wav", 36),), accent=True),
        )
    )
    tracks = [
        song_plan.Track("bass", "", 1.0, synth=synth),
        song_plan.Track("lead", "", 1.0, synth=synth),
    ]
    song = song_plan.song(loaded, score.events(loaded), tracks, rate=48000, save="x", renders=[])
    found = cast("list[dict[str, Any]]", song["tracks"])
    planned = [(t["instrument"], [m["instrument"] for m in t.get("more", [])]) for t in found]
    assert (planned, song["instruments"]) == ([(1, [3]), (2, [4])], 4)
    assert [t["more"][0]["name"] for t in found] == ["bass accent", "lead accent"]
    assert "mix" not in song  # no mix and no sidechain: no mixer to build


@pytest.mark.req("REQ-REN-09")
def test_samples_lowered_to_full_scale_get_their_gain_back_on_their_volume() -> None:
    loaded = score.load_text(ACID + LEAD)
    zones = (song_plan.Family((song_plan.Zone("a.wav", 24),)),)
    tracks = [
        song_plan.Track("bass", "", 0.25, synth=song_plan.Synth(zones, boost=7.5)),
        song_plan.Track("lead", "", 0.25, synth=song_plan.Synth(zones)),
    ]
    song = song_plan.song(loaded, score.events(loaded), tracks, rate=48000, save="x", renders=[])
    found = cast("list[dict[str, Any]]", song["tracks"])
    assert [t["volume"] for t in found] == [1.875, 0.25]  # 0.25 x 7.5, and 0.25 as it was


@pytest.mark.lua
@pytest.mark.req("REQ-REN-01")
def test_the_tool_adds_tracks_before_the_master(tmp_path: Path) -> None:
    # A new song has fewer tracks than a track's voices; a track added at the
    # master would be a send track, so the tool adds them at the front.
    hat = tmp_path / "hat.wav"
    hat.write_bytes(b"x")
    track: dict[str, luadata.Data] = {
        "sample": str(hat),
        "volume": 1.0,
        "transpose": 0,
        "fine_tune": 0,
        "slices": [],
    }
    plan: dict[str, luadata.Data] = {
        "bpm": 120,
        "lpb": 4,
        "lines": 16,
        "patterns": [{"lines": 16}],
        "rate": 48000,
        "save": "x",
        "renders": [],
        "tracks": [{**track, "name": f"voice {n}", "notes": []} for n in range(1, 15)],
    }
    path = tmp_path / "plan.lua"
    path.write_text(luadata.document(plan), "utf-8")
    built = lua.run("build", str(path))
    names = [(t["name"], t["type"]) for t in built["mixer"]]
    assert names == [*((f"voice {n}", 1) for n in range(1, 15)), ("Master", 2)]
