"""A check of the bridge tool in the real Renoise, for scripts/renoise-check (REQ-REN-08).

    python scripts/renoise_check.py

Run it on Dean's Mac with Renoise closed. It installs this checkout's bridge
tool, then, in one Renoise that mph starts:

1. a job builds, renders and saves two tiny throwaway songs, A and B, each
   with a named section and a track that plays a clip (REQ-TRK-02, REQ-TRK-04);
2. A is opened the way "Open in Renoise" opens a render's song, and a second
   job must keep it as a copy in mph's scratch folder before it builds C;
3. a third job builds M, a song with a mix and sampled synths (REQ-REN-09 to
   REQ-REN-11): every planned parameter must show what the plan asks for,
   the duck (the remix's own: 24 dB in 2 ms, back over 430 ms) must pass
   REQ-BEN-12's own checks at every kick on a steady tone and on the bass, as
   a render's report measures it, the stab must still sound on its own track
   while it sends (its #Send keeps its source), and the stab's echo must come
   back a dotted eighth after the note thrown into it. It prints each kind of
   device's parameters as Renoise names and shows them, the optional settings
   Renoise doesn't have or didn't take, the other settings in those devices'
   preset data, how far apart in samples each ducked render and its dry
   render are, the duck's real shape around a kick on the steady tone, where
   each part's worst moments fall, and each part's first two milliseconds,
   where a part that starts with the song starts from silence;
4. B is opened the same way, and Renoise is asked to quit.

If a step fails, the check still asks the Renoise it started to quit.

Renoise has to do all of that without asking to save anything: a question
would hold up a job, or keep Renoise open after it was asked to quit. The
songs live in a temporary folder inside mph's scratch folder and are deleted
at the end, with A's copy. The bridge tool that was installed before is put
back. Sources, renders and the catalogue aren't touched.
"""

from __future__ import annotations

import math
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from mph import config, proc
from mph.bench import audio, checks, sampling, score
from mph.config import Config
from mph.proc import Runner
from mph.renoise import bridge, luadata
from mph.renoise import plan as song_plan

RATE = 48000
JOB_TIMEOUT_S = 300.0  # longer than the tool waits for a new song, so its own error shows
LOAD_S = 5.0  # time for Renoise to open a song
QUIT_S = 30.0  # time for Renoise to quit
QUIT = ("osascript", "-e", 'tell application "Renoise" to quit')
LAG_LIMIT = 2400  # samples: how far apart two renders are looked for
FIRST_WINDOWS = 4  # the half-millisecond windows printed from the song's start
SHAPE_MS = (-30, -25, -20, -15, -10, -5, -2.5, 0, 2.5, 5, 10, 20, 50, 100, 200, 300, 400, 430, 440)
SHOWN_SLACK = {"Hz": 0.03, "ms": 0.03}  # a share of the value asked for
SHOWN_SLACK_DB = 0.25
SHOWN_SLACK_PERCENT = 0.5
UNITS = {  # what may follow a number in each unit, and its scale, as mixer.lua reads them
    "Hz": {"hz": 1.0, "khz": 1000.0},
    "ms": {"ms": 1.0, "s": 1000.0, "sec": 1000.0, "secs": 1000.0},
    "dB": {"db": 1.0},
    "%": {"%": 1.0},
    "number": {"": 1.0},
}
ON, OFF = {"on", "enabled", "yes", "true"}, {"off", "disabled", "no", "false"}  # as mixer.lua
# The stab in M: its first note, and the one note thrown into the echo (its
# echo send opens from bar 1.5 to 1.75), which comes back a dotted eighth later.
STAB_NOTE_S = 0.25
THROWN_S, ECHO_S = 1.25, 0.375
ONSET_WINDOW_S = (0.005, 0.025)  # after a note starts
KEPT_DB = 10.0  # the stab with its strip and sends on, at most this much under it dry
ECHO_SEARCH_S = (1.36, 1.74)  # after the thrown note has died away, before the next note
ECHO_SLACK_S = 0.015
ECHO_FLOOR_DB = 20.0  # the echo, at most this much under the thrown note
# The tone in M: one steady sine through both bars, ducked like the bass, so
# the duck's shape can be read without notes starting under it. A little noise
# in the sample tells one cycle of the sine from the next, so the renders'
# offset can be measured.
TONE_S, TONE_HZ, TONE_NOISE = 4.5, 1000.0, 0.005

