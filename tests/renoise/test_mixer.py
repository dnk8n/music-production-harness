"""The bridge tool builds a song's mixer and sampled synths (spec/components/renoise.md,
REQ-REN-03, REQ-REN-09 to REQ-REN-12).

build.lua and mixer.lua run under Lua 5.1 against fake_renoise.lua, whose
devices are named and show their values as Renoise 3.5.4's do ("2.50 kHz",
"-6.021 dB", "-INF dB", "40.000 %"). scripts/renoise-check builds the same
kind of song in real Renoise on the Mac.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from mph.bench import sampling, score
from mph.renoise import luadata
from mph.renoise import plan as song_plan
from tests.renoise import lua

pytestmark = pytest.mark.lua

ARRANGED = """
id = "BM-ARR"
title = "An arranged test"
bpm = 120
bars = 4

sections = [
  { name = "Intro", bars = [1, 2], play = ["kick", "bass"] },
  { name = "Drop", bars = [3, 4], play = ["kick", "bass", "stab"] },
]

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = { role = "kick" }

[voices.kick.mix]
level = -2

[voices.bass]
notes  = "C2 C2 C3 C2 C2 C2 C3 C2 F1 F1 F2 F1 F1 F2 G1 G1"
gate   = "x  x  x  x  x  x  x  x  x  x  x  x  x  x  x  x"
accent = ".  .  X  .  .  .  X  .  .  .  X  .  .  .  .  ."
slide  = ".  .  .  .  .  .  .  .  .  .  .  .  .  S  .  ."
sound = { synth = "mph_bass" }

[voices.bass.mix]
highpass = 30
lowpass = 400
resonance = 0.3

[voices.bass.automate]
lowpass = [[1, 400], [3, 2000]]

[voices.stab]
gate  = ". . x - . . x - . . x - . . x -"
notes = "C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4"
chord = [0, 3, 7]
sound = { synth = "mph_stab" }

[voices.stab.mix]
highpass = 200
pan = -0.5
send = { echo = -8, hall = -14 }

[voices.stab.automate]
echo = [[3, -inf], [3.5, -inf], [3.5, 0], [3.75, 0], [3.75, -inf]]

[sends.echo]
kind = "delay"
beats = 0.75
feedback = 0.5
lowpass = 2500
pingpong = true
send = { hall = -12 }

[sends.hall]
kind = "reverb"
decay_s = 1.5
level = -3

[master]
highpass = 25

[sidechain]
trigger = "kick"
targets = ["bass"]
depth_db = 12
release_ms = 300
"""

FILTER = "Audio/Effects/Native/Digital Filter"
GAINER = "Audio/Effects/Native/Gainer"
SEND = "Audio/Effects/Native/#Send"
MULTITAP = "Audio/Effects/Native/Multitap"
REVERB = "Audio/Effects/Native/mpReverb 2"
AHDSR = "Modulation/AHDSR"


def _synth(folder: Path, loaded: score.Score, voice: str) -> song_plan.Synth:
    """A sampled synth as the render makes one: a sample per sound (REQ-REN-09)."""
    own = [e for e in score.events(loaded) if e.voice == voice]
    synthdef = loaded.voice(voice).sound.synth

    def zone(pitch: int, accent: bool) -> song_plan.Zone:
        file = folder / voice / (f"{pitch}-accent.wav" if accent else f"{pitch}.wav")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"x")
        return song_plan.Zone(str(file), song_plan.renoise_note(pitch))

    found = sorted(sampling.sounds(own))
    return song_plan.Synth(
        zones=tuple(zone(p, False) for p, accent in found if not accent),
        accents=tuple(zone(p, True) for p, accent in found if accent),
        release_s=sampling.release_s(synthdef, {}),
        glide_s=sampling.glide_s(synthdef, {}),
    )


def planned(tmp_path: Path) -> dict[str, Any]:
    loaded = score.load_text(ARRANGED)
    kick = tmp_path / "kick.wav"
    kick.write_bytes(b"x")
    tracks = [
        song_plan.Track("kick", str(kick), 1.0),
        song_plan.Track("bass", "", 1.0, synth=_synth(tmp_path, loaded, "bass")),
        song_plan.Track("stab", "", 0.5, synth=_synth(tmp_path, loaded, "stab")),
    ]
    events = score.events(loaded)
    return song_plan.song(loaded, events, tracks, rate=48000, save="x", renders=[])


def build(tmp_path: Path, song: dict[str, Any], *mode: str) -> dict[str, Any]:
    path = tmp_path / "plan.lua"
    path.write_text(luadata.document(song), "utf-8")
    built: dict[str, Any] = lua.run("build", str(path), *mode)
    return built


def number(shown: str) -> float:
    """A value as the fake shows it, in Hz, dB, ms or %."""
    value, _, unit = shown.partition(" ")
    if value.upper().endswith("INF"):
        return -math.inf if value.startswith("-") else math.inf
    return float(value) * (1000 if unit in ("kHz", "s") else 1)


KHZ_STEP = 10  # Hz: Renoise shows a filter's frequency in kHz, to two places
LINE_S = 0.0625  # 120 BPM, 8 lines a beat


def slot(seconds: float) -> float:
    """Where an automation point at a time lands in the first pattern: the
    nearest 1/256 of a line, counted from 1."""
    return 1 + round(seconds / LINE_S * 256) / 256


def devices(built: dict[str, Any], track: int) -> list[tuple[str, str, bool]]:
    return [
        (d["display_name"], d["name"], d["active"]) for d in built["mixer"][track - 1]["devices"]
    ]


def shows(built: dict[str, Any], track: int, name: str) -> dict[str, str]:
    found: dict[str, str] = next(
        d["shows"] for d in built["mixer"][track - 1]["devices"] if d["display_name"] == name
    )
    return found


def preset(built: dict[str, Any], track: int, name: str) -> dict[str, str]:
    """A device's settings in its preset data, as the fake holds them."""
    found: dict[str, str] = next(
        d["preset"] for d in built["mixer"][track - 1]["devices"] if d["display_name"] == name
    )
    return found


