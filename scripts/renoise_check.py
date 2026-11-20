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
   the bass's duck must dip it about 12 dB at the first kick, the stab must
   still sound on its own track while it sends (its #Send keeps its source),
   and the stab's echo must come back a dotted eighth after the note thrown
   into it. It prints each kind of device's parameters as Renoise names and
   shows them, the optional settings Renoise doesn't have, and the other
   settings in those devices' preset data;
4. B is opened the same way, and Renoise is asked to quit.

Renoise has to do all of that without asking to save anything: a question
would hold up a job, or keep Renoise open after it was asked to quit. The
songs live in a temporary folder inside mph's scratch folder and are deleted
at the end, with A's copy. The bridge tool that was installed before is put
back. Sources, renders and the catalogue aren't touched.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from mph import config, proc
from mph.bench import audio, sampling, score
from mph.config import Config
from mph.proc import Runner
from mph.renoise import bridge, luadata
from mph.renoise import plan as song_plan

RATE = 48000
JOB_TIMEOUT_S = 300.0  # longer than the tool waits for a new song, so its own error shows
LOAD_S = 5.0  # time for Renoise to open a song
QUIT_S = 30.0  # time for Renoise to quit
QUIT = ("osascript", "-e", 'tell application "Renoise" to quit')
DUCK_DB = (9.0, 14.0)  # the dip at the first kick, 5 to 20 ms in: about 11.9 dB planned
DUCK_WINDOW_S = (0.005, 0.02)
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
targets = ["bass"]
depth_db = 12
release_ms = 300
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
    """Song M: the mixed score, its synths sampled from the blip. Rendered as a
    mix after a warm-up, then the bass alone with its duck (a stem) and without."""
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
    ]
    renders: list[luadata.Data] = [
        {"file": str(folder / "M warm-up.wav")},
        {"file": str(folder / "M.wav")},
        {"file": str(folder / "M stem.wav"), "solo": 2, "mode": "stem"},
        {"file": str(folder / "M dry.wav"), "solo": 2, "mode": "dry"},
        {"file": str(folder / "M stab.wav"), "solo": 3},  # with its strip, sends and their effects
        {"file": str(folder / "M stab dry.wav"), "solo": 3, "mode": "dry"},
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


def devices_wrong(planned: dict[str, Any], outcome: dict[str, Any]) -> list[str]:
    """Each planned parameter that doesn't show what the plan asks for."""
    mix = planned["mix"]
    built = {t["track"]: t["devices"] for s in outcome.get("shown", []) for t in s["tracks"]}
    tracks = [*mix["tracks"], mix["master"], *mix["sends"]]  # in the order the tool lists them
    wrong = []
    for label, entry in zip(built, tracks, strict=False):
        for device, plan in zip(built[label], entry["devices"], strict=False):
            for setting in plan["params"]:
                problem = mismatch(setting, device["shows"] or {})
                if problem:
                    wrong.append(f"{label}, {device['name']}: {problem}")
    return wrong


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


def level_db(data: np.ndarray, rate: int, window: tuple[float, float] = DUCK_WINDOW_S) -> float:
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


class Check:
    def __init__(self, cfg: Config, runner: Runner, home: Path, clock: bridge.Clock) -> None:
        self.cfg, self.runner, self.home, self.clock = cfg, runner, home, clock
        self.session = bridge.Session()

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
        wrong = devices_wrong(planned, outcome) + released_wrong(planned, outcome)
        if wrong:
            raise CheckFailed("M's devices don't show what was planned:\n  " + "\n  ".join(wrong))
        print("every planned parameter shows what was asked")
        stem, rate = audio.read(folder / "M stem.wav")
        dry, _ = audio.read(folder / "M dry.wav")
        dip = level_db(dry, rate) - level_db(stem, rate)
        if not DUCK_DB[0] <= dip <= DUCK_DB[1]:
            raise CheckFailed(
                f"the duck dipped the bass {dip:.1f} dB at the first kick, not about 12"
            )
        print(f"the duck dipped the bass {dip:.1f} dB at the first kick")
        mixed, _ = audio.read(folder / "M stab.wav")
        alone, _ = audio.read(folder / "M stab dry.wav")
        wrong = sends_wrong(mixed, alone, rate)
        if wrong:
            raise CheckFailed("; ".join(wrong))
        print("the stab keeps sounding while it sends, and its echo comes back on time")

    def quit(self) -> None:
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