# Every kind of device the mixer uses, and both kinds of synth note (REQ-REN-09 to 11).
MIXED = """
id = "RC-MIX"
bpm = 120
bars = 2

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
lowpass = [[1, 400], [2, 2000]]

[voices.stab]
gate  = ". . x - . . x - . . x - . . x -"
notes = "C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4 C4"
sound = { synth = "mph_stab" }

[voices.stab.mix]
highpass = 200
pan = -0.5
send = { echo = -8, hall = -14 }

[voices.stab.automate]
echo = [[1, -inf], [1.5, -inf], [1.5, 0], [1.75, 0], [1.75, -inf]]

[voices.tone]
grid = "X . . . . . . . . . . . . . . . | . . . . . . . . . . . . . . . ."
sound = { role = "pad" }

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

[master.automate]
level = [[2, 0], [3, -6]]

[sidechain]
trigger = "kick"
targets = ["bass", "tone"]
depth_db = 24
attack_ms = 2
release_ms = 430
"""


class CheckFailed(RuntimeError):
    pass


@contextmanager
def tool_put_back(home: Path) -> Iterator[None]:
    """Whatever bridge tool was installed before is installed again afterwards."""
    tools = bridge.tools_folder(home)
    installed = tools / bridge.TOOL_FOLDER if tools else None
    with tempfile.TemporaryDirectory(prefix="mph-tool-") as spare:
        before = Path(spare) / bridge.TOOL_FOLDER
        if installed and installed.is_dir():
            shutil.copytree(installed, before)
        try:
            yield
        finally:
            if installed:
                shutil.rmtree(installed, ignore_errors=True)
                if before.is_dir():
                    shutil.copytree(before, installed)


