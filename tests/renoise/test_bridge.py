from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mph import config
from mph.proc import Result
from mph.renoise import bridge, luadata
from tests.fakes import FakeRunner
from tests.renoise import lua


def cfg_for(tmp_path: Path) -> config.Config:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    return config.parse(raw)


def renoise_home(tmp_path: Path, *versions: str) -> Path:
    home = tmp_path / "home"
    for version in versions:
        (home / "Library/Preferences/Renoise" / version).mkdir(parents=True)
    home.mkdir(exist_ok=True)
    return home


class Tool:
    """Stands in for the Lua tool: answers a job as soon as mph starts waiting."""

    def __init__(self, result: dict[str, object] | None) -> None:
        self.result = result
        self.waited = 0.0
        self.jobs: list[str] = []
        self.target: Path | None = None

    def now(self) -> float:
        return self.waited

    def clock(self) -> bridge.Clock:
        return bridge.Clock(now=self.now, sleep=self.sleep)

    def sleep(self, seconds: float) -> None:
        self.waited += seconds
        if self.result is not None and self.target is not None and not self.target.exists():
            self.target.write_text(json.dumps(self.result), "utf-8")


def test_tools_folder_is_the_newest_renoise_version(tmp_path: Path) -> None:
    home = renoise_home(tmp_path, "V3.4.4", "V3.5.4", "V3.10.0", "Vbeta")
    assert bridge.tools_folder(home) == home / "Library/Preferences/Renoise/V3.10.0/Scripts/Tools"
    assert bridge.tools_folder(tmp_path / "nobody") is None


