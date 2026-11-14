"""The mix: strips, sends, automation and how a mixed score renders
(spec/components/mix.md, REQ-MIX-01 to REQ-MIX-04).

A small score with two voices, a dub echo, a hall and a master runs through
the score rules, the SuperCollider graph mph builds, and a render with the
fake scsynth, so CI covers the mix without real apps. The real synths are
measured in ``test_real_supercollider.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from mph.bench import audio, kit, mix, osc, render, sc, scmix, score
from mph.bench.mix import Lane, Strip
from mph.proc import Result
from tests.bench.fake_sc import fake_scsynth
from tests.bench.test_render import cfg_for, runner

MIXED = """
id = "BM-MIX"
title = "A mixed test"
bpm = 120
bars = 4

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = {{ role = "kick" }}

[voices.kick.mix]
level = -2

[voices.stab]
gate  = ". . x - - - x - - - x - - - x -"
notes = "C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4"
chord = [0, 3, 7]
sound = {{ synth = "mph_stab" }}

[voices.stab.mix]
highpass = 200
lowpass = 1200
resonance = 0.3
pan = -0.5
send = {{ echo = -8, hall = -14 }}

[voices.stab.automate]
lowpass = [[1, 1200], [3, 4800]]
echo = [[2, -inf], [2.5, -inf], [2.5, 0], [2.75, 0], [2.75, -inf]]

[sends.echo]
kind = "delay"
beats = 0.75
feedback = 0.5
lowpass = 2500
pingpong = true
highpass = 250
send = {{ hall = -12 }}

[sends.hall]
kind = "reverb"
decay_s = 1.5
level = -3
{master}
[sidechain]
trigger = "kick"
targets = ["stab"]
depth_db = 12
release_ms = 400
"""
MASTER = """
[master]
highpass = 25