def blip(path: Path) -> Path:
    """A tenth of a second of decaying sine to build songs from."""
    t = np.arange(RATE // 10) / RATE
    audio.write_float(path, 0.5 * np.sin(2 * np.pi * 440 * t) * np.exp(-30 * t), RATE)
    return path


def tone(path: Path) -> Path:
    """A steady sine with a little noise, longer than song M."""
    t = np.arange(round(TONE_S * RATE)) / RATE
    noise = np.random.default_rng(7).standard_normal(len(t))
    audio.write_float(path, 0.5 * np.sin(2 * np.pi * TONE_HZ * t) + TONE_NOISE * noise, RATE)
    return path


def song(folder: Path, sample: Path, name: str) -> dict[str, luadata.Data]:
    """One bar at 120 BPM, a section named after the song: a blip on each beat,
    and the blip as a clip on the first. Rendered as a mix and then solo."""
    notes: list[luadata.Data] = [
        {"line": line, "delay": 0, "volume": 0x80} for line in (0, 4, 8, 12)
    ]
    track: dict[str, luadata.Data] = {
        "name": "blip",
        "sample": str(sample),
        "volume": 1.0,
        "transpose": 0,
        "fine_tune": 0,
        "slices": [],
        "notes": notes,
    }
    clip: dict[str, luadata.Data] = {
        **track,
        "name": "clip",
        "sample": "",
        "clips": [{"file": str(sample), "name": "blip"}],
        "notes": [{"line": 0, "delay": 0, "volume": 0x80, "clip": 1}],
    }
    return {
        "bpm": 120,
        "lpb": 4,
        "patterns": [{"lines": 16, "section": name}],
        "lines": 16,
        "rate": RATE,
        "save": str(folder / f"{name}.xrns"),
        "tracks": [track, clip],
        "renders": [
            {"file": str(folder / f"{name}.wav")},
            {"file": str(folder / f"{name} solo.wav"), "solo": 1},
        ],
    }


def mixed_song(folder: Path, sample: Path) -> dict[str, luadata.Data]:
    """Song M: the mixed score, its synths sampled from the blip, and a steady
    tone. Rendered as a mix after a warm-up, then the bass alone with its duck
    (a stem) and without, the stab with its sends and without, and the tone
    with its duck and without."""
    loaded = score.load_text(MIXED)
    events = score.events(loaded)

    def synth(voice: str) -> song_plan.Synth:
        found = sampling.sounds([e for e in events if e.voice == voice])
        zones = {
            accent: tuple(
                song_plan.Zone(str(sample), song_plan.renoise_note(pitch))
                for pitch, accented in sorted(found)
                if accented == accent
            )
            for accent in (False, True)
        }
        made = loaded.voice(voice).sound.synth
        release, glide = sampling.release_s(made, {}), sampling.glide_s(made, {})
        return song_plan.Synth(zones[False], zones[True], release, glide)

    tracks = [
        song_plan.Track("kick", str(sample), 1.0),
        song_plan.Track("bass", "", 1.0, synth=synth("bass")),
        song_plan.Track("stab", "", 1.0, synth=synth("stab")),
        song_plan.Track("tone", str(tone(folder / "tone.wav")), 1.0),
    ]
    renders: list[luadata.Data] = [
        {"file": str(folder / "M warm-up.wav")},
        {"file": str(folder / "M.wav")},
        {"file": str(folder / "M stem.wav"), "solo": 2, "mode": "stem"},
        {"file": str(folder / "M dry.wav"), "solo": 2, "mode": "dry"},
        {"file": str(folder / "M stab.wav"), "solo": 3},  # with its strip, sends and their effects
        {"file": str(folder / "M stab dry.wav"), "solo": 3, "mode": "dry"},
        {"file": str(folder / "M tone stem.wav"), "solo": 4, "mode": "stem"},
        {"file": str(folder / "M tone dry.wav"), "solo": 4, "mode": "dry"},
    ]
    save = str(folder / "M.xrns")
    return song_plan.song(loaded, events, tracks, rate=RATE, save=save, renders=renders)


def shown_number(text: str, unit: str) -> float | None:
    """A shown value as a number in the plan's unit, read as the tool reads it:
    only in that unit, so "40.000 %" is no level."""
    shown = text.strip().lower().replace(",", ".")
    if "inf" in shown:
        if unit != "dB":
            return None
        return -float("inf") if shown.startswith("-") else float("inf")
    number, _, rest = shown.partition(" ")
    scale = UNITS.get(unit, UNITS["number"]).get(rest.strip())
    try:
        return None if scale is None else float(number) * scale
    except ValueError:
        return None


def resolution(text: str, unit: str) -> float:
    """Half the step between two values as Renoise shows them, in the plan's
    unit: a filter shows "0.03 kHz", so 25 Hz shows as 20 or 30."""
    number, _, rest = text.strip().lower().partition(" ")
    places = len(number.partition(".")[2])
    return 0.5 * 10.0**-places * UNITS.get(unit, UNITS["number"]).get(rest.strip(), 1.0)


def same_choice(wanted: str, shown: str) -> bool:
    """Whether a choice shows what was asked: the same words, or on and off in
    any of their spellings, as the tool matches them."""
    want, text = wanted.strip().lower(), shown.strip().lower()
    for words in (ON, OFF):
        if want in words:
            return text in words
    return want == text or want in text


def mismatch(setting: dict[str, Any], shows: dict[str, str]) -> str:
    """Why a planned parameter doesn't show what was asked, or ""."""
    names, value, unit = setting["names"], setting["value"], setting["unit"]
    text = next((shows[n] for n in shows if n.lower() in (m.lower() for m in names)), None)
    if text is None:
        return "" if setting.get("optional") else f"{names[0]} isn't there"
    wanted = value["name"] if unit == "send" else value
    if unit in ("choice", "preset"):
        ok = same_choice(str(wanted), text)
    elif unit == "send":  # Renoise shows the send track's number, from 0
        ok = same_choice(str(wanted), text) or text.strip() == str(value["index"])
    elif unit in ("Hz", "ms", "%", "number"):
        found = shown_number(text, unit)
        slack = max(
            SHOWN_SLACK.get(unit, 0.0) * abs(value),
            SHOWN_SLACK_PERCENT if unit == "%" else 0.0,
            resolution(text, unit),
        )
        ok = found is not None and abs(found - value) <= slack
    elif unit == "dB":
        found = shown_number(text, unit)
        silent = value <= song_plan.mixplan.SILENT_DB and found is not None and found <= value
        ok = found is not None and (silent or abs(found - value) <= SHOWN_SLACK_DB)
    else:
        ok = True  # a raw value or a share of the range: shown however Renoise likes
    return "" if ok else f"{names[0]} shows {text!r}, not {wanted}"


def devices_wrong(planned: dict[str, Any], outcome: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Each planned parameter that doesn't show what the plan asks for; and,
    once for each kind of device, each optional preset setting that Renoise
    didn't take, which keeps Renoise's own value (the tool lets it)."""
    mix = planned["mix"]
    built = {t["track"]: t["devices"] for s in outcome.get("shown", []) for t in s["tracks"]}
    tracks = [*mix["tracks"], mix["master"], *mix["sends"]]  # in the order the tool lists them
    wrong, kept = [], set()
    for label, entry in zip(built, tracks, strict=False):
        for device, plan in zip(built[label], entry["devices"], strict=False):
            for setting in plan["params"]:
                problem = mismatch(setting, device["shows"] or {})
                if problem and setting.get("optional") and setting["unit"] == "preset":
                    kept.add(f"{device.get('path', device['name'])}: {problem}")
                elif problem:
                    wrong.append(f"{label}, {device['name']}: {problem}")
    return wrong, sorted(kept)


def released_wrong(planned: dict[str, Any], outcome: dict[str, Any]) -> list[str]:
    """Each synth envelope whose release doesn't show its synth's release time."""
    releases = {t["name"]: 1000 * t["release"] for t in planned["tracks"] if "zones" in t}
    wrong = []
    for song in outcome.get("shown", []):
        for envelope in song.get("envelopes", []):
            name, text = envelope["instrument"], envelope["release"]
            wanted = releases[name.removesuffix(" accent")]
            found = shown_number(text, "ms")
            if found is None or abs(found - wanted) > SHOWN_SLACK["ms"] * wanted:
                wrong.append(f"{name}, its envelope: Release shows {text!r}, not {wanted:g} ms")
    if not any(song.get("envelopes") for song in outcome.get("shown", [])):
        wrong.append("no synth envelope was reported")
    return wrong


def report_devices(outcome: dict[str, Any]) -> None:
    """Each kind of device's parameters as Renoise names and shows them."""
    for path, parameters in sorted((outcome.get("devices") or {}).items()):
        print(f"  {path}:")
        for p in parameters:
            low, default, high = (p.get("shows") or ["?", "?", "?"])[:3]
            print(f"    {p['name']}: {low} | {default} | {high} (quantum {p.get('quantum')})")


def report_settings(outcome: dict[str, Any]) -> None:
    """The optional settings Renoise doesn't have, which keep Renoise's own
    values, and the other settings in those devices' preset data, once per kind."""
    missing, presets = set(), {}
    for song in outcome.get("shown", []):
        for track in song["tracks"]:
            for device in track["devices"]:
                kind = device.get("path", device["name"])
                missing |= {(kind, name) for name in device.get("missing", [])}
                if device.get("preset"):
                    presets.setdefault(kind, device["preset"])
    for kind, name in sorted(missing):
        print(f"  {kind} has no {name} (optional: it keeps its own)")
    for kind, tags in sorted(presets.items()):
        print(f"  {kind} preset data: {', '.join(tags)}")


def level_db(data: np.ndarray, rate: int, window: tuple[float, float]) -> float:
    first, last = (round(s * rate) for s in window)
    part = data[first:last]
    return float(10 * np.log10(np.mean(part**2) + 1e-20))


def envelope_db(data: np.ndarray, rate: int, window: tuple[float, float]) -> np.ndarray:
    """The level in 2 ms frames, a millisecond apart, from the window's start."""
    mono = data.mean(axis=1) if data.ndim > 1 else data
    frame, hop = round(0.002 * rate), round(0.001 * rate)
    first, last = (round(s * rate) for s in window)
    starts = range(first, last - frame, hop)
    return np.array([10 * np.log10(np.mean(mono[k : k + frame] ** 2) + 1e-20) for k in starts])


def mono(data: np.ndarray) -> np.ndarray:
    flat: np.ndarray = (data.mean(axis=1) if data.ndim > 1 else data).astype(np.float64)
    return flat


def lag_samples(late: np.ndarray, early: np.ndarray, limit: int = LAG_LIMIT) -> int:
    """How many samples ``late`` runs behind ``early`` (negative: ahead), where
    the two line up best, by cross-correlation."""
    a, b = mono(late), mono(early)
    n = min(len(a), len(b))
    size = 1 << (2 * n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(a[:n], size) * np.conj(np.fft.rfft(b[:n], size)), size)
    near = np.concatenate([corr[-limit:], corr[: limit + 1]])  # lags -limit .. limit
    return int(np.argmax(near)) - limit


def envelope(data: np.ndarray) -> np.ndarray:
    """A signal's amplitude at every sample (the analytic signal's size), so
    a steady tone's level can be read between its zero crossings."""
    flat = mono(data)
    n = len(flat)
    weights = np.zeros(n)
    weights[0] = 1.0
    weights[1 : (n + 1) // 2] = 2.0
    if n % 2 == 0:
        weights[n // 2] = 1.0
    size: np.ndarray = np.abs(np.fft.ifft(np.fft.fft(flat) * weights))
    return size


def duck_shape(
    stem: np.ndarray, dry: np.ndarray, rate: int, kicks: Sequence[float]
) -> list[tuple[float, float]]:
    """The duck's gain (dB) at each of SHAPE_MS around a kick, the median over
    every kick but the first (which starts the song already dipped)."""
    ratio = envelope(stem) / np.maximum(envelope(dry), 1e-9)
    found = []
    for ms in SHAPE_MS:
        at = [round((kick + ms / 1000) * rate) for kick in kicks[1:]]
        gains = [20 * math.log10(max(ratio[k], 1e-9)) for k in at if 0 <= k < len(ratio)]
        found.append((ms, float(np.median(gains)) if gains else math.nan))
    return found


def worst_places(
    stem: np.ndarray, dry: np.ndarray, rate: int, chain: score.Sidechain, kicks: Sequence[float]
) -> str:
    """Where a target's deepest moment and its biggest step on the way back
    fall, in ms after their kick, looked for where REQ-BEN-12's checks look,
    in the same half-millisecond windows."""
    frames = round(checks.DUCK_WINDOW_S * rate)
    gain = audio.gain_curve(stem, dry, frames)
    window = frames / rate
    search = math.ceil((chain.attack_ms / 1000 + checks.DUCK_DIP_SEARCH_S) / window)
    course = chain.attack_ms + chain.hold_ms + chain.release_ms
    recover = round((course / 1000 + checks.DUCK_RECOVER_AFTER_S) / window)
    deep, step = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)  # (dB, kick, ms after it)
    for n, kick in enumerate(kicks):
        start = int(kick / window)
        near = gain[start : start + search]
        if near.size == 0 or np.isnan(near).all():
            continue
        deepest = start + int(np.nanargmin(near))
        following = int(kicks[n + 1] / window) - 1 if n + 1 < len(kicks) else len(gain)
        end = min(start + recover, following)
        back = np.abs(np.diff(gain[deepest : end + 1]))
        if -gain[deepest] > deep[0]:
            deep = (-float(gain[deepest]), kick, 1000 * (deepest * window - kick))
        if back.size and not np.isnan(back).all() and np.nanmax(back) > step[0]:
            at = deepest + 1 + int(np.nanargmax(back))
            step = (float(np.nanmax(back)), kick, 1000 * (at * window - kick))
    return (
        f"deepest {deep[0]:.1f} dB down, {deep[2]:+.1f} ms after the kick at {deep[1]:g} s; "
        f"biggest step on the way back {step[0]:.2f} dB, {step[2]:+.1f} ms after the kick "
        f"at {step[1]:g} s"
    )


def first_windows(stem: np.ndarray, dry: np.ndarray, rate: int) -> str:
    """A target's first FIRST_WINDOWS half-millisecond windows: its level
    against its dry render (dB), then the dry render's own level (dBFS)."""
    frames = round(checks.DUCK_WINDOW_S * rate)
    gain = audio.gain_curve(stem, dry, frames)[:FIRST_WINDOWS]
    alone = mono(dry)[: FIRST_WINDOWS * frames].astype(np.float64).reshape(-1, frames)
    level = 10 * np.log10(np.maximum((alone**2).mean(axis=1), 1e-30))
    return ", ".join(f"{g:.1f} | {d:.1f}" for g, d in zip(gain, level, strict=False))


def mixed_chain() -> tuple[score.Sidechain, list[float]]:
    """M's sidechain and its kicks."""
    loaded = score.load_text(MIXED)
    chain = loaded.sidechain
    if chain is None:
        raise CheckFailed("M's score has no sidechain")
    return chain, [e.time_s for e in score.events(loaded) if e.voice == chain.trigger]


def duck_checks(stem: np.ndarray, dry: np.ndarray, rate: int, target: str) -> list[checks.Check]:
    """REQ-BEN-12's checks of a target's duck at every kick, as a render's
    report makes them, from the target rendered with its duck and without."""
    chain, kicks = mixed_chain()
    return checks.sidechain(chain, target, stem, dry, kicks, rate=rate)


def report_checks(title: str, found: Sequence[checks.Check]) -> list[str]:
    """Prints a target's checks; returns the ones that failed."""
    print(title)
    for check in found:
        note = f" ({check.note})" if check.note else ""
        verdict = "" if check.passed else "  FAILED"
        print(f"  {check.name}: {check.value + 0.0:g}, limit {check.limit}{note}{verdict}")
    return [f"{c.name} is {c.value + 0.0:g}, not {c.limit}" for c in found if not c.passed]


def sends_wrong(mixed: np.ndarray, dry: np.ndarray, rate: int) -> list[str]:
    """What's wrong with the stab's sends, from the stab rendered with its
    strip, sends and their effects (``mixed``) and dry: whether it still
    sounds on its own track, and where its echo comes back."""
    wrong = []
    window = (STAB_NOTE_S + ONSET_WINDOW_S[0], STAB_NOTE_S + ONSET_WINDOW_S[1])
    lost = level_db(dry, rate, window) - level_db(mixed, rate, window)
    if lost > KEPT_DB:
        wrong.append(f"the stab is {lost:.1f} dB quieter while it sends: its #Send mutes it")
    levels = envelope_db(mixed, rate, ECHO_SEARCH_S)
    if not len(levels):
        return [*wrong, "M stab is too short to hear the echo"]
    peak_s = ECHO_SEARCH_S[0] + 0.001 * int(np.argmax(levels))
    thrown = level_db(dry, rate, (THROWN_S + ONSET_WINDOW_S[0], THROWN_S + ONSET_WINDOW_S[1]))
    if abs(peak_s - (THROWN_S + ECHO_S)) > ECHO_SLACK_S or levels.max() < thrown - ECHO_FLOOR_DB:
        wrong.append(
            f"the stab's echo should come back at {THROWN_S + ECHO_S:.3f} s, a dotted eighth "
            f"after the note thrown into it, but the loudest sound then is at {peak_s:.3f} s, "
            f"{thrown - levels.max():.1f} dB under the note"
        )
    return wrong


def listen_to_duck(folder: Path) -> list[str]:
    """What the ducked renders say, printed in full before any verdict: how
    far apart each pair of renders is, the duck's shape on the steady tone,
    and REQ-BEN-12's checks on the tone and the bass. Returns what failed."""
    chain, kicks = mixed_chain()
    failed = []
    for target, name in (("tone", "M tone"), ("bass", "M")):
        stem, rate = audio.read(folder / f"{name} stem.wav")
        dry, _ = audio.read(folder / f"{name} dry.wav")
        print(f"{name} stem runs {lag_samples(stem, dry)} samples behind {name} dry")
        if target == "tone":
            shape = ", ".join(f"{ms:+g}: {db:.1f}" for ms, db in duck_shape(stem, dry, rate, kicks))
            print(f"the duck on the steady tone, around a kick (ms: dB): {shape}")
        print(f"the {target}, ducked: {worst_places(stem, dry, rate, chain, kicks)}")
        print(
            f"the {target}'s first 2 ms, in 0.5 ms windows (ducked against dry, dB | "
            f"dry, dBFS): {first_windows(stem, dry, rate)}"
        )
        found = duck_checks(stem, dry, rate, target)
        failed += report_checks(f"the {target}'s duck, by REQ-BEN-12's checks:", found)
    return failed


def listen(folder: Path) -> None:
    """What M's renders say: whether the duck passes REQ-BEN-12 on a steady
    tone and on the bass, and the stab's sends."""
    failed = listen_to_duck(folder)
    if failed:
        raise CheckFailed("the duck fails REQ-BEN-12: " + "; ".join(failed))
    mixed, rate = audio.read(folder / "M stab.wav")
    alone, _ = audio.read(folder / "M stab dry.wav")
    wrong = sends_wrong(mixed, alone, rate)
    if wrong:
        raise CheckFailed("; ".join(wrong))
    print("the stab keeps sounding while it sends, and its echo comes back on time")


class Check:
    def __init__(self, cfg: Config, runner: Runner, home: Path, clock: bridge.Clock) -> None:
        self.cfg, self.runner, self.home, self.clock = cfg, runner, home, clock
        self.session = bridge.Session()
        self.asked_to_quit = False

    def job(self, name: str, songs: Sequence[luadata.Data]) -> dict[str, Any]:
        return bridge.run_job(
            self.cfg,
            self.runner,
            self.home,
            name,
            {"songs": list(songs)},
            timeout_s=JOB_TIMEOUT_S,
            clock=self.clock,
            session=self.session,
        )

    def open(self, path: Path) -> None:
        bridge.open_song(self.runner, path)
        self.clock.sleep(LOAD_S)

    def run(self, folder: Path) -> None:
        sample = blip(folder / "blip.wav")
        self.job("renoise-check-1", [song(folder, sample, "A"), song(folder, sample, "B")])
        print("job 1: built, rendered and saved A and B")
        self.open(folder / "A.xrns")
        print('opened A, as "Open in Renoise" does')
        self.job("renoise-check-2", [song(folder, sample, "C")])
        if [kept.song.name for kept in self.session.kept] != ["A.xrns"]:
            raise CheckFailed("job 2 didn't keep a copy of A, the song opened by hand")
        copy = self.session.kept[0].copy
        if not copy.is_file():
            raise CheckFailed(f"job 2 said it kept A as {copy}, but that file isn't there")
        print(f"job 2: kept A as {copy.name}, then built, rendered and saved C")
        self.mixer(folder, sample)
        self.open(folder / "B.xrns")
        print("opened B")
        self.quit()

    def mixer(self, folder: Path, sample: Path) -> None:
        planned = mixed_song(folder, sample)
        try:
            outcome = self.job("renoise-check-3", [planned])
        except bridge.BridgeError as failed:
            print("job 3 failed. What Renoise has:")
            report_devices(failed.outcome)
            report_settings(failed.outcome)
            raise
        print("job 3: built, rendered and saved M, a song with a mix and sampled synths")
        print("what each kind of device has (lowest | default | highest):")
        report_devices(outcome)
        report_settings(outcome)
        refused = sum(song.get("refused_ends", 0) for song in outcome.get("shown", []))
        if refused:
            print(
                f"Renoise refused points after the start of a pattern's last line in {refused} "
                "automations: they were kept to that line"
            )
        wrong, kept = devices_wrong(planned, outcome)
        for line in kept:
            print(f"  {line} (optional: it keeps its own)")
        wrong += released_wrong(planned, outcome)
        if wrong:
            raise CheckFailed("M's devices don't show what was planned:\n  " + "\n  ".join(wrong))
        print("every planned parameter shows what was asked")
        listen(folder)

    def quit(self) -> None:
        self.asked_to_quit = True
        asked = self.runner.run(QUIT)
        if not asked.ok:
            raise CheckFailed(
                f"couldn't ask Renoise to quit ({(asked.stderr or asked.stdout).strip()}). "
                "Quit it by hand: if it asks to save, the check failed"
            )
        deadline = self.clock.now() + QUIT_S
        while bridge.running(self.runner):
            if self.clock.now() > deadline:
                raise CheckFailed(
                    "Renoise is still open after being asked to quit, so it's asking "
                    "whether to save. Answer it, then say what it asked"
                )
            self.clock.sleep(1.0)
        print("asked Renoise to quit: it quit without asking to save")

    def close(self) -> None:
        """After a failure, asks the Renoise the check started to quit, so the
        next check or render finds it closed. Renoise was closed when the
        check began, so this is never someone's own session."""
        if self.asked_to_quit or not bridge.running(self.runner):
            return
        try:
            self.quit()
        except CheckFailed as exc:
            print(f"also: {exc}")


def check(cfg: Config, runner: Runner, home: Path, clock: bridge.Clock) -> int:
    if bridge.running(runner):
        print("Renoise is open. Save your work and quit it, then run the check again.")
        return 2
    scratch = bridge.scratch_dir(cfg)
    scratch.mkdir(parents=True, exist_ok=True)
    run = Check(cfg, runner, home, clock)
    with (
        tool_put_back(home),
        tempfile.TemporaryDirectory(prefix="renoise-check-", dir=scratch) as folder,
    ):
        try:
            bridge.install(cfg, home)
            run.run(Path(folder))
        except (bridge.BridgeError, CheckFailed) as exc:
            print(f"FAIL: {exc}")
            run.close()
            return 1
        finally:
            for kept in run.session.kept:
                kept.copy.unlink(missing_ok=True)
    print("PASS: Renoise never asked to save")
    return 0


def main() -> int:
    return check(config.load(), proc.SubprocessRunner(), Path.home(), bridge.Clock())


if __name__ == "__main__":
    sys.exit(main())