def lanes(built: dict[str, Any], track: int, parameter: str) -> list[dict[str, Any]]:
    return [a for a in built["automation"] if a["track"] == track and a["parameter"] == parameter]


@pytest.mark.req("REQ-REN-10")
@pytest.mark.req("REQ-REN-12")
def test_the_tool_builds_the_mixer(tmp_path: Path) -> None:
    built = build(tmp_path, planned(tmp_path))
    assert built.get("error") is None
    assert (built["tpl"], built["headroom_db"]) == (12, 0.0)
    # Sequencer tracks, the master, then a send track for each send.
    kinds = [(t["name"], t["type"]) for t in built["mixer"]]
    assert kinds == [("kick", 1), ("bass", 1), ("stab", 1), ("Master", 2), ("echo", 3), ("hall", 3)]
    assert devices(built, 1) == [("Filter", FILTER, False), ("Level", GAINER, True)]
    assert devices(built, 2) == [
        ("Duck", GAINER, True),
        ("Highpass", FILTER, True),
        ("Lowpass", FILTER, True),
    ]
    assert devices(built, 3) == [
        ("Highpass", FILTER, True),
        ("Level", GAINER, True),
        ("Send to echo", SEND, True),
        ("Send to hall", SEND, True),
    ]
    assert devices(built, 4) == [("Highpass", FILTER, True)]
    assert devices(built, 5) == [("echo", MULTITAP, True), ("Send to hall", SEND, True)]
    assert devices(built, 6) == [("hall", REVERB, True), ("Level", GAINER, True)]
    # Each parameter shows what the score asks for.
    assert shows(built, 1, "Level")["Gain"] == "-2.000 dB"
    highpass, lowpass = shows(built, 2, "Highpass"), shows(built, 2, "Lowpass")
    # A plain filter is Butterworth 4n, which has no resonance; a resonant one
    # keeps Biquad and its resonance knob.
    assert (highpass["Type"], highpass["Cutoff"]) == ("HighPass", "0.03 kHz")
    assert "Resonance" not in highpass and "(void)" in highpass
    assert preset(built, 2, "Highpass")["Model"] == "Butterworth 4n"
    assert (lowpass["Type"], number(lowpass["Cutoff"])) == ("LowPass", 400.0)
    assert number(lowpass["Resonance"]) == pytest.approx(30.0, abs=0.1)  # resonance 0.3
    assert preset(built, 2, "Lowpass")["Model"] == "Biquad"
    level = shows(built, 3, "Level")
    assert (level["Gain"], level["Panning"]) == ("0.000 dB", "25 L")  # pan -0.5
    echo, hall = shows(built, 3, "Send to echo"), shows(built, 3, "Send to hall")
    # The send keeps its source: the fake's #Send starts muted, and the tool
    # unmutes it in the preset data. Receivers are send tracks counted from 0.
    assert (echo["Amount"], echo["Receiver"]) == ("-INF dB", "0")
    assert number(hall["Amount"]) == pytest.approx(-14.0, abs=0.01)
    assert hall["Receiver"] == "1"
    assert preset(built, 3, "Send to echo")["MuteSource"] == "false"
    assert preset(built, 3, "Send to hall")["MuteSource"] == "false"
    assert preset(built, 5, "Send to hall")["MuteSource"] == "false"
    assert number(shows(built, 4, "Highpass")["Cutoff"]) == pytest.approx(25.0, abs=KHZ_STEP / 2)
    tap = shows(built, 5, "echo")
    assert (tap["Mute Dry"], tap["T1 Line Sync"], tap["T1 Ping Pong"]) == ("On", "On", "On")
    assert (tap["T1 L Sync Time"], tap["T1 R Sync Time"]) == ("6", "6")  # 3/4 beat, in lines
    assert number(tap["T1 L Feedback"]) == pytest.approx(50.0, abs=0.1)
    assert number(tap["T1 R Feedback"]) == pytest.approx(50.0, abs=0.1)
    assert (tap["T1 Amount"], tap["T2 Amount"], tap["T4 Amount"]) == (
        "0.000 dB",
        "-INF dB",
        "-INF dB",
    )
    assert (tap["T1 Flt Mode"], tap["T1 Flt Type"]) == ("In + Feed", "Lowpass")
    assert number(tap["T1 Flt Freq."]) == 2500.0
    assert tap["T1 Sync L DelayTime"] == "-1"  # a pattern command's target: always -1
    assert number(shows(built, 5, "Send to hall")["Amount"]) == pytest.approx(-12.0, abs=0.01)
    reverb = shows(built, 6, "hall")
    assert number(reverb["Duration"]) == pytest.approx(1500, rel=0.01)
    assert number(reverb["Predelay"]) == pytest.approx(20, abs=1)
    assert (reverb["Dry Mix"], reverb["Wet Mix"]) == ("0.000 %", "100.000 %")  # fully wet
    assert shows(built, 6, "Level")["Gain"] == "-3.000 dB"


