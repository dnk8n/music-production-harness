from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from mph import cli
from mph.proc import Result, SubprocessRunner
from mph.query import answers, commands, mcp
from mph.query.answers import Library
from tests.fakes import FakeRunner
from tests.query.conftest import HAT, KICK, RAIN, TRIMMED, library_for, make_cfg

ON_PATH = {"claude": "/opt/homebrew/bin/claude", "uv": "/opt/homebrew/bin/uv"}


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    return library_for(make_cfg(tmp_path), tmp_path)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "src" / "music-production-harness"
    repo.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'mph'\n")
    return repo


# --- mph catalogue sound -----------------------------------------------------------------


def lines_of(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


@pytest.mark.req("REQ-CAT-09")
def test_sound_lists_every_label_with_its_origin_and_confidence(
    full: Library, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.cmd_sound(full, KICK[:8]) == 0
    lines = lines_of(capsys)
    assert lines[:2] == [KICK, "  source, wav, 44100 Hz, 24-bit, 2 ch, 0.5 s"]
    assert "  dj: Pack/Kicks/Kick 909.wav (commercial-pack)" in lines
    labels = [line.split() for line in lines[lines.index("labels:") + 1 :][:3]]
    assert labels == [
        ["form", "oneshot", "analysis", "0.9"],
        ["pack", "Pack", "folder", "0.5"],
        ["role", "kick", "filename", "0.6"],
    ]
    assert "analysis core v2, 2026-11-02T10:00Z: duration_s 0.5, form oneshot, lufs -9.5" in lines
    assert any(line.startswith("on disk: ") and line.endswith("Kick 909.wav") for line in lines)
    assert lines[-2:] == [
        "  BM-DRUM-02 renoise (kick), 2026-11-09T09:00:00Z",
        "  BM-DRUM-02 renoise (kick), 2026-11-08T09:00:00Z",
    ]


@pytest.mark.req("REQ-CAT-09")
def test_sound_shows_downloads_lineage_and_failed_analysis(
    full: Library, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.cmd_sound(full, RAIN) == 0
    rain = lines_of(capsys)
    assert (
        "download: freesound 123, 'Rain on a tin roof' by someone, cc0, "
        "https://freesound.org/s/123/" in rain
    )
    assert "  role      fx  human     1" in rain
    assert commands.cmd_sound(full, TRIMMED) == 0
    trimmed = lines_of(capsys)
    assert "labels: none" in trimmed
    assert any(line.startswith("made from: ") and "trim" in line for line in trimmed)
    assert trimmed[-4:-1] == ["stashes: keepers", "on disk: no", "used in 0 renders"]
    assert "not analysed yet" in trimmed[-1]
    assert commands.cmd_sound(full, HAT) == 0
    assert "analysis core v2, 2026-11-02T10:00Z: failed: could not decode" in lines_of(capsys)


@pytest.mark.req("REQ-CAT-09")
def test_an_unreadable_sound_says_why() -> None:
    answer: dict[str, Any] = {
        "hash": "ab" * 32,
        "origin": "source",
        "container": None,
        "sample_rate": None,
        "bit_depth": None,
        "channels": None,
        "duration_s": None,
        "probe_error": "moov atom not found",
        "files": [],
        "tags": [],
        "analysis": [],
        "download": None,
        "made_from": None,
        "stashes": [],
        "on_disk": {"path": None, "present": False},
        "used_in_renders": {"count": 0, "newest": []},
    }
    assert commands.render_sound(answer).splitlines()[1:3] == [
        "  source, ?",
        "  unreadable: moov atom not found",
    ]


@pytest.mark.req("REQ-CAT-09")
def test_sound_errors_name_the_cli_search(
    full: Library, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.cmd_sound(full, "ffffff") == 1
    err = capsys.readouterr().err
    assert "matches no sounds" in err and "`mph catalogue search`" in err
    fresh = library_for(make_cfg(tmp_path / "fresh"), tmp_path)
    assert commands.cmd_sound(fresh, KICK) == 1
    assert "no catalogue yet" in capsys.readouterr().err


@pytest.mark.req("REQ-CAT-09")
@pytest.mark.req("REQ-QRY-03")
def test_mph_catalogue_sound_json_is_the_tools_answer(
    full: Library, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = full.cfg.data_dir.parent / "mph.toml"
    config_file.write_text(f"[paths]\ndata_dir = '{full.cfg.data_dir}'\n")
    monkeypatch.setenv("MPH_CONFIG", str(config_file))
    assert cli.main(["catalogue", "sound", KICK[:6], "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["hash"] == KICK
    assert printed["tags"] == answers.sound(full, KICK)["tags"]
    assert cli.main(["catalogue", "sound", "Pack/Kicks/Kick 909.wav"]) == 0
    assert capsys.readouterr().out.startswith(KICK)


# --- mph mcp call ----------------------------------------------------------------------


@pytest.mark.req("REQ-QRY-08")
def test_call_prints_what_claude_sees(full: Library, capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.cmd_call(full, "sound", json.dumps({"ref": KICK[:8]})) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["hash"] == KICK
    assert printed["used_in_renders"]["count"] == 2


@pytest.mark.req("REQ-QRY-08")
def test_call_with_no_arguments(lib: Library, capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.cmd_call(lib, "benchmarks", "{}") == 0
    assert "BM-DRUM-02" in capsys.readouterr().out


@pytest.mark.req("REQ-QRY-08")
@pytest.mark.parametrize(
    ("tool", "raw", "says"),
    [
        ("labels", "{}", "no catalogue yet: run `mph catalogue scan` first"),
        ("labels", '{"limit": 0}', "bad arguments: limit must be at least 1"),
    ],
)
def test_call_tool_errors_exit_1(
    lib: Library, capsys: pytest.CaptureFixture[str], tool: str, raw: str, says: str
) -> None:
    assert commands.cmd_call(lib, tool, raw) == 1
    out, err = capsys.readouterr()
    assert (out, err.strip()) == ("", says)


@pytest.mark.req("REQ-QRY-08")
@pytest.mark.parametrize("raw", ["{roles: kick}", '["kick"]', "3"])
def test_call_needs_a_json_object(
    lib: Library, capsys: pytest.CaptureFixture[str], raw: str
) -> None:
    assert commands.cmd_call(lib, "labels", raw) == 2
    assert "arguments must be a JSON object" in capsys.readouterr().err


@pytest.mark.req("REQ-QRY-08")
def test_mph_mcp_call_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = tmp_path / "mph.toml"
    config_file.write_text(f"[paths]\ndata_dir = '{tmp_path / 'data'}'\n")
    monkeypatch.setenv("MPH_CONFIG", str(config_file))
    assert cli.main(["mcp", "call", "overview"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["config_files"] == [str(config_file)]
    assert cli.main(["mcp", "call", "sound", '{"ref": "abcdef"}']) == 1
    assert "no catalogue yet" in capsys.readouterr().err


@pytest.mark.req("REQ-CLI-01")
def test_mcp_commands_are_listed() -> None:
    parser = cli.build_parser()
    assert "    mcp" in parser.format_help()
    for action in ("serve", "install", "call"):
        assert parser.parse_args(["mcp", action, *(["overview"] if action == "call" else [])])


# --- mph mcp install -------------------------------------------------------------------


@pytest.mark.req("REQ-QRY-07")
def test_install_registers_the_server_for_every_session(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner(on_path=dict(ON_PATH))
    assert commands.cmd_install(runner, checkout=checkout) == 0
    serve = ["/opt/homebrew/bin/uv", "--directory", str(checkout), "run", "--quiet", "mph"]
    claude = "/opt/homebrew/bin/claude"
    assert [argv for argv, _ in runner.calls] == [
        (claude, "mcp", "remove", "--scope", "user", "mph"),
        (claude, "mcp", "add", "--scope", "user", "mph", "--", *serve, "mcp", "serve"),
    ]
    out = capsys.readouterr().out
    assert "claude mcp list" in out
    assert "claude mcp remove --scope user mph" in out
    assert f"--directory {checkout} run --quiet mph mcp serve" in out


@pytest.mark.req("REQ-QRY-07")
def test_install_names_the_config_file(checkout: Path, tmp_path: Path) -> None:
    runner = FakeRunner(on_path=dict(ON_PATH))
    assert commands.cmd_install(runner, config_file=Path("mph.toml"), checkout=checkout) == 0
    added = runner.calls[-1][0]
    assert added[-4:] == ("--config", str(Path("mph.toml").resolve()), "mcp", "serve")


@pytest.mark.req("REQ-QRY-07")
def test_the_config_file_to_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = tmp_path / "chosen.toml"
    assert commands.pinned_config(chosen) == chosen
    monkeypatch.setenv("MPH_CONFIG", str(chosen))
    assert commands.pinned_config(None) is None  # not there: nothing to name
    chosen.write_text("")
    assert commands.pinned_config(None) == chosen
    monkeypatch.delenv("MPH_CONFIG")
    assert commands.pinned_config(None) is None  # the default needs no naming


@pytest.mark.req("REQ-QRY-07")
@pytest.mark.parametrize(
    ("on_path", "says"),
    [
        ({"uv": ON_PATH["uv"]}, "Claude Code isn't installed here"),
        ({"claude": ON_PATH["claude"]}, "uv isn't on PATH"),
    ],
)
def test_install_needs_claude_and_uv(
    checkout: Path, capsys: pytest.CaptureFixture[str], on_path: dict[str, str], says: str
) -> None:
    runner = FakeRunner(on_path=on_path)
    assert commands.cmd_install(runner, checkout=checkout) == 1
    assert says in capsys.readouterr().err
    assert runner.calls == []


@pytest.mark.req("REQ-QRY-07")
def test_install_needs_a_checkout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(on_path=dict(ON_PATH))
    assert commands.cmd_install(runner, checkout=tmp_path) == 1
    assert "isn't an mph checkout" in capsys.readouterr().err
    assert runner.calls == []


@pytest.mark.req("REQ-QRY-07")
def test_install_says_why_claude_refused(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def claude(argv: Any) -> Result:
        if argv[2] == "remove":
            return Result(1, stderr="No MCP server found with name: mph")  # first install
        return Result(1, stderr="Invalid scope")

    runner = FakeRunner(on_path=dict(ON_PATH), responses={"claude": claude})
    assert commands.cmd_install(runner, checkout=checkout) == 1
    assert "`claude mcp add` failed: Invalid scope" in capsys.readouterr().err


@pytest.mark.req("REQ-QRY-07")
def test_the_checkout_is_this_repo() -> None:
    assert (commands.CHECKOUT / "pyproject.toml").is_file()
    assert (commands.CHECKOUT / "spec" / "components" / "query-channel.md").is_file()


# --- mph mcp serve ---------------------------------------------------------------------


class Pipe(io.BytesIO):
    def __init__(self, data: bytes = b"", *, tty: bool = False) -> None:
        super().__init__(data)
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


INITIALIZE = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
OVERVIEW = b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"overview"}}\n'


@pytest.mark.req("REQ-QRY-01")
def test_serve_writes_only_protocol_to_stdout(
    lib: Library, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def noisy_overview(library: Library) -> dict[str, Any]:
        print("a stray print from deep inside")
        return {"ok": True}

    monkeypatch.setattr(answers, "overview", noisy_overview)
    writer = Pipe()
    assert commands.cmd_serve(lib, Pipe(INITIALIZE + OVERVIEW), writer) == 0
    out = writer.getvalue().splitlines()
    assert [json.loads(line)["id"] for line in out] == [1, 2]
    assert json.loads(out[1])["result"]["content"][0]["text"] == '{"ok":true}'
    captured = capsys.readouterr()
    assert "a stray print from deep inside" in captured.err
    assert captured.out == ""


@pytest.mark.req("REQ-QRY-01")
def test_serve_in_a_terminal_says_what_it_is(
    lib: Library, capsys: pytest.CaptureFixture[str]
) -> None:
    assert commands.cmd_serve(lib, Pipe(tty=True), Pipe()) == 0
    assert "uv run mph mcp install" in capsys.readouterr().err


class Gone(Pipe):
    def write(self, data: Any) -> int:
        raise BrokenPipeError


class Interrupted(Pipe):
    def readline(self, size: int | None = -1) -> bytes:
        raise KeyboardInterrupt


@pytest.mark.req("REQ-QRY-01")
def test_serve_ends_quietly_when_the_client_goes(lib: Library) -> None:
    assert commands.cmd_serve(lib, Pipe(INITIALIZE), Gone()) == 0
    assert commands.cmd_serve(lib, Interrupted(), Pipe()) == 0


@pytest.mark.req("REQ-QRY-01")
def test_the_real_server_over_pipes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The command Claude Code runs, as its own process, in both protocol eras."""
    config_file = tmp_path / "mph.toml"
    config_file.write_text(f"[paths]\ndata_dir = '{tmp_path / 'data'}'\n")
    monkeypatch.setenv("MPH_CONFIG", str(config_file))
    meta = {"_meta": {mcp.VERSION_KEY: "2026-07-28"}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": meta},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "benchmarks"}},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    done = SubprocessRunner(timeout_s=60).run(
        [sys.executable, "-m", "mph.cli", "mcp", "serve"], stdin=stdin
    )
    assert done.code == 0, done.stderr
    answers_by_id = {a["id"]: a["result"] for a in map(json.loads, done.stdout.splitlines())}
    assert sorted(answers_by_id) == [1, 2, 3, 4]
    assert answers_by_id[1]["supportedVersions"][0] == "2026-07-28"
    assert answers_by_id[2]["protocolVersion"] == "2025-11-25"
    assert len(answers_by_id[3]["tools"]) == 11
    assert answers_by_id[4]["isError"] is False
    assert not (tmp_path / "data").exists()


@pytest.mark.req("REQ-QRY-07")
def test_dispatch(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "cmd_serve", lambda library: 7)
    assert commands.dispatch(argparse.Namespace(action="serve"), lib, FakeRunner()) == 7
    install = argparse.Namespace(action="install", config=None)
    assert commands.dispatch(install, lib, FakeRunner()) == 1  # no claude on this PATH
