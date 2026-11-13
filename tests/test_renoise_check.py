"""scripts/renoise_check.py, the real-Renoise check of the bridge tool (REQ-REN-08)."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from mph import config
from mph.proc import Result
from mph.renoise import bridge
from tests.fakes import FakeRunner

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "renoise_check.py"


@pytest.fixture(scope="module")
def renoise_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("renoise_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Renoise:
    """Renoise and the bridge tool as the check sees them: a process that
    starts and quits, and jobs answered as soon as mph waits for them."""

    def __init__(self, cfg: config.Config, *, keeps: bool = True, quits: bool = True) -> None:
        self.cfg, self.keeps, self.quits = cfg, keeps, quits
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
            job.unlink()
            result: dict[str, Any] = {"ok": True, "files": [], "kept": []}
            if job.name.startswith("renoise-check-2") and self.keeps:
                opened = next(bridge.scratch_dir(self.cfg).glob("renoise-check-*/A.xrns"))
                copy = bridge.scratch_dir(self.cfg) / "20261113-081500 A.xrns"
                copy.write_text("A, as it was", "utf-8")
                result["kept"] = [{"from": str(opened), "to": str(copy)}]
            job.with_name(job.name.replace(".job.lua", ".result.json")).write_text(
                json.dumps(result), "utf-8"
            )


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