@pytest.mark.req("REQ-REN-03")
def test_the_result_says_what_renoise_has(tmp_path: Path) -> None:
    built = build(tmp_path, planned(tmp_path))
    lowpass = shows(built, 2, "Lowpass")
    # What each kind of device has, for the render's notes.
    gainer = built["described"][GAINER]
    assert [(p["name"], p["shows"]) for p in gainer] == [
        ("Gain", ["-INF dB", "0.000 dB", "12.041 dB"]),
        ("Panning", ["50 L", "Center", "50 R"]),
    ]
    assert set(built["described"]) == {FILTER, GAINER, SEND, MULTITAP, REVERB, AHDSR}
    # And what each planned device shows, by track, for the result file: with
    # the optional settings Renoise doesn't have, and the device's other
    # preset settings, where such a setting might be found instead.
    shown = {entry["track"]: entry["devices"] for entry in built["shown"]["tracks"]}
    assert list(shown) == ["kick", "bass", "stab", "master", "send echo", "send hall"]
    assert shown["bass"][2] == {
        "name": "Lowpass",
        "path": FILTER,
        "active": True,
        "shows": {
            "Type": "LowPass",
            "Model": "Biquad",  # in its preset data
            "Cutoff": lowpass["Cutoff"],
            "Resonance": lowpass["Resonance"],
        },
        "preset": [
            "IsMaximized=true",
            "OversamplingFactor=2x",
            "Model=Biquad",
            "ShowResponseView=true",
            "ResponseViewMaxGain=18",
        ],
    }
    assert shown["kick"][0] == {"name": "Filter", "path": FILTER, "active": False, "shows": []}
    sent = shown["stab"][2]
    assert (sent["shows"]["MuteSource"], "missing" in sent) == ("false", False)
    assert sent["preset"] == [
        "MuteSource=false",
        "SmoothParameterChanges=true",
        "ApplyPostVolume=true",
    ]
    assert shown["send hall"][0]["missing"] == ["High Cut"]  # mpReverb 2 has no damping


