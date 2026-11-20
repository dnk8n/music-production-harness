"""A score's mix as a Renoise mixer plan (spec/components/renoise.md, REQ-REN-10, REQ-REN-11).

The plan is plain data for the bridge tool; ``test_mixer.py`` builds it in
the fake Renoise.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest

from mph.bench import score
from mph.renoise import mixplan
from mph.renoise import plan as song_plan
from tests.bench.test_mix import mixed

LINE_S = 0.125  # 120 BPM, 4 lines a beat
TPL = 16  # ticks a line
LEAD_S = LINE_S / TPL  # the duck is drawn one tick early


def slot(seconds: float) -> float:
    """Where an automation point at a time lands in the first pattern: the
    nearest 1/256 of a line, counted from 1."""
    return 1 + round(seconds / LINE_S * 256) / 256


def mixer_of(loaded: score.Score) -> dict[str, Any]:
    """The mixer at 4 lines a beat, in 4-bar patterns."""
    lines = song_plan.lines_for(loaded.length_s, LINE_S)
    lengths = [min(64, lines - start) for start in range(0, lines, 64)]
    found = mixplan.plan(
        loaded, score.events(loaded), lengths, line_s=LINE_S, lpb=4, bar_lines=16, tpl=TPL
    )
    assert found is not None
    return found


def params(device: dict[str, Any]) -> dict[str, Any]:
    return {p["names"][0]: (p["value"], p["unit"]) for p in device["params"]}


def lane(track: dict[str, Any], device: int) -> dict[str, Any]:
    return next(a for a in track["automation"] if a["device"] == device)


@pytest.mark.req("REQ-REN-10")
@pytest.mark.req("REQ-REN-11")
def test_a_mix_becomes_devices_send_tracks_and_a_master() -> None:
    mixer = mixer_of(mixed())
    kick, stab = mixer["tracks"]
    # The kick's strip only sets a level, so it keeps REQ-REN-06's spare filter.
    assert [(d["name"], d["role"]) for d in kick["devices"]] == [
        ("Filter", "spare"),
        ("Level", "strip"),
    ]
    assert params(kick["devices"][1]) == {"Gain": (-2.0, "dB")}
    # The ducked stab: its duck first, then its strip in order, then its sends.
    assert [(d["name"], d["role"]) for d in stab["devices"]] == [
        ("Duck", "duck"),
        ("Highpass", "strip"),
        ("Lowpass", "strip"),
        ("Level", "strip"),
        ("Send to echo", "send"),
        ("Send to hall", "send"),
    ]
    duck, highpass, lowpass, level, echo, hall = stab["devices"]
    # A plain filter is Butterworth 4n, 24 dB an octave; a resonant one keeps
    # Biquad, the model with a resonance knob, turned the strip's share of the way.
    assert params(highpass) == {
        "Type": ("HighPass", "choice"),
        "Model": ("Butterworth 4n", "preset"),
        "Cutoff": (200.0, "Hz"),
    }
    assert params(lowpass)["Model"] == ("Biquad", "preset")
    assert params(lowpass)["Cutoff"] == (1200.0, "Hz")  # the lane's first value
    assert params(lowpass)["Resonance"] == (30.0, "%")  # resonance 0.3: that share of the knob
    assert params(level) == {"Gain": (0.0, "dB"), "Panning": (0.25, "fraction")}  # pan -0.5
    assert params(echo)["Amount"] == (-90.0, "dB")  # the echo lane starts silent
    assert params(echo)["Receiver"] == ({"name": "echo", "index": 0}, "send")
    assert params(echo)["MuteSource"] == ("false", "preset")  # keep the source
    assert params(hall)["Receiver"] == ({"name": "hall", "index": 1}, "send")
    # The Duck steps to each gain at the tick that reads it: Renoise's smoothing
    # is off. Its gain is where its curve starts: see the next test.
    assert list(params(duck)) == ["SmoothParameterChanges", "Gain"]
    assert params(duck)["SmoothParameterChanges"] == ("false", "preset")
    # Sends are send tracks: the effect, fully wet, then the return strip.
    delay, reverb = mixer["sends"]
    assert delay["name"] == "echo" and [d["name"] for d in delay["devices"]] == [
        "echo",
        "Highpass",
        "Send to hall",
    ]
    tap = params(delay["devices"][0])
    assert delay["devices"][0]["path"] == mixplan.MULTITAP
    assert tap["Mute Dry"] == ("On", "choice")
    assert tap["T1 L Sync Time"] == (3, "number")  # 0.75 beats at 4 lines a beat
    assert tap["T1 L Feedback"] == (50.0, "%")
    assert tap["T1 Ping Pong"] == ("On", "choice")
    assert tap["T1 Flt Mode"] == ("In + Feed", "choice")  # each echo darker than the last
    assert tap["T1 Flt Freq."] == (2500.0, "Hz")
    assert tap["T2 Amount"] == (-90.0, "dB")
    assert params(delay["devices"][2])["Amount"] == (-12.0, "dB")
    hall_effect = params(reverb["devices"][0])
    assert reverb["devices"][0]["path"] == mixplan.REVERB
    assert hall_effect["Duration"] == (1500.0, "ms")
    assert hall_effect["Predelay"] == (20.0, "ms")
    assert (hall_effect["Dry Mix"], hall_effect["Wet Mix"]) == ((0.0, "%"), (100.0, "%"))
    assert [d["name"] for d in reverb["devices"]] == ["hall", "Level"]
    # The master's strip, on the master track.
    assert [(d["name"], d["role"]) for d in mixer["master"]["devices"]] == [
        ("Highpass", "master"),
        ("Level", "master"),
    ]
    assert mixplan.MODES == {
        "mix": ("duck", "strip", "send", "effect", "master"),
        "stem": ("duck",),
        "dry": (),
    }


@pytest.mark.req("REQ-REN-10")
def test_lanes_become_points_in_every_pattern() -> None:
    mixer = mixer_of(mixed())
    stab = mixer["tracks"][1]
    # The low-pass opens from 1200 to 4800 Hz between bars 1 and 3: a point
    # every beat (4 lines), evenly in octaves.
    opening = lane(stab, 3)
    assert opening["names"][0] == "Cutoff" and opening["unit"] == "Hz"
    first = opening["patterns"][0]["points"]
    assert first[:3] == [
        [1.0, 1200.0],
        [5.0, pytest.approx(1200 * 2 ** (2 / 8), abs=0.01)],
        [9.0, pytest.approx(1200 * 2 ** (4 / 8), abs=0.01)],
    ]
    assert [1 + 32, 4800.0] in first  # bar 3
    assert first[-1] == [pytest.approx(64 + 255 / 256), 4800.0]  # each pattern ends on its value
    # The echo send opens for half a beat at bar 2.5: jumps are two points
    # 1/256 of a line apart, and silence is written as -90 dB.
    throw = lane(stab, 5)["patterns"][0]["points"]
    at = 1 + 24  # bar 2.5
    assert [at - 1 / 256, -90.0] in throw and [at, 0.0] in throw
    assert [at + 4 - 1 / 256, 0.0] in throw and [at + 4, -90.0] in throw
    # The master's level fades over the last bar, into the second pattern.
    fade = mixer["master"]["automation"][0]
    assert fade["device"] == 2 and [p["pattern"] for p in fade["patterns"]] == [1, 2]
    assert fade["patterns"][1]["points"][0] == [1.0, pytest.approx(-6.0)]


@pytest.mark.req("REQ-REN-11")
def test_the_duck_dips_at_every_hit() -> None:
    mixer = mixer_of(mixed())
    duck = lane(mixer["tracks"][1], 1)
    assert duck["names"] == ["Gain"] and duck["unit"] == "dB"
    points = duck["patterns"][0]["points"]
    # Drawn one tick early (REQ-REN-11). The first kick is on the first line,
    # so the song starts in its dip, and the Duck's own gain starts there too.
    assert points[0][0] == 1.0 and -12 < points[0][1] < -11.5
    assert params(mixer["tracks"][1]["devices"][0])["Gain"] == (points[0][1], "dB")
    # The second kick, a beat in: 0 dB a tick before it, the full 12 dB 2 ms
    # later, then a half-cosine back over 400 ms, with a point every 1/32 of it.
    start = slot(0.5 - LEAD_S)
    assert [start, 0.0] in points
    assert [slot(0.502 - LEAD_S), pytest.approx(-12.0)] in points
    release = [p for p in points if start < p[0] <= slot(0.902 - LEAD_S)]
    assert len(release) == 1 + mixplan.RELEASE_POINTS
    assert release[-1][1] == 0.0
    assert all(b[1] >= a[1] for a, b in itertools.pairwise(release))
    # Where dips overlap, the deeper one wins (REQ-REN-11): a hit before the
    # last dip has come back starts its own, which keeps its release's shape.
    chain = score.Sidechain("kick", ("stab",), depth_db=12, release_ms=400)
    timeline = mixplan.Timeline((64,), LINE_S, 4, 16)
    curve = mixplan.duck_curve([0.0, 0.1], chain, timeline)

    def taken(dt: float) -> float:  # one hit's share taken off the level, in its release
        return (1 - 10 ** (-12 / 20)) * (1 + math.cos(math.pi * (dt - 0.002) / 0.4)) / 2

    def db(share: float) -> float:
        return 20 * math.log10(1 - share)

    assert curve.value(0.05 / LINE_S) == pytest.approx(db(taken(0.05)))  # the first dip
    assert curve.value(0.102 / LINE_S) == pytest.approx(-12.0)  # the second's floor
    assert curve.value(0.3 / LINE_S) == pytest.approx(db(taken(0.2)))  # then the second alone
    assert curve.value(0.3 / LINE_S) > db(taken(0.3) + taken(0.2)) + 1  # not added up


def test_a_score_without_a_mix_or_a_sidechain_has_no_mixer() -> None:
    plain = score.load_text(
        'id = "X"\nbpm = 120\nbars = 1\n'
        '[voices.kick]\ngrid = "x . . . x . . . x . . . x . . ."\nsound = { role = "kick" }\n'
    )
    assert (
        mixplan.plan(plain, score.events(plain), [16], line_s=LINE_S, lpb=4, bar_lines=16, tpl=TPL)
        is None
    )
    song = song_plan.song(plain, score.events(plain), [], rate=48000, save="x", renders=[])
    assert "mix" not in song and "modes" not in song
    assert (song["tpl"], song["headroom_db"]) == (16, 0.0)  # REQ-REN-12


def ducked(sidechain: str) -> score.Score:
    """A bar of four kicks ducking a bass, with ``sidechain``'s settings."""
    return score.load_text(
        'id = "X"\nbpm = 120\nbars = 1\n'
        '[voices.kick]\ngrid = "x . . . x . . . x . . . x . . ."\nsound = { role = "kick" }\n'
        f'[voices.bass]\nnotes = "{" ".join(["C2"] * 16)}"\ngate = "{" ".join("x" * 16)}"\n'
        'sound = { synth = "mph_bass" }\n'
        f'[sidechain]\ntrigger = "kick"\ntargets = ["bass"]\n{sidechain}\n'
    )


