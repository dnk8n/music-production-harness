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
    job: dict[str, luadata.Data] = {"songs": []}
    outcome = bridge.run_job(cfg, runner, home, "BM-1", job, timeout_s=5, clock=tool.clock())
    assert outcome["renoise"] == "3.5.4"
    scratch = bridge.scratch_dir(cfg)
    assert (bridge.jobs_dir(cfg) / "BM-1.job.lua").read_text("utf-8") == luadata.document(
        {
            "songs": [],
            "scratch": {
                "song": str(scratch / "mph-scratch.xrns"),
                "folder": str(scratch),
                "ours": str(cfg.data_dir),
            },
        }
    )
    assert scratch.is_dir()  # Renoise saves into it, so it has to be there
    assert ("open", "-g", "-a", bridge.APP) in [call for call, _ in runner.calls]
    assert not tool.target.exists()  # the result is consumed


@pytest.mark.req("REQ-REN-08")
def test_run_job_remembers_songs_the_tool_saved_out_of_the_way(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    bridge.install(cfg, home)
    runner = FakeRunner()  # Renoise is running: this session started it
    session = bridge.Session(launched=True)
    song = cfg.data_dir / "renders" / "BM-1" / "abc" / "BM-1.xrns"
    copy = bridge.scratch_dir(cfg) / "20261113-081500 BM-1.xrns"
    kept = [{"from": str(song), "to": str(copy)}]
    for name, result in (("ok", {"ok": True, "kept": kept}), ("bad", {"ok": False, "kept": kept})):
        tool = Tool({**result, "error": "could not load kick.wav", "files": []})
        tool.target = bridge.jobs_dir(cfg) / f"{name}.result.json"
        try:
            bridge.run_job(
                cfg, runner, home, name, {}, timeout_s=5, clock=tool.clock(), session=session
            )
        except bridge.BridgeError:
            assert name == "bad"
    assert session.kept == [bridge.Kept(song, copy)] * 2  # kept, whether the render worked or not
    assert session.kept[0].note() == (
        f"Renoise had BM-1.xrns open, so mph saved it, with any changes, as {copy} "
        "before rendering. BM-1.xrns itself is unchanged."
    )
    assert bridge.kept_songs({"ok": True}) == []
    assert bridge.kept_songs({"kept": {}}) == []  # Lua writes an empty list as {}


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
    ours = bridge.Session(launched=True)
    with pytest.raises(bridge.BridgeError, match=r"Renoise: could not load kick\.wav"):
        bridge.run_job(cfg, runner, home, "x", {}, timeout_s=1, clock=failing.clock(), session=ours)
    assert [call for call, _ in runner.calls] == [("pgrep", "-x", "Renoise")]  # already running
    with pytest.raises(bridge.BridgeError, match="didn't finish within 1 s"):
        bridge.run_job(
            cfg, runner, home, "y", {}, timeout_s=1, clock=Tool(None).clock(), session=ours
        )


@pytest.mark.req("REQ-REN-03")
def test_run_job_refuses_a_renoise_someone_already_has_open(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    bridge.install(cfg, home)
    runner = FakeRunner()  # pgrep finds Renoise
    with pytest.raises(bridge.BridgeError, match="Save your work and quit Renoise"):
        bridge.run_job(cfg, runner, home, "x", {"songs": []}, timeout_s=1)
    assert [call for call, _ in runner.calls] == [("pgrep", "-x", "Renoise")]  # never launched
    assert list(bridge.jobs_dir(cfg).glob("*.job.lua")) == []  # nothing left for it to pick up


@pytest.mark.req("REQ-REN-03")
def test_a_session_reuses_the_renoise_it_started(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    bridge.install(cfg, home)
    launched: list[bool] = []

    def pgrep(_argv: object) -> Result:
        return Result(0 if launched else 1)

    def open_app(_argv: object) -> Result:
        launched.append(True)
        return Result(0)

    runner = FakeRunner(responses={"pgrep": pgrep, "open": open_app})
    session = bridge.Session()
    for name in ("one", "two"):
        tool = Tool({"ok": True, "renoise": "3.5.4", "files": []})
        tool.target = bridge.jobs_dir(cfg) / f"{name}.result.json"
        bridge.run_job(
            cfg, runner, home, name, {}, timeout_s=5, clock=tool.clock(), session=session
        )
    assert session.launched and launched == [True]  # started once, then reused
    # A later command with a new session finds Renoise open and refuses.
    with pytest.raises(bridge.BridgeError, match="already open"):
        bridge.run_job(cfg, runner, home, "three", {}, timeout_s=1, session=bridge.Session())


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


def _setup_tool(tmp_path: Path) -> tuple[config.Config, Path, Path]:
    """mph's config, the installed tool, and a sample to build songs from."""
    cfg = cfg_for(tmp_path)
    installed = bridge.install(cfg, renoise_home(tmp_path, "V3.5.4"))
    (tmp_path / "out").mkdir()
    sample = tmp_path / "kick.wav"
    sample.write_bytes(b"x")
    return cfg, installed, sample


@pytest.mark.lua
@pytest.mark.req("REQ-REN-03")
@pytest.mark.req("REQ-REN-08")
def test_the_tool_runs_a_job_from_the_folder_to_a_result(tmp_path: Path) -> None:
    cfg, installed, sample = _setup_tool(tmp_path)
    result = bridge.submit(cfg, "BM-1", _job(tmp_path, sample))

    seen = lua.run("job", str(installed), "14")

    outcome = json.loads(result.read_text("utf-8"))
    assert outcome["ok"] is True
    assert outcome["renoise"] == "3.5.4 (fake)"
    assert outcome["files"] == [
        str(tmp_path / "out" / "mix.wav"),
        str(tmp_path / "out" / "kick.wav"),
    ]
    assert outcome["kept"] == []
    assert seen["renders"][0]["rate"] == 48000
    assert [r["active"] for r in seen["renders"]] == [["kick"], ["kick"]]
    # Each song is saved after building, and again with nothing muted after rendering.
    # Then the song left open is saved as the scratch song, so nothing is left unsaved.
    out, scratch = tmp_path / "out", str(bridge.scratch_dir(cfg) / "mph-scratch.xrns")
    songs = [str(out / name) for name in ("a.xrns", "a.xrns", "b.xrns", "b.xrns")]
    assert seen["saved"] == [*songs, scratch]
    assert seen["prompts"] == [] and seen["unsaved"] is False and seen["open"] == scratch
    assert sorted(p.name for p in bridge.jobs_dir(cfg).iterdir()) == ["BM-1.result.json"]


@pytest.mark.lua
@pytest.mark.req("REQ-REN-08")
def test_jobs_follow_each_other_without_renoise_asking_to_save(tmp_path: Path) -> None:
    cfg, installed, sample = _setup_tool(tmp_path)
    first = bridge.submit(cfg, "BM-1", _job(tmp_path, sample))
    second = bridge.submit(cfg, "BM-2", _job(tmp_path, sample))

    seen = lua.run("job", str(installed), "30")

    assert json.loads(first.read_text("utf-8"))["ok"] is True
    assert json.loads(second.read_text("utf-8"))["ok"] is True
    assert seen["prompts"] == [] and seen["unsaved"] is False
    scratch = str(bridge.scratch_dir(cfg) / "mph-scratch.xrns")
    # The second job saves the scratch song again before starting its first song.
    assert seen["saved"].count(scratch) == 3
    assert seen["open"] == scratch


@pytest.mark.lua
@pytest.mark.req("REQ-REN-08")
def test_a_job_waits_for_a_song_that_is_still_loading(tmp_path: Path) -> None:
    cfg, installed, sample = _setup_tool(tmp_path)
    result = bridge.submit(cfg, "BM-1", _job(tmp_path, sample))
    seen = lua.run("job", str(installed), "17", "loading=4")
    assert json.loads(result.read_text("utf-8"))["ok"] is True
    assert seen["prompts"] == [] and seen["unsaved"] is False


@pytest.mark.lua
@pytest.mark.req("REQ-REN-08")
def test_a_render_song_opened_by_hand_is_kept_before_a_job(tmp_path: Path) -> None:
    cfg, installed, sample = _setup_tool(tmp_path)
    song = cfg.data_dir / "renders" / "BM-1" / "abc" / "BM-1.xrns"
    song.parent.mkdir(parents=True)
    song.write_text("as measured", "utf-8")
    result = bridge.submit(cfg, "BM-2", _job(tmp_path, sample))

    seen = lua.run("job", str(installed), "14", f"open={song}", "changed")

    outcome = json.loads(result.read_text("utf-8"))
    copy = bridge.scratch_dir(cfg) / "19700101-003320 BM-1.xrns"  # the fake clock's tick 2
    assert outcome["ok"] is True
    assert outcome["kept"] == [{"from": str(song), "to": str(copy)}]
    assert seen["saved"][0] == str(copy) and copy.is_file()
    assert song.read_text("utf-8") == "as measured"  # the render's own song is untouched
    assert seen["prompts"] == [] and seen["unsaved"] is False


@pytest.mark.lua
@pytest.mark.req("REQ-REN-08")
def test_a_song_that_is_not_mphs_is_never_saved_by_the_tool(tmp_path: Path) -> None:
    cfg, installed, sample = _setup_tool(tmp_path)
    mine = tmp_path / "dj" / "groovy.xrns"
    mine.parent.mkdir()
    mine.write_text("Dean's", "utf-8")
    changed = bridge.submit(cfg, "BM-1", _job(tmp_path, sample))
    seen = lua.run("job", str(installed), "4", f"open={mine}", "changed")
    assert "didn't start a new song" in json.loads(changed.read_text("utf-8"))["error"]
    assert seen["prompts"] == [str(mine)]  # Renoise asks Dean, as it always has
    assert seen["saved"] == []
    assert mine.read_text("utf-8") == "Dean's"

    shutil.rmtree(bridge.jobs_dir(cfg))
    unchanged = bridge.submit(cfg, "BM-1", _job(tmp_path, sample))
    seen = lua.run("job", str(installed), "14", f"open={mine}")
    assert json.loads(unchanged.read_text("utf-8"))["ok"] is True
    assert str(mine) not in seen["saved"]  # closed without a save, and without a question
    assert seen["prompts"] == []


@pytest.mark.lua
@pytest.mark.req("REQ-REN-08")
def test_the_tool_reports_failures_in_the_result(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_home(tmp_path, "V3.5.4")
    installed = bridge.install(cfg, home)
    (tmp_path / "out").mkdir()
    missing = bridge.submit(cfg, "missing", _job(tmp_path, tmp_path / "nowhere.wav"))
    seen = lua.run("job", str(installed), "6")
    assert "could not load" in json.loads(missing.read_text("utf-8"))["error"]
    # The half-built song is saved as the scratch song, in a step of its own.
    assert seen["open"] == str(bridge.scratch_dir(cfg) / "mph-scratch.xrns")
    assert seen["prompts"] == [] and seen["unsaved"] is False

    shutil.rmtree(bridge.jobs_dir(cfg))
    busy = bridge.submit(cfg, "busy", _job(tmp_path, tmp_path / "nowhere.wav"))
    lua.run("job", str(installed), "4", "refuse")
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