@pytest.mark.req("REQ-REN-10")
@pytest.mark.req("REQ-REN-11")
def test_lanes_and_the_duck_are_automated(tmp_path: Path) -> None:
    built = build(tmp_path, planned(tmp_path))
    # The Intro, the Drop and the tail, at 8 lines a beat for the melodic parts.
    assert (built["lpb"], built["patterns"]) == (8, [64, 64, 16])
    # The bass's low-pass opens from 400 Hz to 2 kHz over the Intro: a point a
    # beat, in the Intro's pattern; the Drop's and the tail's hold it open.
    opening, held, tail = lanes(built, 2, "Cutoff")  # only the Lowpass moves
    assert opening["pattern"] == 1 and opening["playmode"] == 2  # lines between points
    points = [(p["time"], 20 * 1000 ** p["value"]) for p in opening["points"]]
    assert points[0] == (1.0, pytest.approx(400, rel=0.01))
    assert points[1] == (9.0, pytest.approx(400 * 5 ** (1 / 8), rel=0.01))  # a beat on
    assert points[-1] == (64 + 255 / 256, pytest.approx(2000, rel=0.01))
    assert [(p["time"], 20 * 1000 ** p["value"]) for p in held["points"]] == [
        (1.0, pytest.approx(2000, rel=0.01)),
        (64 + 255 / 256, pytest.approx(2000, rel=0.01)),
    ]
    assert [p["time"] for p in tail["points"]] == [1.0, 16 + 255 / 256]
    # The duck: the Gainer's Gain (0 to 4, linear) dips 12 dB at each kick,
    # drawn about 20 ms early: four ticks, a third of a line, at 8 lines a
    # beat. The first kick is on the first line, so the song starts dipped,
    # and the Duck starts there too.
    duck = lanes(built, 2, "Gain")
    assert [d["pattern"] for d in duck] == [1, 2, 3]
    gains = {p["time"]: 20 * math.log10(4 * p["value"]) for p in duck[0]["points"]}
    assert gains[1.0] == pytest.approx(-11.75, abs=0.01)  # 21 ms into the first dip
    assert number(shows(built, 2, "Duck")["Gain"]) == pytest.approx(gains[1.0], abs=0.01)
    lead_s, kick = 4 * LINE_S / 12, 8 * LINE_S  # 12 ticks a line; the next kick
    assert gains[slot(kick - lead_s)] == pytest.approx(0.0, abs=0.01)  # 0 dB 21 ms before it
    assert gains[slot(kick - lead_s + 0.002)] == pytest.approx(-12.0, abs=0.01)  # 2 ms on
    # The stab's echo throw: silent, open for a quarter bar at bar 3.5, silent again.
    throw = lanes(built, 3, "Amount")
    drop = next(t for t in throw if t["pattern"] == 2)
    values = {p["time"]: p["value"] for p in drop["points"]}
    assert values[1 + 16 - 1 / 256] == 0 and values[1 + 16] == 1.0
    assert values[1 + 24 - 1 / 256] == 1.0 and values[1 + 24] == 0


@pytest.mark.req("REQ-REN-09")
def test_melodic_voices_play_sampled_synths(tmp_path: Path) -> None:
    song = planned(tmp_path)
    built = build(tmp_path, song)
    assert built["instruments"] == 4  # kick, bass, stab, and the bass's accents
    bass, accent = built["instrument_states"][1], built["instrument_states"][3]
    assert bass["name"] == "bass" and accent["name"] == "bass accent"
    # A sample per pitch the bass plays, each on the keys nearest it.
    assert [(s["name"], s["base_note"], s["note_range"]) for s in bass["samples"]] == [
        ("bass F-1", 17, [0, 18]),
        ("bass G-1", 19, [19, 21]),
        ("bass C-2", 24, [22, 26]),
        ("bass F-2", 29, [27, 32]),
        ("bass C-3", 36, [33, 119]),
    ]
    assert {(s["new_note_action"], s["modulation_set"]) for s in bass["samples"]} == {(2, 1)}
    assert [s["base_note"] for s in accent["samples"]] == [29, 36]  # F2 and C3 are accented
    # Its envelope holds each note until its note-off, then releases it as the synth does.
    gate = bass["modulation_sets"][0]
    assert gate["name"] == "Gate"
    (envelope,) = gate["devices"]
    assert (envelope["target"], envelope["tempo_synced"]) == (1, False)
    assert number(envelope["shows"]["Release"]) == pytest.approx(20.0, rel=0.01)  # mph_bass
    stab = built["instrument_states"][2]
    assert [s["base_note"] for s in stab["samples"]] == [48]  # each chord whole, at its root
    stab_release = stab["modulation_sets"][0]["devices"][0]["shows"]["Release"]
    assert number(stab_release) == pytest.approx(120.0, rel=0.01)
    envelopes = built["shown"]["envelopes"]
    assert [e["instrument"] for e in envelopes] == ["bass", "bass accent", "stab"]
    released = [number(e["release"]) for e in envelopes]
    assert released == pytest.approx([20.0, 20.0, 120.0], rel=0.01)
    assert "Modulation/AHDSR" in built["described"]
    assert built["shown"]["refused_ends"] == 0  # the fake takes points up to a pattern's end
    # Notes: accents play the accent instrument; the tied note's successor glides.
    notes = {n["line"]: n for n in built["notes"] if n["track"] == 2}
    assert (notes[0]["note"], notes[0]["instrument"]) == (24, 1)
    assert notes[1]["note"] == 120  # its gate ends half a sixteenth on: a note-off
    assert (notes[4]["note"], notes[4]["instrument"]) == (36, 3)  # accented C3
    glide = notes[28]
    assert (glide["note"], glide["effect"], glide["instrument"]) == (19, "0G", 255)
    assert glide["amount"] == song_plan.glide_speed(-10, 0.05, 0.0625)
    assert 27 not in notes  # the slid note holds its whole step, into the glide
    assert notes[29]["note"] == 120  # and the glided-to note's gate ends as a plain one's
    assert built["tracks"][1]["effect_columns"] == 1