@pytest.mark.req("REQ-REN-11")
def test_a_song_with_a_duck_gets_lines_enough_for_its_release() -> None:
    # The Duck steps once a tick, so a song has enough lines a beat that its
    # release moves at most DUCK_TICK_STEP_DB a tick: at 120 BPM, 16 ticks a
    # line and 8 lines a beat, a tick is 3.9 ms.
    def song_lpb(sidechain: str) -> int:
        return song_plan.lpb(ducked(sidechain))

    remix = score.Sidechain("kick", ("bass",), depth_db=24, release_ms=430)
    assert mixplan.release_step_db(remix, 0.5 / 8 / 16) == pytest.approx(0.462, abs=0.001)
    assert mixplan.release_step_db(remix, 0.5 / 16 / 16) == pytest.approx(0.231, abs=0.001)
    assert song_lpb("depth_db = 24\nrelease_ms = 430") == 16  # the remix's duck
    assert song_lpb("depth_db = 12\nrelease_ms = 400") == 8  # gentle enough at 8
    assert song_lpb("depth_db = 0") == 8  # no duck at all
    # A duck too steep for even the finest grid gets the finest a pattern holds.
    assert song_lpb("depth_db = 48\nrelease_ms = 100") == 32
    assert mixplan.release_step_db(score.Sidechain("kick", ("bass",), depth_db=0), 0.001) == 0.0


