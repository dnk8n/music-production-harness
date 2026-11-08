from __future__ import annotations

import json
from pathlib import Path

import pytest

from mph import cli, components, config
from mph.components import Context
from mph.platform_info import Host
from mph.proc import Result
from tests.conftest import make_bundle
from tests.fakes import FakeRunner, FakeStore, ScriptedPrompter


@pytest.mark.req("REQ-CLI-01")
@pytest.mark.parametrize("command", ["status", "doctor", "install", "secrets", "spec"])
def test_commands_exist(command: str) -> None:
    assert f"    {command}" in cli.build_parser().format_help()


@pytest.mark.req("REQ-CLI-02")
def test_status_fits_one_screen(capsys: pytest.CaptureFixture[str], mac_host: Host) -> None:
    code = cli.cmd_status(config.load(), Context(FakeRunner(), mac_host), FakeStore())
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) <= 15
    assert any("48000 Hz, 24-bit AIFF" in line for line in lines)
    assert code == 1  # nothing installed in the fake host


@pytest.mark.req("REQ-CLI-03")
def test_bad_config_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "c.toml"
    bad.write_text("[audio]\ndelivery_format = 'mp3'\n")
    assert cli.main(["--config", str(bad), "status"]) == 2
    assert "delivery_format" in capsys.readouterr().err


@pytest.mark.req("REQ-CLI-03")
def test_main_runs_doctor_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--stage", "ai", "--json"])
    assert code in (0, 1)
    assert isinstance(json.loads(capsys.readouterr().out), list)


@pytest.mark.req("REQ-DOC-05")
def test_doctor_json(capsys: pytest.CaptureFixture[str], mac_host: Host) -> None:
    cli.cmd_doctor(config.load(), Context(FakeRunner(), mac_host), FakeStore(), [], as_json=True)
    findings = json.loads(capsys.readouterr().out)
    assert {"check", "status", "detail", "fix"} <= findings[0].keys()


def test_doctor_text_shows_fixes(capsys: pytest.CaptureFixture[str], mac_host: Host) -> None:
    cli.cmd_doctor(config.load(), Context(FakeRunner(), mac_host), FakeStore(), [], as_json=False)
    out = capsys.readouterr().out
    assert "✗ component:renoise" in out
    assert "fix: mph install renoise" in out


@pytest.mark.req("REQ-DOC-06")
def test_install_is_dry_run_by_default(capsys: pytest.CaptureFixture[str], mac_host: Host) -> None:
    runner = FakeRunner()
    assert cli.cmd_install(components.by_id("sox"), Context(runner, mac_host), run=False) == 0
    assert runner.calls == []
    assert "Dry run" in capsys.readouterr().out


@pytest.mark.req("REQ-DOC-06")
def test_install_yes_runs_only_automatable_steps(
    capsys: pytest.CaptureFixture[str], mac_host: Host
) -> None:
    runner = FakeRunner()
    cli.cmd_install(components.by_id("tidal"), Context(runner, mac_host), run=True)
    ran = [argv[0] for argv, _ in runner.calls]
    assert ran == ["brew", "ghcup", "ghcup", "cabal"]
    assert "manual step" in capsys.readouterr().out


def test_install_stops_on_failure(mac_host: Host) -> None:
    runner = FakeRunner(responses={"brew": Result(1)})
    assert cli.cmd_install(components.by_id("ollama"), Context(runner, mac_host), run=True) == 1
    assert len(runner.calls) == 1


def test_install_skips_when_present(capsys: pytest.CaptureFixture[str], mac_host: Host) -> None:
    make_bundle(mac_host.roots[0], "/Applications/Renoise.app", "3.5.4")
    cli.cmd_install(components.by_id("renoise"), Context(FakeRunner(), mac_host), run=False)
    assert "already installed (3.5.4)" in capsys.readouterr().out


@pytest.mark.req("REQ-SEC-05")
def test_secrets_list_set_delete(capsys: pytest.CaptureFixture[str]) -> None:
    store = FakeStore()
    prompter = ScriptedPrompter(["fake-id-0000", "fake-value-0000"])
    assert cli.cmd_secrets(store, "set", "freesound", prompter) == 0
    assert cli.cmd_secrets(store, "list", None) == 0
    out = capsys.readouterr().out
    assert "freesound    set" in out
    assert "gemini       missing" in out
    assert "fake-value-0000" not in out
    del store.values["freesound.client_id"]
    cli.cmd_secrets(store, "list", None)
    assert "freesound    partial" in capsys.readouterr().out
    assert cli.cmd_secrets(store, "delete", "freesound") == 0
    assert cli.cmd_secrets(store, "delete", "freesound") == 1


def test_secrets_need_keychain() -> None:
    assert cli.cmd_secrets(None, "list", None) == 2


def test_secrets_unknown_name() -> None:
    assert cli.cmd_secrets(FakeStore(), "set", "aws") == 2


def test_terminal_prompter_hides_secret_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("getpass.getpass", lambda prompt: "hidden")
    monkeypatch.setattr("builtins.input", lambda prompt: "shown")
    prompter = cli.TerminalPrompter()
    assert prompter.ask("Key", secret=True) == "hidden"
    assert prompter.ask("ID", secret=False) == "shown"


def test_spec_check_on_this_repo() -> None:
    repo = Path(__file__).resolve().parents[1]
    assert cli.main(["spec", "check", "--repo", str(repo)]) == 0