@pytest.mark.req("REQ-REN-03")
@pytest.mark.parametrize(
    ("mode", "on"),
    [
        (
            "mix",
            {
                "Level",
                "Duck",
                "Highpass",
                "Lowpass",
                "Send to echo",
                "Send to hall",
                "echo",
                "hall",
            },
        ),
        ("stem", {"Duck"}),
        ("dry", set()),
    ],
)
def test_renders_switch_devices_by_role(tmp_path: Path, mode: str, on: set[str]) -> None:
    built = build(tmp_path, planned(tmp_path), mode)
    switched = {
        (track["name"], device["display_name"]): device["active"]
        for track in built["switched"]
        for device in track["devices"]
    }
    assert switched[("kick", "Filter")] is False  # the spare filter stays bypassed
    del switched[("kick", "Filter")]
    assert {name for (_, name), active in switched.items() if active} == on
    assert all(active == (name in on) for (_, name), active in switched.items())


@pytest.mark.req("REQ-REN-03")
def test_a_build_names_everything_renoise_lacks(tmp_path: Path) -> None:
    song = planned(tmp_path)
    level = song["mix"]["tracks"][0]["devices"][1]
    level["params"].append({"names": ["Shimmer", "Sparkle"], "value": 1, "unit": "number"})
    level["params"].append({"names": ["Width"], "value": 1, "unit": "number", "optional": True})
    highpass, lowpass = song["mix"]["tracks"][1]["devices"][1:3]
    assert highpass["params"][1] == {"names": ["Model"], "value": "Butterworth 4n",
                                     "unit": "preset", "optional": True}  # fmt: skip
    del highpass["params"][1]["optional"]
    highpass["params"][1]["value"] = "Moog"  # Renoise won't load it
    lowpass["params"][1]["value"] = "Moog"  # optional: it keeps Biquad
    sent = song["mix"]["tracks"][2]["devices"][2]
    sent["params"].append({"names": ["Glow"], "value": "true", "unit": "preset"})
    reverb = song["mix"]["sends"][1]["devices"][0]
    reverb["params"].append({"names": ["Wet Mix"], "value": -6.0, "unit": "dB"})  # it shows %
    highpass = song["mix"]["master"]["devices"][0]
    highpass["params"][0]["value"] = "Wobble"
    song["mix"]["sends"][0]["devices"][0]["path"] = "Audio/Effects/Native/Multitap Delay"
    built = build(tmp_path, song)
    assert built["error"].splitlines() == [
        "kick, Level: no parameter called Shimmer or Sparkle (it has Gain, Panning)",
        "bass, Highpass: Model stayed Biquad, not Moog",
        "stab, Send to echo: no setting called Glow in its preset data",
        "master, Highpass: Type never shows Wobble",
        "send echo: Renoise has no device Audio/Effects/Native/Multitap Delay"
        " (it has Audio/Effects/Native/Multitap)",
        "send hall, hall: Wet Mix never shows -6",
    ]
    assert set(built["described"]) == {FILTER, GAINER, SEND, REVERB, AHDSR}  # no Multitap
    assert built["automation"] == []  # nothing automated once a device is wrong