@pytest.mark.req("REQ-REN-11")
def test_the_duck_comes_back_in_small_steps_after_a_pushed_kick() -> None:
    # The remix's duck, with a kick pushed onto the last eighth of a bar, as
    # Renoise reads it: once a tick, along straight lines between the points,
    # a pattern a bar. Each dip comes back no faster than one release does:
    # added up, the dips after the push rose about 0.8 dB a tick (2026-11-20).
    loaded = score.load_text(
        'id = "X"\nbpm = 120\nbars = 2\n'
        '[voices.kick]\ngrid = "x . . . x . . . x . . . x . x . '
        '| x . . . x . . . x . . . x . . ."\nsound = { role = "kick" }\n'
        f'[voices.bass]\nnotes = "{" ".join(["C2"] * 16)}"\ngate = "{" ".join("x" * 16)}"\n'
        'sound = { synth = "mph_bass" }\n'
        '[sidechain]\ntrigger = "kick"\ntargets = ["bass"]\ndepth_db = 24\nrelease_ms = 430\n'
    )
    lpb = song_plan.lpb(loaded)
    lengths = [4 * lpb, 4 * lpb]
    found = mixplan.plan(
        loaded,
        score.events(loaded),
        lengths,
        line_s=loaded.beat_s / lpb,
        lpb=lpb,
        bar_lines=4 * lpb,
        tpl=TPL,
    )
    assert found is not None and lpb == 16
    mixer: dict[str, Any] = dict(found)
    duck = lane(mixer["tracks"][1], 1)
    heard: list[float] = []
    for part, length in zip(duck["patterns"], lengths, strict=True):
        times = [t - 1 for t, _ in part["points"]]
        gains = [10 ** (v / 20) for _, v in part["points"]]
        ticks = np.arange(length * TPL) / TPL
        heard += list(20 * np.log10(np.interp(ticks, times, gains)))
    assert np.diff(heard).max() <= mixplan.DUCK_TICK_STEP_DB  # 0.24 dB, as renoise-check measured


@pytest.mark.req("REQ-REN-11")
def test_the_duck_starts_where_its_automation_does() -> None:
    # At 16 lines a beat a tick (1.95 ms) is shorter than the remix's 2 ms
    # attack, so the first dip's floor lands on the song's first line, where
    # Renoise holds it: the Duck's own gain starts there too.
    loaded = ducked("depth_db = 24\nattack_ms = 2\nrelease_ms = 430")
    line_s = loaded.beat_s / 16
    found = mixplan.plan(
        loaded, score.events(loaded), [64], line_s=line_s, lpb=16, bar_lines=64, tpl=16
    )
    assert found is not None
    mixer: dict[str, Any] = dict(found)
    bass = mixer["tracks"][1]
    first = bass["automation"][0]["patterns"][0]["points"][0]
    assert first == [1.0, pytest.approx(-24.0, abs=0.01)]
    assert params(bass["devices"][0])["Gain"] == (first[1], "dB")
    # Each later kick: 0 dB a tick before it, the floor on its own line.
    points = dict((t, v) for t, v in bass["automation"][0]["patterns"][0]["points"])
    assert points[1 + 16 - 1 / 16] == 0.0 and points[1 + 16] == pytest.approx(-24.0, abs=0.01)