[master.automate]
level = [[4, 0], [5, -6]]
"""


def mixed(master: str = MASTER) -> score.Score:
    return score.load_text(MIXED.format(master=master))


def changed(old: str, new: str) -> score.Score:
    text = MIXED.format(master=MASTER)
    assert old in text, old
    return score.load_text(text.replace(old, new))


# --- strips and sends (REQ-MIX-01, REQ-MIX-02) -----------------------------------------------


@pytest.mark.req("REQ-MIX-01")
@pytest.mark.req("REQ-MIX-02")
def test_strips_sends_and_master_are_read_from_the_score() -> None:
    loaded = mixed()
    assert loaded.mixed and loaded.supercollider_only.startswith("a melodic part")
    assert loaded.voice("kick").strip == Strip(level=-2.0)
    stab = loaded.voice("stab").strip
    assert stab is not None
    assert (stab.highpass, stab.lowpass, stab.resonance, stab.pan) == (200.0, 1200.0, 0.3, -0.5)
    assert stab.sends == (("echo", -8.0), ("hall", -14.0))
    assert stab.lane("echo") == Lane(
        "echo", ((2.0, -math.inf), (2.5, -math.inf), (2.5, 0.0), (2.75, 0.0), (2.75, -math.inf))
    )
    echo, hall = loaded.sends
    assert (echo.name, echo.kind, echo.setting("beats"), echo.setting("pingpong")) == (
        "echo",
        "delay",
        0.75,
        1.0,
    )
    assert (echo.setting("feedback"), echo.setting("lowpass")) == (0.5, 2500.0)
    assert echo.strip == Strip(highpass=250.0, sends=(("hall", -12.0),))
    assert dict(hall.settings) == {"decay_s": 1.5, "damping": 6000.0, "predelay_ms": 20.0}
    assert hall.strip == Strip(level=-3.0)
    assert loaded.master == Strip(highpass=25.0, lanes=(Lane("level", ((4.0, 0.0), (5.0, -6.0))),))
    plain = score.load("BM-DRUM-02")
    assert not plain.mixed and plain.voices[0].strip is None
    # A score with only a master is mixed too, and its voices get plain strips.
    master_only = score.load_text(
        'id = "X"\nbpm = 120\nbars = 1\n[master]\nlevel = -1\n'
        '[voices.kick]\ngrid = "x . . . x . . . x . . . x . . ."\n'
    )
    assert master_only.mixed and master_only.master == Strip(level=-1.0)
    assert master_only.supercollider_only == "a mix (strips, sends and automation)"


@pytest.mark.req("REQ-MIX-01")
@pytest.mark.req("REQ-MIX-02")
@pytest.mark.req("REQ-MIX-03")
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("resonance = 0.3", "resonance = 1.3", "resonance goes from 0 to 1, and pan from -1 to 1"),
        ("pan = -0.5", "pan = -1.5", "resonance goes from 0 to 1"),
        ("highpass = 200", "highpass = 2", "needs a frequency from 10 to 30000 Hz"),
        ("highpass = 200", "highpass = true", "voice stab needs a number: True"),
        ("highpass = 200", "volume = 3", "a strip takes highpass, .* not volume"),
        ("send = { echo = -8, hall = -14 }", "send = 4", "send names sends and dB"),
        ("level = -2", "level = inf", "needs a level in dB, or -inf for silence"),
        ("send = { echo = -8, hall = -14 }", "send = { dub = -8 }", "sends to 'dub', which isn't"),
        ("send = { hall = -12 }", "send = { echo = -12 }", "send echo feeds echo, but a send o"),
        ("lowpass = [[1, 1200], [3, 4800]]", "lowpass = [[3, 1200], [1, 4800]]", "go in order"),
        ("lowpass = [[1, 1200], [3, 4800]]", "lowpass = [[0, 1200]]", "bars count from 1"),
        (
            "lowpass = [[1, 1200], [3, 4800]]",
            "lowpass = [[1, 2, 3]]",
            "a point is \\[bar, value\\]",
        ),
        ("lowpass = [[1, 1200], [3, 4800]]", "lowpass = []", "needs a list of \\[bar, value\\]"),
        ("lowpass = [[1, 1200], [3, 4800]]", "pan = [[1, 0.5]]", "automates 'pan'; it can move"),
        (
            "lowpass = [[1, 1200], [3, 4800]]",
            "lowpass = [[1, 100], [1, 200], [1, 300]]",
            "a bar holds at most two points",
        ),
        ('kind = "delay"', 'kind = "flanger"', "send echo needs a kind: delay, reverb"),
        ("beats = 0.75", "", "a delay needs beats"),
        ("beats = 0.75", "beats = 9", "beats goes above 0 up to 8"),
        ("feedback = 0.5", "feedback = 1.0", "feedback from 0 to under 1"),
        ("pingpong = true", "pingpong = 1", "pingpong is true or false"),
        ("decay_s = 1.5", "decay_s = 31", "decay_s goes above 0 up to 30"),
        ("decay_s = 1.5", "decay_s = 1.5\npredelay_ms = 600", "predelay_ms from 0 to 500"),
        ("decay_s = 1.5", "decay_s = 1.5\ndamping = 5", "send hall, damping needs a frequency"),
        ("[sends.echo]", "[sends.level]", "a send can't be called level"),
        ("highpass = 25", "highpass = 25\npan = 0.5", "the master has no sends and no pan"),
        ("[sends.hall]", "[sends.hall]\nlevel = 2\n[sends.hall2]", "needs a kind"),
        ("bpm = 120", "bpm = 11", "echo comes after 0.75 beats, longer than the 4 seconds"),
    ],
)
def test_mix_mistakes_are_explained(old: str, new: str, message: str) -> None:
    with pytest.raises(score.ScoreError, match=message):
        changed(old, new)


@pytest.mark.req("REQ-MIX-01")
def test_a_strip_feeds_at_most_four_sends_and_sends_are_tables() -> None:
    with pytest.raises(score.ScoreError, match="sends holds one table per send"):
        score.load_text(
            'id = "X"\nbpm = 120\nbars = 1\nsends = 3\n'
            '[voices.kick]\ngrid = "x . . . x . . . x . . . x . . ."\n'
        )
    five = ", ".join(f"s{n} = -6" for n in range(5))
    with pytest.raises(score.ScoreError, match="feeds 5 sends; a strip feeds at most 4"):
        changed("send = { echo = -8, hall = -14 }", f"send = {{ {five} }}")


# --- automation (REQ-MIX-03) ------------------------------------------------------------------


@pytest.mark.req("REQ-MIX-03")
def test_lanes_move_frequencies_in_octaves_and_levels_in_decibels() -> None:
    sweep = Lane("lowpass", ((3.0, 1000.0), (5.0, 4000.0)))
    assert sweep.at(1) == 1000.0  # the first value, before the first point
    assert sweep.at(4) == pytest.approx(2000.0)  # half way in octaves, not 2500
    assert sweep.at(9) == 4000.0  # the last value, after the last point
    fade = Lane("level", ((1.0, -math.inf), (2.0, 0.0)))
    assert fade.at(1.5) == pytest.approx(-45.0)  # from silence, counted from -90 dB
    throw = Lane("echo", ((2.0, -math.inf), (2.0, -6.0), (3.0, -6.0), (3.0, -math.inf)))
    assert throw.at(2) == -6.0  # a jump: the value it jumps to
    assert throw.at(2.5) == -6.0
    assert throw.at(3) == -math.inf
    assert mix.between(-100.0, -95.0, 0.5, octaves=False) == mix.SILENT_DB


@pytest.mark.req("REQ-MIX-03")
@pytest.mark.req("REQ-TRK-03")
def test_an_excerpt_starts_each_lane_at_the_tracks_value() -> None:
    sweep = Strip(lanes=(Lane("lowpass", ((3.0, 1000.0), (7.0, 16000.0))),))
    cut = mix.cut(sweep, 4, 5)
    assert cut is not None
    assert cut.lanes[0] == Lane("lowpass", ((1.0, 2000.0), (3.0, 8000.0)))
    inside = mix.cut(sweep, 2, 8)
    assert inside is not None
    assert inside.lanes[0].points == ((1.0, 1000.0), (2.0, 1000.0), (6.0, 16000.0), (8.0, 16000.0))
    assert mix.cut(None, 1, 2) is None
    assert mix.cut(Strip(level=-3.0), 1, 2) == Strip(level=-3.0)
    part = score.excerpt(mixed(), 2, 3, id="BM-MIX-X")
    stab = part.voice("stab").strip
    assert stab is not None
    assert stab.lane("lowpass") == Lane("lowpass", ((1.0, 2400.0), (2.0, 4800.0), (3.0, 4800.0)))
    assert stab.lane("echo") is not None and stab.lane("echo").at(1.5) == 0.0  # type: ignore[union-attr]
    assert part.master is not None and part.master.lanes[0].points == ((1.0, 0.0), (3.0, 0.0))
    assert part.sends[0].strip == mixed().sends[0].strip


# --- the graph (REQ-MIX-01, REQ-MIX-02, REQ-MIX-04) -------------------------------------------


def plan_for(tmp_path: Path, loaded: score.Score, *, master_only: bool = False) -> sc.Plan:
    choices = kit.choose(loaded, None, tmp_path)
    return sc.Plan(loaded, choices, 48000, tmp_path / "defs", master_only=master_only)


def started(bundles: list[osc.Bundle], name: str) -> list[osc.Message]:
    return [m for b in bundles for m in b.messages if m.address == "/s_new" and m.args[0] == name]


def args_of(message: osc.Message) -> dict[str, object]:
    return dict(zip(message.args[4::2], message.args[5::2], strict=True))  # type: ignore[arg-type]


@pytest.mark.req("REQ-MIX-01")
@pytest.mark.req("REQ-MIX-02")
@pytest.mark.req("REQ-MIX-04")
def test_a_mix_routes_each_voice_through_its_strip_to_the_sends_and_master(
    tmp_path: Path,
) -> None:
    loaded = mixed()
    plan = plan_for(tmp_path, loaded)
    assert plan.channels == 2 * 2 + 2 + 2  # two stems, the stab before its dip, the master
    bundles = sc.build(plan, score.events(loaded))
    setup = bundles[0].messages
    groups = [m.args for m in setup if m.address == "/g_new"]
    assert groups == [
        (scmix.AUTOMATION_GROUP, scmix.HEAD, 0),
        (scmix.VOICES_GROUP, scmix.TAIL, 0),
        (scmix.BYPASS_GROUP, scmix.AFTER, scmix.VOICES_GROUP),
        (scmix.SIDECHAIN_GROUP, scmix.AFTER, scmix.BYPASS_GROUP),
        (scmix.STRIPS_GROUP, scmix.AFTER, scmix.SIDECHAIN_GROUP),
        (scmix.SENDS_GROUP, scmix.AFTER, scmix.STRIPS_GROUP),
        (scmix.MASTER_GROUP, scmix.AFTER, scmix.SENDS_GROUP),
    ]
    # Each voice writes to its own private pair, and the stab's chord is three synths.
    kicks, stabs = started(bundles, "mph_kick"), started(bundles, "mph_stab")
    assert {args_of(m)["out"] for m in kicks} == {scmix.VOICE_BUS}
    assert {args_of(m)["out"] for m in stabs} == {scmix.VOICE_BUS + 2}
    assert len(stabs) == 3 * 16
    # The duckers dip the stab's pair; one tap copies it before the dip, and the
    # stem taps copy every pair as it enters its strip.
    ducker = started(bundles, "mph_ducker")[0]
    assert ducker.args[3] == scmix.SIDECHAIN_GROUP and args_of(ducker)["out"] == scmix.VOICE_BUS + 2
    taps = [(m.args[3], args_of(m)["in"], args_of(m)["out"]) for m in started(bundles, "mph_tap")]
    assert taps == [
        (scmix.BYPASS_GROUP, scmix.VOICE_BUS + 2, 4),
        (scmix.STRIPS_GROUP, scmix.VOICE_BUS, 0),
        (scmix.STRIPS_GROUP, scmix.VOICE_BUS + 2, 2),
    ]
    strips = {m.args[1]: args_of(m) for m in started(bundles, "mph_strip")}
    assert strips[scmix.STRIP_NODE] == {
        "in": scmix.VOICE_BUS, "out": scmix.MASTER_BUS, "hp": 0, "lp": 0,
        "resonance": 0.0, "level": -2.0, "pan": 0.0,
    }  # fmt: skip
    assert strips[scmix.STRIP_NODE + 1] == {
        "in": scmix.VOICE_BUS + 2, "out": scmix.MASTER_BUS, "hp": 1, "lp": 1,
        "highpass": 200.0, "lowpass": 1200.0, "resonance": 0.3, "level": 0.0, "pan": -0.5,
        "bus1": scmix.SEND_BUS, "send1": -90.0, "bus2": scmix.SEND_BUS + 4, "send2": -14.0,
    }  # fmt: skip
    echo, hall = started(bundles, "mph_delay")[0], started(bundles, "mph_reverb")[0]
    assert args_of(echo) == {
        "in": scmix.SEND_BUS, "out": scmix.SEND_BUS + 2, "time": 0.375, "feedback": 0.5,
        "lowpass": 2500.0, "pingpong": 1,
    }  # fmt: skip
    assert args_of(hall) == {
        "in": scmix.SEND_BUS + 4, "out": scmix.SEND_BUS + 6, "decay": 1.5, "damping": 6000.0,
        "predelay": 0.02,
    }  # fmt: skip
    assert strips[scmix.RETURN_NODE]["bus1"] == scmix.SEND_BUS + 4  # the echo fades into the hall
    assert strips[scmix.RETURN_NODE + 1]["level"] == -3.0
    assert (strips[scmix.MASTER_NODE]["in"], strips[scmix.MASTER_NODE]["out"]) == (
        scmix.MASTER_BUS,
        6,
    )
    # The second pass writes the master alone, with the same graph and no taps.
    second = plan_for(tmp_path, loaded, master_only=True)
    again = sc.build(second, score.events(loaded))
    assert second.channels == 2 and not started(again, "mph_tap")
    assert args_of(started(again, "mph_strip")[-1])["out"] == 0
    assert [b for b in again if b.time_s] == [b for b in bundles if b.time_s]


@pytest.mark.req("REQ-MIX-04")
def test_a_mix_has_room_for_a_hundred_voices(tmp_path: Path) -> None:
    loaded = mixed()
    crowded = score.Score(
        "X",
        120,
        1,
        tuple(loaded.voices[0] for _ in range(scmix.MAX_VOICES + 1)),
        sends=loaded.sends,
    )
    with pytest.raises(ValueError, match="a mix has room for 100 voices and 50 sends"):
        scmix.check(crowded)


@pytest.mark.req("REQ-MIX-03")
def test_automation_maps_each_moving_control_to_a_bus_it_moves(tmp_path: Path) -> None:
    loaded = mixed()
    bundles = sc.build(plan_for(tmp_path, loaded), score.events(loaded))
    setup = bundles[0].messages
    sets = [m.args for m in setup if m.address == "/c_set"]
    maps = [m.args for m in setup if m.address == "/n_map"]
    stab, master = scmix.STRIP_NODE + 1, scmix.MASTER_NODE
    assert maps == [(stab, "lowpass", 100), (stab, "send1", 101), (master, "level", 102)]
    assert sets == [(100, 1200.0), (101, -90.0), (102, 0.0)]  # -inf is -90 dB
    moves = [(b.time_s, m.address, m.args) for b in bundles[1:] for m in b.messages]
    block = scmix.BLOCK / 48000
    lines = [
        (t, a[0], args_of(osc.Message("/s_new", a)))
        for t, addr, a in moves
        if a and a[0] in ("mph_line", "mph_xline")
    ]
    sweep = {"bus": 100, "from": 1200.0, "to": 4800.0, "dur": pytest.approx(4.0 - block)}
    fade = {"bus": 102, "from": 0.0, "to": -6.0, "dur": pytest.approx(2.0 - block)}
    assert lines == [(0.0, "mph_xline", sweep), (6.0, "mph_line", fade)]
    jumps = [(t, a) for t, addr, a in moves if addr == "/c_set" and a[0] == 101]
    assert jumps == [(3.0, (101, 0.0)), (3.5, (101, -90.0))]  # the echo throw
    # A move too short for a line is a jump to its end.
    quick = Lane("level", ((1.0, 0.0), (1.0001, -6.0)))
    assert scmix.moves(quick, 7, 2.0, 48000) == [
        osc.Bundle(0.0, (osc.Message("/c_set", (7, -6.0)),))
    ]


# --- rendering (REQ-MIX-04) -------------------------------------------------------------------


@pytest.mark.req("REQ-MIX-04")
@pytest.mark.req("REQ-BEN-12")
def test_a_mixed_render_measures_dry_stems_and_delivers_the_mixed_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = mixed()
    monkeypatch.setattr(render, "load", lambda _: loaded)
    fake = runner()
    outcome = render.run("BM-MIX", cfg_for(tmp_path), fake, None, engine="supercollider")
    found = {m["name"]: m for m in outcome.report["measurements"]}  # type: ignore[attr-defined]
    assert [m for m in found.values() if not m["passed"]] == []
    assert found["determinism: samples differing in a second render"]["value"] == 0
    assert any(name.startswith("sidechain: stab") for name in found)
    calls = [argv for argv, _ in fake.calls if argv[0].endswith("scsynth") and "-N" in argv]
    channels = [int(argv[argv.index("-o") + 1]) for argv in calls]
    assert 8 in channels and 2 in channels  # the first pass and the master alone
    folder = outcome.folder
    stems = {n: audio.read(folder / "stems" / f"{n}.wav")[0] for n in ("kick", "stab")}
    master, _ = audio.read(folder / "master.wav")
    # The master is the mix, not the stems' sum: the kick at -2 dB, the stab
    # panned left and echoing, the master fading out over the last bar.
    summed = audio.mixdown(stems)
    assert not np.allclose(master, summed[: len(master)], atol=1e-4)
    tail = slice(round(8.5 * 48000), 9 * 48000)
    assert np.abs(master[tail]).max() > 0  # the echoes and hall ring on into the tail
    assert np.abs(stems["stab"][tail]).max() < np.abs(master[tail]).max()


@pytest.mark.req("REQ-MIX-04")
def test_renoise_refuses_a_mix(tmp_path: Path) -> None:
    only = score.load_text(
        'id = "X"\nbpm = 120\nbars = 1\n[master]\nlevel = -1\n'
        '[voices.kick]\ngrid = "x . . . x . . . x . . . x . . ."\n'
    ).supercollider_only
    with pytest.raises(sc.RenderError, match=r"a mix .*only SuperCollider can play so far"):
        render.pick_engine("renoise", tmp_path, only=only)


@pytest.mark.req("REQ-MIX-04")
def test_scsynth_gets_memory_for_every_sends_delay_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = mixed()
    assert scmix.memory_kb(score.load("BM-DRUM-02"), 48000) == scmix.VOICE_MEMORY_KB
    # At 48 kHz, scsynth rounds each line up to a power of two samples: the
    # echo's four 4-second lines, and the hall's two predelays, eight
    # allpasses and eight lines.
    samples = 4 * 262144 + 2 * 32768 + 8 * 4096 + 8 * 8192
    assert scmix.memory_kb(loaded, 48000) == scmix.VOICE_MEMORY_KB + samples * 4 // 1024
    monkeypatch.setattr(render, "load", lambda _: loaded)
    fake = runner()
    render.run("BM-MIX", cfg_for(tmp_path), fake, None, engine="supercollider")
    renders = [argv for argv, _ in fake.calls if argv[0].endswith("scsynth") and "-N" in argv]
    assert renders and all(argv[argv.index("-m") + 1].isdigit() for argv in renders)
    mixes = [argv for argv in renders if argv[argv.index("-o") + 1] in ("8", "2")]
    assert {argv[argv.index("-m") + 1] for argv in mixes} == {str(scmix.memory_kb(loaded, 48000))}


@pytest.mark.req("REQ-MIX-04")
def test_a_render_fails_if_scsynth_could_not_start_a_synth(tmp_path: Path) -> None:
    def saying(line: str) -> Callable[[Sequence[str]], Result]:
        def scsynth(argv: Sequence[str]) -> Result:
            done = fake_scsynth(argv)
            return replace(done, stdout=f"{done.stdout}\n{line}\n") if "-N" in argv else done

        return scsynth

    short = saying("FAILURE IN SERVER /s_new out of real time memory")
    with pytest.raises(sc.RenderError, match=r"couldn't start every synth \(1 failed\), so the"):
        render.run("BM-DRUM-02", cfg_for(tmp_path), runner(scsynth=short), None)
    # A release sent to a note that had already ended is harmless.
    late = saying("FAILURE IN SERVER /n_set Node 1004 not found")
    outcome = render.run("BM-DRUM-02", cfg_for(tmp_path), runner(scsynth=late), None)
    assert all(m["passed"] for m in outcome.report["measurements"])  # type: ignore[attr-defined]


@pytest.mark.req("REQ-BEN-12")
def test_a_target_never_held_under_a_kick_has_no_dip_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The stab only plays in bar 2, where the kick rests: nothing to duck.
    loaded = changed(
        "bars = 4\n",
        'bars = 4\nsections = [\n  { name = "Kick", bars = [1, 1], play = ["kick"] },\n'
        '  { name = "Stab", bars = [2, 2], play = ["stab"] },\n'
        '  { name = "Kick", bars = [3, 4], play = ["kick"] },\n]\n',
    )
    monkeypatch.setattr(render, "load", lambda _: loaded)
    outcome = render.run("BM-MIX", cfg_for(tmp_path), runner(), None, engine="supercollider")
    found = outcome.report["measurements"]
    assert not [m for m in found if m["name"].startswith("sidechain")]  # type: ignore[attr-defined]
    assert all(m["passed"] for m in found)  # type: ignore[attr-defined]