@pytest.mark.req("REQ-REN-03")
def test_install_copies_the_tool_and_names_the_job_folder(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    assert bridge.status(home) == "missing"
    target = bridge.install(cfg, home)
    assert sorted(p.name for p in target.iterdir()) == sorted([*bridge.TOOL_FILES, "settings.lua"])
    assert str(bridge.jobs_dir(cfg)) in (target / "settings.lua").read_text("utf-8")
    assert bridge.jobs_dir(cfg).is_dir()
    assert bridge.status(home) == "ready"
    (target / "main.lua").write_text("-- old", "utf-8")
    assert bridge.status(home) == "outdated"
    (target / "main.lua").unlink()
    assert bridge.status(home) == "missing"


def test_install_needs_renoise_to_have_run_once(tmp_path: Path) -> None:
    assert bridge.status(tmp_path) == "no-renoise"
    with pytest.raises(bridge.BridgeError, match="open Renoise once"):
        bridge.install(cfg_for(tmp_path), tmp_path)


@pytest.mark.req("REQ-REN-03")
def test_run_job_writes_the_job_starts_renoise_and_waits(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    bridge.install(cfg, home)
    runner = FakeRunner(responses={"pgrep": Result(1)})
    tool = Tool({"ok": True, "renoise": "3.5.4", "files": ["a.wav"]})
    tool.target = bridge.jobs_dir(cfg) / "BM-1.result.json"
    job: luadata.Data = {"songs": []}
    outcome = bridge.run_job(cfg, runner, home, "BM-1", job, timeout_s=5, clock=tool.clock())
    assert outcome["renoise"] == "3.5.4"
    assert (bridge.jobs_dir(cfg) / "BM-1.job.lua").read_text("utf-8") == luadata.document(
        {"songs": []}
    )
    assert ("open", "-g", "-a", bridge.APP) in [call for call, _ in runner.calls]
    assert not tool.target.exists()  # the result is consumed


def test_run_job_reports_renoise_errors_timeouts_and_a_missing_tool(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    runner = FakeRunner()
    with pytest.raises(bridge.BridgeError, match="isn't installed: run `mph renoise install`"):
        bridge.run_job(cfg, runner, home, "x", {}, timeout_s=1)
    target = bridge.install(cfg, home)
    (target / "build.lua").write_text("-- old", "utf-8")
    with pytest.raises(bridge.BridgeError, match="out of date"):
        bridge.run_job(cfg, runner, home, "x", {}, timeout_s=1)
    bridge.install(cfg, home)
    failing = Tool({"ok": False, "error": "could not load kick.wav"})
    failing.target = bridge.jobs_dir(cfg) / "x.result.json"
    with pytest.raises(bridge.BridgeError, match=r"Renoise: could not load kick\.wav"):
        bridge.run_job(cfg, runner, home, "x", {}, timeout_s=1, clock=failing.clock())
    assert [call for call, _ in runner.calls] == [("pgrep", "-x", "Renoise")]  # already running
    with pytest.raises(bridge.BridgeError, match="didn't finish within 1 s"):
        bridge.run_job(cfg, runner, home, "y", {}, timeout_s=1, clock=Tool(None).clock())


def test_launch_and_open_report_failures() -> None:
    broken = FakeRunner(responses={"open": Result(1, stderr="no such app")})
    with pytest.raises(bridge.BridgeError, match=r"couldn't start Renoise: no such app"):
        bridge.launch(broken)
    with pytest.raises(bridge.BridgeError, match=r"couldn't open T\.xrns: no such app"):
        bridge.open_song(broken, Path("/r/T.xrns"))
    runner = FakeRunner()
    bridge.open_song(runner, Path("/r/T.xrns"))
    assert runner.calls == [(("open", "-a", bridge.APP, "/r/T.xrns"), False)]
    assert bridge.app_installed() in (True, False)


def _job(tmp_path: Path, sample: Path) -> dict[str, luadata.Data]:
    track: dict[str, luadata.Data] = {
        "name": "kick",
        "sample": str(sample),
        "volume": 1.0,
        "transpose": 0,
        "fine_tune": 0,
        "slices": [],
        "notes": [{"line": 0, "delay": 0, "volume": 128}],
    }
    song: dict[str, luadata.Data] = {
        "bpm": 120,
        "lpb": 4,
        "pattern_lines": 64,
        "lines": 16,
        "rate": 48000,
        "tracks": [track],
    }
    return {
        "songs": [
            {
                **song,
                "save": str(tmp_path / "out" / "a.xrns"),
                "renders": [
                    {"file": str(tmp_path / "out" / "mix.wav")},
                    {"file": str(tmp_path / "out" / "kick.wav"), "solo": 1},
                ],
            },
            {**song, "save": str(tmp_path / "out" / "b.xrns"), "renders": []},
        ]
    }


@pytest.mark.lua
@pytest.mark.req("REQ-REN-03")
def test_the_tool_runs_a_job_from_the_folder_to_a_result(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    installed = bridge.install(cfg, home)
    (tmp_path / "out").mkdir()
    sample = tmp_path / "kick.wav"
    sample.write_bytes(b"x")
    result = bridge.submit(cfg, "BM-1", _job(tmp_path, sample))

    seen = lua.run("job", str(installed), "12")

    outcome = json.loads(result.read_text("utf-8"))
    assert outcome["ok"] is True
    assert outcome["renoise"] == "3.5.4 (fake)"
    assert outcome["files"] == [
        str(tmp_path / "out" / "mix.wav"),
        str(tmp_path / "out" / "kick.wav"),
    ]
    assert seen["renders"][0]["rate"] == 48000
    assert [r["active"] for r in seen["renders"]] == [["kick"], ["kick"]]
    # Each song is saved after building, and again with nothing muted after rendering.
    out = str(tmp_path / "out")
    assert seen["saved"] == [f"{out}/a.xrns", f"{out}/a.xrns", f"{out}/b.xrns", f"{out}/b.xrns"]
    assert sorted(p.name for p in bridge.jobs_dir(cfg).iterdir()) == ["BM-1.result.json"]


@pytest.mark.lua
def test_the_tool_reports_failures_in_the_result(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    installed = bridge.install(cfg, home)
    (tmp_path / "out").mkdir()
    missing = bridge.submit(cfg, "missing", _job(tmp_path, tmp_path / "nowhere.wav"))
    lua.run("job", str(installed), "6")
    assert "could not load" in json.loads(missing.read_text("utf-8"))["error"]

    shutil.rmtree(bridge.jobs_dir(cfg))
    busy = bridge.submit(cfg, "busy", _job(tmp_path, tmp_path / "nowhere.wav"))
    lua.run("job", str(installed), "3", "refuse")
    assert "didn't start a new song" in json.loads(busy.read_text("utf-8"))["error"]

    shutil.rmtree(bridge.jobs_dir(cfg))
    broken = bridge.submit(cfg, "broken", {"songs": []})
    broken.with_name("broken.job.lua").write_text("return 'not a job'", "utf-8")
    lua.run("job", str(installed), "2")
    assert "unreadable job" in json.loads(broken.read_text("utf-8"))["error"]


@pytest.mark.req("REQ-REN-03")
def test_renoise_commands_install_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mph import cli  # noqa: PLC0415
    from mph.renoise import commands  # noqa: PLC0415

    cfg = cfg_for(tmp_path)

    def run(action: str, home: Path) -> int:
        return commands.dispatch(cli.build_parser().parse_args(["renoise", action]), cfg, home)

    assert run("status", tmp_path) == 1
    assert "open it once" in capsys.readouterr().out
    assert run("install", tmp_path) == 1
    assert "open Renoise once" in capsys.readouterr().err
    home = renoise_home(tmp_path, "V3.5.4")
    assert run("status", home) == 1
    assert "isn't installed" in capsys.readouterr().out
    assert run("install", home) == 0
    assert "Restart Renoise" in capsys.readouterr().out
    assert run("status", home) == 0
    assert "up to date" in capsys.readouterr().out
