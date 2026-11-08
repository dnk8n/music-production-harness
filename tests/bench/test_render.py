from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph import cli, config
from mph.bench import audio, commands, kit, osc, render, sc, score
from mph.catalogue import db
from mph.proc import Result
from tests.bench.fake_sc import fake_sclang, fake_scsynth
from tests.fakes import FakeRunner

TOOLS = {"scsynth": "/usr/bin/scsynth", "sclang": "/usr/bin/sclang"}


def runner(**responses: object) -> FakeRunner:
    return FakeRunner(
        on_path=dict(TOOLS),
        responses={"scsynth": fake_scsynth, "sclang": fake_sclang, **responses},  # type: ignore[dict-item]
    )


def cfg_for(tmp_path: Path, rate: int = 48000) -> config.Config:
    raw = config.deep_merge(
        config._defaults(),
        {"paths": {"data_dir": str(tmp_path / "data")}, "audio": {"production_sample_rate": rate}},
    )
    return config.parse(raw)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def add_sample(
    conn: sqlite3.Connection,
    folder: Path,
    name: str,
    tags: list[tuple[str, str]],
    *,
    form: str = "oneshot",
    seconds: float = 0.3,
    container: str = "wav",
    licence: str = "commercial-pack",
) -> str:
    rate = 44100
    t = np.arange(round(seconds * rate)) / rate
    wave = np.sin(2 * np.pi * 400 * t) * np.exp(-((t * 8) % 1) * 20)
    folder.mkdir(parents=True, exist_ok=True)
    if container == "wav":
        soundfile.write(str(folder / name), wave, rate, subtype="PCM_16")
    else:
        (folder / name).write_bytes(b"not something scsynth can read")
    digest = f"{abs(hash(name)):064x}"[:64]
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO source VALUES ('lib', 'local', ?, ?, NULL)",
            (str(folder), licence),
        )
        conn.execute(
            "INSERT INTO asset (hash, bytes, container, sample_rate, channels, duration_s, "
            "origin) VALUES (?, 1, ?, ?, 1, ?, 'source')",
            (digest, container, rate, seconds),
        )
        conn.execute("INSERT INTO source_file VALUES ('lib', ?, 1, 1, ?, 1)", (name, digest))
        for tag, value in tags:
            conn.execute("INSERT INTO tag VALUES (?, ?, ?, 'filename', 0.6)", (digest, tag, value))
        conn.execute(
            "INSERT INTO analysis VALUES (?, 'core', 2, 't', ?, NULL)",
            (digest, json.dumps({"form": form})),
        )
    return digest


# --- kit ------------------------------------------------------------------------------


