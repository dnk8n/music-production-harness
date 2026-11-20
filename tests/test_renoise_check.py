"""scripts/renoise_check.py, the real-Renoise check of the bridge tool (REQ-REN-08)."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from mph import config
from mph.bench import audio
from mph.proc import Result
from mph.renoise import bridge, luadata
from tests.fakes import FakeRunner
from tests.renoise import lua

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "renoise_check.py"


@pytest.fixture(scope="module")
def renoise_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renoise_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KICKS = [0.5 * beat for beat in range(8)]  # the mixed song's kicks: every beat for 2 bars
LONG = np.arange(4 * 48000) / 48000  # the mixed song's 2 bars, in seconds


def ducked(dry: np.ndarray, depth_db: float, late_s: float) -> np.ndarray:
    """``dry`` ducked at every kick as the check's score asks (down over 2 ms,
    back along a half cosine over 430 ms), ``late_s`` late."""
    depth = 1 - 10 ** (-depth_db / 20)
    taken = np.zeros_like(LONG)
    for kick in KICKS:
        after = LONG - kick - late_s
        down = np.clip(after / 0.002, 0, 1)
        back = (1 + np.cos(np.pi * np.clip((after - 0.002) / 0.43, 0, 1))) / 2
        taken += depth * np.where(after < 0.002, down, back)
    found: np.ndarray = dry * (1 - np.minimum(taken, depth))[:, None]
    return found


class Renoise:
    """Renoise and the bridge tool as the check sees them: a process that
    starts and quits, and jobs answered as soon as mph waits for them. The
    mixed song is built by the tool in the fake Renoise (tests/renoise). Its
    bass and tone stems are their dry renders ducked ``duck_db`` at every
    kick, ``duck_late_s`` late (the tone's ``lag`` samples later still); and
    its stab, rendered with its sends, is the dry stab (unless ``muted``)
    plus the thrown note's echo, ``echo_s`` later."""

    def __init__(
        self,
        cfg: config.Config,
        *,
        keeps: bool = True,
        quits: bool = True,
        duck_db: float = 24.0,
        duck_late_s: float = 0.0,
        lag: int = 0,
        echo_s: float = 0.375,
        muted: bool = False,
    ) -> None:
        self.cfg, self.keeps, self.quits = cfg, keeps, quits
        self.duck_db, self.duck_late_s, self.lag = duck_db, duck_late_s, lag
        self.echo_s, self.muted = echo_s, muted
        self.open = False
        self.opened: list[str] = []
        self.waited = 0.0

    def runner(self) -> FakeRunner:
        def pgrep(_argv: Sequence[str]) -> Result:
            return Result(0 if self.open else 1)

        def open_app(argv: Sequence[str]) -> Result:
            self.open = True
            if argv[-1].endswith(".xrns"):
                self.opened.append(Path(argv[-1]).name)
            return Result(0)

        def osascript(_argv: Sequence[str]) -> Result:
            self.open = not self.quits
            return Result(0)

        return FakeRunner(responses={"pgrep": pgrep, "open": open_app, "osascript": osascript})

    def clock(self) -> bridge.Clock:
        return bridge.Clock(now=lambda: self.waited, sleep=self.sleep)

    def sleep(self, seconds: float) -> None:
        self.waited += seconds
        for job in sorted(bridge.jobs_dir(self.cfg).glob("*.job.lua")):
            for saved in saves(job):
                Path(saved).write_text("xrns", "utf-8")
            result: dict[str, Any] = {"ok": True, "files": [], "kept": []}
            if job.name.startswith("renoise-check-3"):
                result.update(self.mixed(job))
            job.unlink()
            if job.name.startswith("renoise-check-2") and self.keeps:
                opened = next(bridge.scratch_dir(self.cfg).glob("renoise-check-*/A.xrns"))
                copy = bridge.scratch_dir(self.cfg) / "20261113-081500 A.xrns"
                copy.write_text("A, as it was", "utf-8")
                result["kept"] = [{"from": str(opened), "to": str(copy)}]
            job.with_name(job.name.replace(".job.lua", ".result.json")).write_text(
                json.dumps(result), "utf-8"
            )

    def mixed(self, job: Path) -> dict[str, Any]:
        (song,) = lua.run("data", str(job))["songs"]
        plan = job.with_name("mixed.plan.lua")
        plan.write_text(luadata.document(song), "utf-8")
        built = lua.run("build", str(plan))
        plan.unlink()
        assert built.get("error") is None, built["error"]
        renders = {Path(r["file"]).name: Path(r["file"]) for r in song["renders"]}
        dry = (0.5 * np.sin(2 * np.pi * 440 * LONG))[:, None]
        audio.write_float(renders["M dry.wav"], dry, 48000)
        audio.write_float(renders["M stem.wav"], ducked(dry, self.duck_db, self.duck_late_s), 48000)
        noise = np.random.default_rng(7).standard_normal(len(LONG))
        steady = (0.5 * np.sin(2 * np.pi * 1000 * LONG) + 0.005 * noise)[:, None]
        tone = np.roll(ducked(steady, self.duck_db, self.duck_late_s), self.lag, axis=0)
        audio.write_float(renders["M tone dry.wav"], steady, 48000)
        audio.write_float(renders["M tone stem.wav"], tone, 48000)
        t = np.arange(4800) / 48000
        stab = np.zeros((5 * 48000, 2), dtype=np.float32)  # two bars and the tail
        blip = 0.5 * np.sin(2 * np.pi * 440 * t) * np.exp(-30 * t)
        for start in (0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75):  # the stab's notes
            stab[round(start * 48000) : round(start * 48000) + len(t)] += blip[:, None]
        sent = stab * (0.0 if self.muted else 0.7)  # its pan and high-pass cost a little
        at = round((1.25 + self.echo_s) * 48000)
        sent[at : at + len(t), 0] += 0.6 * blip  # the echo, on the left
        audio.write_float(renders["M stab dry.wav"], stab, 48000)
        audio.write_float(renders["M stab.wav"], sent, 48000)
        return {"devices": built["described"], "shown": [{"song": "M", **built["shown"]}]}


def saves(job: Path) -> list[str]:
    """Where a job's songs are saved, read from its Lua text."""
    return [
        line.split('"')[1]
        for line in job.read_text("utf-8").splitlines()
        if line.strip().startswith("save = ")
    ]


def setup(tmp_path: Path) -> tuple[config.Config, Path, Path]:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    cfg = config.parse(raw)
    home = tmp_path / "home"
    tools = home / "Library/Preferences/Renoise/V3.5.4/Scripts/Tools"
    installed = tools / bridge.TOOL_FOLDER
    installed.mkdir(parents=True)
    (installed / "main.lua").write_text("-- the tool from main", "utf-8")
    return cfg, home, installed


@pytest.mark.req("REQ-REN-08")
def test_the_check_passes_when_renoise_never_asks(
    renoise_check: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, home, installed = setup(tmp_path)
    renoise = Renoise(cfg)
    assert renoise_check.check(cfg, renoise.runner(), home, renoise.clock()) == 0
    out = capsys.readouterr().out
    assert "job 2: kept A as 20261113-081500 A.xrns" in out
    assert "    Cutoff: 0.02 kHz | 20.00 kHz | 20.00 kHz (quantum 0)" in out  # the filter's
    assert "  Audio/Effects/Native/mpReverb 2 has no High Cut (optional: it keeps its own)" in out
    assert (
        "  Audio/Effects/Native/#Send preset data: MuteSource=false, "
        "SmoothParameterChanges=true, ApplyPostVolume=true"
    ) in out
    assert (
        "  Audio/Effects/Native/Digital Filter preset data: IsMaximized=true, "
        "OversamplingFactor=2x, Model=Butterworth 4n, ShowResponseView=true, "
        "ResponseViewMaxGain=18"
    ) in out
    # The Duck steps once a tick: its smoothing is off (REQ-REN-11).
    assert "  Audio/Effects/Native/Gainer preset data: SmoothParameterChanges=false" in out
    assert "every planned parameter shows what was asked" in out
    # The duck, the remix's own (24 dB in 2 ms, back over 430 ms): how far
    # apart each pair of renders is, its shape on the steady tone, where the
    # worst moments fall, and REQ-BEN-12's checks on the tone and the bass.
    assert "M tone stem runs 0 samples behind M tone dry" in out
    assert (
        "the duck on the steady tone, around a kick (ms: dB): -30: -0.0, -25: -0.0, "
        "-20: -0.0, -15: -0.0, -10: -0.0, -5: -0.0, -2.5: 0.0, +0: -0.2, +2.5: -24.4, "
        "+5: -24.0, +10: -23.9, +20: -23.5, +50: -20.8"
    ) in out
    assert "the tone, ducked: deepest 24.0 dB down, +2.0 ms after the kick" in out
    assert "  sidechain: tone dip reached (ms): 2.5, limit <= 5" in out
    assert "M stem runs 0 samples behind M dry" in out
    assert (
        "the bass, ducked: deepest 24.0 dB down, +2.0 ms after the kick at 0 s; "
        "biggest step on the way back 0.08 dB, +70.5 ms after the kick at 0 s"
    ) in out
    # The bass's first moments, where a part that starts with the song starts
    # from silence: against its dry render, and the dry render's own level.
    assert (
        "the bass's first 2 ms, in 0.5 ms windows (ducked against dry, dB | dry, dBFS): "
        "-1.5 | -9.9, -3.3 | -7.5, -8.8 | -13.0, -14.0 | -6.7"
    ) in out
    assert "  sidechain: bass dip (dB): 24, limit 24 ± 1 (8 of 8 hits measured)" in out
    assert "  sidechain: bass dip reached (ms): 2.5, limit <= 5" in out
    assert "FAILED" not in out
    assert "the stab keeps sounding while it sends, and its echo comes back on time" in out
    assert out.strip().endswith("PASS: Renoise never asked to save")
    assert renoise.opened == ["A.xrns", "B.xrns"] and not renoise.open
    # The tool from before is back, and the check's songs and A's copy are gone.
    assert (installed / "main.lua").read_text("utf-8") == "-- the tool from main"
    assert list(bridge.scratch_dir(cfg).iterdir()) == []
    blip = renoise_check.blip(tmp_path / "blip.wav")
    assert blip.stat().st_size > renoise_check.RATE // 10 * 4  # a tenth of a second, float


@pytest.mark.req("REQ-REN-08")
def test_the_check_fails_when_renoise_asks_or_keeps_nothing(
    renoise_check: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, home, installed = setup(tmp_path)
    stuck = Renoise(cfg, quits=False)
    assert renoise_check.check(cfg, stuck.runner(), home, stuck.clock()) == 1
    assert "FAIL: Renoise is still open after being asked to quit" in capsys.readouterr().out
    assert (installed / "main.lua").read_text("utf-8") == "-- the tool from main"

    forgetful = Renoise(cfg, keeps=False)
    assert renoise_check.check(cfg, forgetful.runner(), home, forgetful.clock()) == 1
    assert "didn't keep a copy of A" in capsys.readouterr().out

    refused = Renoise(cfg)
    runner = refused.runner()
    runner.responses["osascript"] = Result(1, stderr="not allowed to send Apple events")
    assert renoise_check.check(cfg, runner, home, refused.clock()) == 1
    assert "couldn't ask Renoise to quit (not allowed" in capsys.readouterr().out

    already = Renoise(cfg)
    already.open = True
    assert renoise_check.check(cfg, already.runner(), home, already.clock()) == 2
    assert "Renoise is open" in capsys.readouterr().out


@pytest.mark.req("REQ-REN-08")
def test_the_check_reports_a_job_that_fails(
    renoise_check: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, home, installed = setup(tmp_path)

    class Broken(Renoise):
        def sleep(self, seconds: float) -> None:
            self.waited += seconds
            for job in bridge.jobs_dir(self.cfg).glob("*.job.lua"):
                job.with_name(job.name.replace(".job.lua", ".result.json")).write_text(
                    json.dumps({"ok": False, "error": "Renoise didn't start a new song"}), "utf-8"
                )
                job.unlink()

    broken = Broken(cfg)
    assert renoise_check.check(cfg, broken.runner(), home, broken.clock()) == 1
    assert "FAIL: Renoise: Renoise didn't start a new song" in capsys.readouterr().out
    assert (installed / "main.lua").read_text("utf-8") == "-- the tool from main"

    nowhere = tmp_path / "nobody"
    nowhere.mkdir()
    fresh = Renoise(cfg)
    assert renoise_check.check(cfg, fresh.runner(), nowhere, fresh.clock()) == 1
    assert "open Renoise once" in capsys.readouterr().out


@pytest.mark.lua
@pytest.mark.req("REQ-TRK-04")
def test_the_checks_songs_build_with_the_tool(renoise_check: ModuleType, tmp_path: Path) -> None:
    # The check builds its songs by hand, so they must keep up with the plan the
    # tool reads, or the real-Renoise check fails for the wrong reason.
    sample = renoise_check.blip(tmp_path / "blip.wav")
    plan = tmp_path / "plan.lua"
    plan.write_text(luadata.document(renoise_check.song(tmp_path, sample, "A")), "utf-8")
    built = lua.run("build", str(plan))
    assert built["sections"] == [{"position": 1, "line": 0, "name": "A"}]
    assert [t["name"] for t in built["tracks"]] == ["blip", "clip"]
    zone = built["tracks"][1]["zones"][0]
    assert (zone["name"], zone["base_note"], zone["autoseek"]) == ("blip", 48, True)


@pytest.mark.lua
@pytest.mark.req("REQ-REN-10")
@pytest.mark.req("REQ-REN-11")
def test_the_check_fails_when_the_mixer_isnt_as_planned(
    renoise_check: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, home, _ = setup(tmp_path)
    flat = Renoise(cfg, duck_db=0.0)
    assert renoise_check.check(cfg, flat.runner(), home, flat.clock()) == 1
    out = capsys.readouterr().out
    assert "FAIL: the duck fails REQ-BEN-12: sidechain: tone dip (dB) is 0, not 24 ± 1" in out
    # A duck that lands late, as one drawn at the kick did in Renoise
    # (2026-11-20), dips too slowly. The check still quits the Renoise it started.
    late = Renoise(cfg, duck_late_s=0.0104)
    assert renoise_check.check(cfg, late.runner(), home, late.clock()) == 1
    out = capsys.readouterr().out
    assert "  sidechain: tone dip reached (ms): 13, limit <= 5  FAILED" in out
    assert "  sidechain: bass dip reached (ms): 13, limit <= 5  FAILED" in out
    assert "FAIL: the duck fails REQ-BEN-12: sidechain: tone dip reached (ms) is 13" in out
    assert out.strip().endswith("asked Renoise to quit: it quit without asking to save")
    assert not late.open
    # Renders that don't line up are named, in samples.
    apart = Renoise(cfg, lag=30)
    assert renoise_check.check(cfg, apart.runner(), home, apart.clock()) == 0
    assert "M tone stem runs 30 samples behind M tone dry" in capsys.readouterr().out
    # A #Send that mutes its source, and an echo at a sixteenth's delay with
    # its first repeat at 1.625 s: both are caught.
    wrong = Renoise(cfg, echo_s=0.125, muted=True)
    assert renoise_check.check(cfg, wrong.runner(), home, wrong.clock()) == 1
    out = capsys.readouterr().out
    assert "FAIL: the stab is " in out and "while it sends: its #Send mutes it" in out
    assert "the stab's echo should come back at 1.625 s" in out
    assert "the loudest sound then is at 1.375 s" in out


@pytest.mark.req("REQ-REN-10")
def test_the_check_reads_what_renoise_shows_as_the_tool_does(renoise_check: ModuleType) -> None:
    # What a device shows is read the way the tool reads it, in its own unit only.
    shown, mismatch = renoise_check.shown_number, renoise_check.mismatch
    assert (shown("2.50 kHz", "Hz"), shown("1.5 s", "ms"), shown("-INF dB", "dB")) == (
        2500.0,
        1500.0,
        -float("inf"),
    )
    assert (shown("40.000 %", "%"), shown("6", "number")) == (40.0, 6.0)
    assert shown("Lowpass", "Hz") is None
    assert shown("40.000 %", "dB") is None  # a share is no level
    assert shown("-INF dB", "%") is None
    assert shown("6 lines", "number") is None
    assert renoise_check.resolution("0.03 kHz", "Hz") == pytest.approx(5.0)
    low = {"names": ["Cutoff"], "value": 25.0, "unit": "Hz"}
    assert mismatch(low, {"Cutoff": "0.03 kHz"}) == ""  # as near as Renoise shows it
    assert mismatch(low, {"Cutoff": "0.04 kHz"}) == "Cutoff shows '0.04 kHz', not 25.0"
    wet = {"names": ["Wet Mix"], "value": 100.0, "unit": "%"}
    assert mismatch(wet, {"Wet Mix": "100.000 %"}) == ""
    assert mismatch(wet, {"Wet Mix": "40.000 %"}) == "Wet Mix shows '40.000 %', not 100.0"
    lines = {"names": ["T1 L Sync Time"], "value": 6, "unit": "number"}
    assert mismatch(lines, {"T1 L Sync Time": "6"}) == ""
    assert mismatch(lines, {"T1 L Sync Time": "-1"}) == "T1 L Sync Time shows '-1', not 6"
    model = {"names": ["Model"], "value": "Butterworth 4n", "unit": "preset", "optional": True}
    assert mismatch(model, {"Model": "Butterworth 4n"}) == mismatch(model, {}) == ""
    assert mismatch(model, {"Model": "Biquad"}) == "Model shows 'Biquad', not Butterworth 4n"
    # An optional preset setting Renoise didn't take keeps its own, as the
    # tool lets it: a note once for each kind of device, not a failure.
    keep = {"names": ["MuteSource"], "value": "false", "unit": "preset"}
    filtered = {"params": [model]}
    planned_mix = {"tracks": [{"devices": [filtered, filtered, {"params": [keep]}]}]}
    shown_devices = [
        {"name": "Highpass", "path": "Filter", "shows": {"Model": "Biquad"}},
        {"name": "Lowpass", "path": "Filter", "shows": {"Model": "Biquad"}},
        {"name": "Send to echo", "path": "#Send", "shows": {"MuteSource": "true"}},
    ]
    assert renoise_check.devices_wrong(
        {"mix": {**planned_mix, "master": {"devices": []}, "sends": []}},
        {"shown": [{"tracks": [{"track": "bass", "devices": shown_devices}]}]},
    ) == (
        ["bass, Send to echo: MuteSource shows 'true', not false"],
        ["Filter: Model shows 'Biquad', not Butterworth 4n"],
    )
    cutoff = {"names": ["Cutoff", "Frequency"], "value": 400.0, "unit": "Hz"}
    assert mismatch(cutoff, {"Frequency": "405.0 Hz"}) == ""
    assert mismatch(cutoff, {"Cutoff": "1.20 kHz"}) == "Cutoff shows '1.20 kHz', not 400.0"
    assert mismatch(cutoff, {"Gain": "0 dB"}) == "Cutoff isn't there"
    assert mismatch({**cutoff, "optional": True}, {}) == ""
    silent = {"names": ["Amount"], "value": -90.0, "unit": "dB"}
    assert mismatch(silent, {"Amount": "-INF dB"}) == ""
    assert mismatch(silent, {"Amount": "-60.0 dB"}) == "Amount shows '-60.0 dB', not -90.0"
    planned = {"tracks": [{"name": "bass", "zones": [], "release": 0.02}]}
    envelopes = [{"instrument": "bass accent", "release": "45.0 ms"}]
    assert renoise_check.released_wrong(planned, {"shown": [{"envelopes": envelopes}]}) == [
        "bass accent, its envelope: Release shows '45.0 ms', not 20 ms"
    ]
    assert renoise_check.released_wrong(planned, {"shown": []}) == [
        "no synth envelope was reported"
    ]
    send = {"names": ["Receiver"], "value": {"name": "hall", "index": 1}, "unit": "send"}
    assert mismatch(send, {"Receiver": "02: hall"}) == mismatch(send, {"Receiver": "1"}) == ""
    assert mismatch(send, {"Receiver": "0"}) == "Receiver shows '0', not hall"
    mute = {"names": ["Mute Dry"], "value": "On", "unit": "choice"}
    assert mismatch(mute, {"Mute Dry": "Enabled"}) == ""
    assert mismatch(mute, {"Mute Dry": "Off"}) == "Mute Dry shows 'Off', not On"


@pytest.mark.lua
@pytest.mark.req("REQ-REN-09")
def test_the_checks_mixed_song_builds_with_the_tool(
    renoise_check: ModuleType, tmp_path: Path
) -> None:
    sample = renoise_check.blip(tmp_path / "blip.wav")
    plan = tmp_path / "plan.lua"
    plan.write_text(luadata.document(renoise_check.mixed_song(tmp_path, sample)), "utf-8")
    built = lua.run("build", str(plan))
    assert built.get("error") is None
    assert [t["name"] for t in built["mixer"]] == [
        "kick",
        "bass",
        "stab",
        "tone",
        "Master",
        "echo",
        "hall",
    ]
    assert built["instruments"] == 5  # the bass's accents get their own