@pytest.mark.req("REQ-BEN-05")
def test_kit_prefers_the_catalogue_and_falls_back_to_synths(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    lib = tmp_path / "lib"
    add_sample(conn, lib, "Kick 909.wav", [("role", "kick")])
    add_sample(conn, lib, "Kick 808.m4a", [("role", "kick")], container="m4a")
    add_sample(conn, lib, "Clap.wav", [("role", "clap")])
    choices = kit.choose(score.load("BM-DRUM-01"), conn, tmp_path / "data")
    by_voice = {c.voice: c for c in choices}
    assert by_voice["kick"].label == "Kick 909.wav"  # the m4a can't be read by scsynth
    assert by_voice["kick"].synthdef == "mph_play1"
    assert by_voice["kick"].reason == "catalogue: best kick oneshot matching 909"
    assert by_voice["clap"].reason == "catalogue: best clap oneshot"  # nothing says 909
    assert by_voice["chh"].synthdef == "mph_hat"
    assert by_voice["chh"].reason == "synth fallback: no hat in the catalogue"
    assert kit.licence_marker(choices) == "licence:private"
    plain = kit.choose(score.load("BM-DRUM-01"), None, tmp_path)
    assert all(not c.is_sample for c in plain) and kit.licence_marker(plain) == "licence:clear"
    assert kit.choose(score.load("BM-DRUM-04"), conn, tmp_path)[0].reason.startswith("the score")


def test_kit_errors(tmp_path: Path) -> None:
    with pytest.raises(kit.KitError, match="needs a break loop"):
        kit.choose(score.load("BM-DRUM-09"), None, tmp_path)
    odd = score.load_text(
        'id = "BM-X"\nbpm = 120\nbars = 1\n[voices.zz]\ngrid = "x . . . . . . . . . . . . . . ."'
    )
    with pytest.raises(kit.KitError, match="no synth voice"):
        kit.choose(odd, None, tmp_path)


# --- SuperCollider seam ---------------------------------------------------------------


def test_tools_are_found_or_reported_missing() -> None:
    assert sc.find_tools(runner()) == sc.Tools(TOOLS["scsynth"], TOOLS["sclang"])
    assert sc.find_tools(FakeRunner()) is None


def test_synthdefs_compile_once(tmp_path: Path) -> None:
    fake = runner()
    tools = sc.find_tools(fake)
    assert tools
    folder = sc.compile_synthdefs(fake, tools, tmp_path)
    assert sc.compile_synthdefs(fake, tools, tmp_path) == folder
    assert [call[0][0] for call in fake.calls] == [TOOLS["sclang"]]
    assert (folder / "synthdefs.scd").read_text() == sc.synthdef_source()
    broken = runner(sclang=Result(1, stderr="ERROR: syntax"))
    with pytest.raises(sc.RenderError, match="syntax"):
        sc.compile_synthdefs(broken, tools, tmp_path / "other")


def test_score_bundles_schedule_hits_chokes_and_slices(tmp_path: Path) -> None:
    loaded = score.load("BM-DRUM-02")
    choices = kit.choose(loaded, None, tmp_path)
    plan = sc.Plan(loaded, choices, 48000, tmp_path / "defs")
    bundles = sc.build(plan, score.events(loaded))
    assert bundles[0].messages[0] == osc.Message("/d_loadDir", (str(tmp_path / "defs"),))
    chokes = [m for b in bundles for m in b.messages if m.address == "/n_set"]
    assert chokes and all(m.args[1:] == ("gate", 0) for m in chokes)
    assert bundles[-1].time_s == pytest.approx(loaded.length_s, abs=1e-4)
    times = [b.time_s * 48000 for b in bundles]
    assert all(abs(t - round(t)) < 1e-6 for t in times)  # every hit on a whole sample


def test_render_failures_are_explained(tmp_path: Path) -> None:
    loaded = score.load("BM-DRUM-01")
    plan = sc.Plan(loaded, kit.choose(loaded, None, tmp_path), 48000, tmp_path)
    tools = sc.Tools("scsynth", "sclang")
    failing = runner(scsynth=Result(1, stderr="exception in real time"))
    with pytest.raises(sc.RenderError, match="exception in real time"):
        sc.render(failing, tools, plan, score.events(loaded), tmp_path / "out.wav")
    assert sc.version(failing, tools) == "unknown"
    assert sc.version(runner(), tools) == "scsynth 3.13.0 (fake)"


# --- whole renders ----------------------------------------------------------------------


@pytest.mark.req("REQ-BEN-03")
@pytest.mark.req("REQ-BEN-06")
@pytest.mark.req("REQ-AUD-05")
def test_render_writes_stems_master_delivery_and_report(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    outcome = render.run("BM-DRUM-02", cfg, runner(), None)
    report = outcome.report
    assert outcome.folder.parent == cfg.data_dir / "renders" / "BM-DRUM-02"
    assert outcome.folder.name == report["score_hash"]
    failures = [m for m in report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []
    names = {m["name"] for m in report["measurements"]}  # type: ignore[attr-defined]
    assert {"swing: chh", "choke: chh cuts ohh (dB left)"} <= names
    assert report["licence_marker"] == "licence:clear"
    assert report["tools"] == {"mph": report["tools"]["mph"], "scsynth": "scsynth 3.13.0 (fake)"}  # type: ignore[index]
    for stem in report["files"]["stems"]:  # type: ignore[index]
        info = soundfile.info(str(outcome.folder / stem))
        assert (info.samplerate, info.subtype) == (48000, "FLOAT")
    delivered = soundfile.info(str(outcome.folder / "BM-DRUM-02.aiff"))
    assert (delivered.samplerate, delivered.subtype) == (48000, "PCM_24")
    saved = json.loads((outcome.folder / "report.json").read_text())
    assert saved["score_hash"] == report["score_hash"] and outcome.passed


def test_render_measures_humanising_and_break_slices(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    cfg = cfg_for(tmp_path)
    shaker = render.run("BM-DRUM-08", cfg, runner(), None)
    assert shaker.passed
    add_sample(
        conn,
        tmp_path / "lib",
        "Photek Break 120bpm.wav",
        [("role", "break"), ("bpm", "120")],
        form="loop",
        seconds=2.0,
    )
    chopped = render.run("BM-DRUM-09", cfg, runner(), conn)
    names = [m["name"] for m in chopped.report["measurements"]]  # type: ignore[attr-defined]
    assert any(n.startswith("clicks: break") for n in names)
    assert chopped.report["kit"][0]["sound"] == "Photek Break 120bpm.wav"  # type: ignore[index]


def test_render_flags_a_wrong_render(tmp_path: Path) -> None:
    def late_scsynth(argv: list[str]) -> Result:
        result = fake_scsynth(argv)
        if "render.wav" in " ".join(argv):  # shift the real render, not the templates
            out = Path(argv[argv.index("-N") + 3])
            data, rate = soundfile.read(str(out), dtype="float32", always_2d=True)
            shifted = np.vstack([np.zeros((rate // 500, data.shape[1]), np.float32), data])
            audio.write_float(out, shifted, rate)
        return result

    outcome = render.run("BM-DRUM-01", cfg_for(tmp_path), runner(scsynth=late_scsynth), None)
    failed = [m["name"] for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert "timing: worst hit error (ms)" in failed
    assert "determinism: samples differing in a second render" in failed
    assert not outcome.passed


def test_render_needs_supercollider(tmp_path: Path) -> None:
    with pytest.raises(sc.RenderError, match="mph install supercollider"):
        render.run("BM-DRUM-01", cfg_for(tmp_path), FakeRunner(), None)


def test_templates_cover_every_distinct_sound() -> None:
    loaded = score.load("BM-DRUM-09")
    templates = render.template_events(loaded, score.events(loaded))
    assert sorted(e.slice for e in templates) == [1, 2, 3, 4, 5, 7, 8]
    ref = render.template_score(loaded, templates)
    assert ref.bars == 0 and ref.length_s == pytest.approx(7 * render.TEMPLATE_GAP_S)
    assert all(v.chokes == () for v in replace(score.load("BM-DRUM-06")).voices[:2])


# --- commands -------------------------------------------------------------------------


def run_cli(tmp_path: Path, *argv: str, fake: FakeRunner | None = None) -> int:
    args = cli.build_parser().parse_args(["bench", *argv])
    return commands.dispatch(args, cfg_for(tmp_path), fake or runner())


def test_cli_render_list_and_report(
    tmp_path: Path, conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(tmp_path, "report", "BM-DRUM-01") == 1
    assert "hasn't been rendered" in capsys.readouterr().out
    assert run_cli(tmp_path, "render", "BM-DRUM-01") == 0
    out = capsys.readouterr().out
    assert "BM-DRUM-01 Detroit four-to-the-floor: pass" in out
    assert "✓ timing" in out and "files" in out
    assert run_cli(tmp_path, "list") == 0
    listing = capsys.readouterr().out
    assert "BM-DRUM-01" in listing and "pass (" in listing and "not rendered" in listing
    assert run_cli(tmp_path, "report", "BM-DRUM-01") == 0
    assert "sound  kick" in capsys.readouterr().out


def test_cli_render_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli(tmp_path, "render") == 2
    assert run_cli(tmp_path, "render", "BM-NOPE") == 1
    assert "no score for BM-NOPE" in capsys.readouterr().err
    (tmp_path / "data" / "renders" / "BM-DRUM-01" / "x").mkdir(parents=True)
    (tmp_path / "data" / "renders" / "BM-DRUM-01" / "x" / "report.json").write_text("{bad")
    assert commands.latest_report(cfg_for(tmp_path), "BM-DRUM-01") is None


def test_cli_renders_against_the_catalogue_when_there_is_one(
    tmp_path: Path, conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    add_sample(conn, tmp_path / "lib", "Kick.wav", [("role", "kick")])
    assert run_cli(tmp_path, "render", "BM-DRUM-01") in (0, 1)
    assert "Kick.wav" in capsys.readouterr().out


def test_main_wires_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPH_CONFIG", str(tmp_path / "none.toml"))
    assert cli.main(["bench", "list"]) == 0
