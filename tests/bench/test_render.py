from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph import cli, config
from mph.bench import audio, commands, kit, levels, osc, render, sc, score
from mph.catalogue import db
from mph.proc import Result
from mph.renoise import bridge
from tests.bench.fake_renoise import FakeRenoise
from tests.bench.fake_sc import fake_sclang, fake_scsynth
from tests.fakes import FakeRunner

TOOLS = {"scsynth": "/usr/bin/scsynth", "sclang": "/usr/bin/sclang"}


def runner(**responses: object) -> FakeRunner:
    return FakeRunner(
        on_path=dict(TOOLS),
        responses={
            "scsynth": fake_scsynth,
            "sclang": fake_sclang,
            "pgrep": Result(1),  # Renoise isn't open until mph starts it
            **responses,  # type: ignore[dict-item]
        },
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
    noise: bool = False,
    freq: float = 400.0,
) -> str:
    rate = 44100
    t = np.arange(round(seconds * rate)) / rate
    tone = (
        np.random.default_rng(len(name)).uniform(-1, 1, len(t))
        if noise
        else np.sin(2 * np.pi * freq * t)
    )
    wave = tone * np.exp(-((t * 8) % 1) * 20)
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
    outcome = render.run("BM-DRUM-02", cfg, runner(), None, home=tmp_path)
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
    shaker = render.run("BM-DRUM-08", cfg, runner(), None, home=tmp_path)
    assert shaker.passed
    add_sample(
        conn,
        tmp_path / "lib",
        "Photek Break 120bpm.wav",
        [("role", "break"), ("bpm", "120")],
        form="loop",
        seconds=2.0,
    )
    chopped = render.run("BM-DRUM-09", cfg, runner(), conn, home=tmp_path)
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

    outcome = render.run(
        "BM-DRUM-01", cfg_for(tmp_path), runner(scsynth=late_scsynth), None, home=tmp_path
    )
    failed = [m["name"] for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert "timing: worst hit error (ms)" in failed
    assert "determinism: samples differing in a second render" in failed
    assert not outcome.passed


def test_render_needs_supercollider(tmp_path: Path) -> None:
    with pytest.raises(sc.RenderError, match="mph install supercollider"):
        render.run("BM-DRUM-01", cfg_for(tmp_path), FakeRunner(), None, home=tmp_path)


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
    return commands.dispatch(args, cfg_for(tmp_path), fake or runner(), tmp_path)


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
    assert commands.latest_reports(cfg_for(tmp_path), "BM-DRUM-01") == []


def test_cli_renders_against_the_catalogue_when_there_is_one(
    tmp_path: Path, conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    add_sample(conn, tmp_path / "lib", "Kick.wav", [("role", "kick")])
    assert run_cli(tmp_path, "render", "BM-DRUM-01") in (0, 1)
    assert "Kick.wav" in capsys.readouterr().out


def test_main_wires_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPH_CONFIG", str(tmp_path / "none.toml"))
    assert cli.main(["bench", "list"]) == 0


# --- Renoise engine -------------------------------------------------------------------


def renoise_ready(tmp_path: Path, cfg: config.Config) -> Path:
    home = tmp_path / "home"
    (home / "Library/Preferences/Renoise/V3.5.4").mkdir(parents=True)
    bridge.install(cfg, home)
    return home


def test_engine_choice(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    assert render.pick_engine("auto", tmp_path) == "supercollider"
    assert render.pick_engine("supercollider", tmp_path) == "supercollider"
    with pytest.raises(sc.RenderError, match="mph renoise install"):
        render.pick_engine("renoise", tmp_path)
    with pytest.raises(sc.RenderError, match="engine must be one of"):
        render.pick_engine("tidal", tmp_path)
    home = renoise_ready(tmp_path, cfg)
    assert render.pick_engine("auto", home) == "renoise"
    assert render.pick_engine("supercollider", home) == "supercollider"


@pytest.mark.lua
@pytest.mark.req("REQ-BEN-10")
@pytest.mark.req("REQ-REN-02")
def test_renoise_render_builds_a_song_and_measures_it(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    outcome = render.run("BM-DRUM-02", cfg, runner(), None, home=home, clock=fake.clock())
    report = outcome.report
    failures = [m for m in report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []
    assert report["engine"] == "renoise"
    assert report["tools"]["renoise"] == "3.5.4 (fake)"  # type: ignore[index]
    assert report["files"]["project"] == "BM-DRUM-02.xrns"  # type: ignore[index]
    assert (outcome.folder / "BM-DRUM-02.xrns").read_bytes() == b"fake xrns"
    main, templates = fake.performed[0]["songs"]
    assert [t["name"] for t in main["tracks"]] == ["kick", "clap", "chh", "ohh"]
    assert [r.get("solo") for r in main["renders"]] == [None, None, None, 1, 2, 3, 4]
    assert main["renders"][0]["file"].endswith("work/warm-up.wav")  # never measured
    assert [r["file"].rsplit("/", 1)[1] for r in templates["renders"]] == [
        "warm-up.wav",
        "templates.wav",
    ]
    # Synth voices were rendered once by SuperCollider and load as samples.
    assert main["tracks"][0]["sample"] == str(outcome.folder / "sounds" / "kick.wav")
    assert (outcome.folder / "sounds" / "kick.wav").is_file()
    assert templates["save"].endswith("work/templates.xrns")
    assert not (cfg.data_dir / "renders" / "BM-DRUM-02" / "staging").exists()
    # The same score through SuperCollider is a different render, in its own folder.
    plain = render.run("BM-DRUM-02", cfg, runner(), None, home=home, engine="supercollider")
    assert plain.folder != outcome.folder and plain.report["engine"] == "supercollider"


@pytest.mark.lua
@pytest.mark.req("REQ-BEN-10")
def test_renoise_render_slices_a_break(conn: sqlite3.Connection, tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    add_sample(
        conn,
        tmp_path / "lib",
        "Photek Break 120bpm.wav",
        [("role", "break"), ("bpm", "120")],
        form="loop",
        seconds=2.0,
    )
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    chopped = render.run("BM-DRUM-09", cfg, runner(), conn, home=home, clock=fake.clock())
    track = fake.performed[0]["songs"][0]["tracks"][0]
    assert len(track["slices"]) == 8  # one bar of break, 8 slices a bar
    assert track["transpose"] == 1  # 120 BPM break played at 130 BPM
    timing = next(m for m in chopped.report["measurements"] if m["name"].startswith("timing"))  # type: ignore[attr-defined]
    assert timing["passed"]


@pytest.mark.lua
def test_renoise_problems_are_reported(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    broken = FakeRenoise(bridge.jobs_dir(cfg), broken=True)
    with pytest.raises(bridge.BridgeError, match="could not load the kick"):
        render.run("BM-DRUM-04", cfg, runner(), None, home=home, clock=broken.clock())

    class WrongRate(FakeRenoise):
        def sleep(self, seconds: float) -> None:
            for job_file in self.jobs.glob("*.job.lua"):
                text = job_file.read_text("utf-8").replace("rate = 48000", "rate = 44100")
                job_file.write_text(text, "utf-8")
            super().sleep(seconds)

    wrong = WrongRate(bridge.jobs_dir(cfg))
    with pytest.raises(sc.RenderError, match="at 44100 Hz, not 48000"):
        render.run("BM-DRUM-04", cfg, runner(), None, home=home, clock=wrong.clock())


# --- levels ---------------------------------------------------------------------------


@pytest.mark.req("REQ-BEN-09")
def test_level_targets_match_the_spec_table() -> None:
    spec = (Path(__file__).parents[2] / "spec/components/benchmarks.md").read_text("utf-8")
    section = spec[spec.index("### REQ-BEN-09") : spec.index("### REQ-BEN-10")]
    rows = re.findall(r"^\| (\w+) \| (-?\d+) \|$", section, re.MULTILINE)
    assert {role: float(target) for role, target in rows} == levels.TARGETS_DB
    assert "aim for -18 dB" in section and levels.DEFAULT_TARGET_DB == -18.0


@pytest.mark.req("REQ-BEN-09")
def test_gains_bring_each_sound_to_its_role_target() -> None:
    rate = 48000
    t = np.arange(rate // 4) / rate
    tone = np.sin(2 * np.pi * 1000 * t)[:, None].astype(np.float32)
    loud = levels.hit_loudness(tone, rate)
    quiet = levels.hit_loudness(tone * 0.1, rate)
    assert loud - quiet == pytest.approx(20.0, abs=0.01)
    hat = score.Voice("chh", score.Sound(role="hat"), grid="x " * 16)
    assert levels.gain_db(hat, loud) == pytest.approx(-22.0 - loud, abs=0.01)
    pushed = replace(hat, gain_db=-3.0)
    assert levels.gain_db(pushed, loud) == pytest.approx(levels.gain_db(hat, loud) - 3.0)
    assert levels.gain_db(hat, -200.0) == 12.0  # clamped
    assert levels.gain_db(hat, 40.0) == -30.0
    assert levels.gain_db(hat, float("-inf")) == 0.0
    assert levels.hit_loudness(np.zeros((100, 2), np.float32), rate) == float("-inf")
    unknown = score.Voice("zz", score.Sound(), grid="x " * 16)
    assert levels.target_for(unknown) == -18.0


@pytest.mark.req("REQ-BEN-09")
def test_supercollider_renders_apply_the_gains(conn: sqlite3.Connection, tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    add_sample(conn, tmp_path / "lib", "Loud Hat.wav", [("role", "hat")])
    outcome = render.run("BM-DRUM-01", cfg, runner(), conn, home=tmp_path)
    gains = {k["voice"]: k["gain_db"] for k in outcome.report["kit"]}  # type: ignore[attr-defined]
    sample = audio.read(tmp_path / "lib" / "Loud Hat.wav")
    assert gains["chh"] == pytest.approx(-22.0 - levels.hit_loudness(*sample), abs=0.01)
    # The fake plays each hit at the amp it's given: velocity x the hat's gain.
    peak = float(np.abs(audio.read(outcome.folder / "stems" / "chh.wav")[0]).max())
    assert peak == pytest.approx(0.8 * levels.linear(gains["chh"]), rel=0.05)
    assert set(gains) == {"kick", "clap", "chh"}
    assert outcome.passed


# --- variants -------------------------------------------------------------------------


@pytest.mark.req("REQ-BEN-07")
def test_kit_can_pick_a_lower_match_or_any_synth(conn: sqlite3.Connection, tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    add_sample(conn, lib, "Hat One.wav", [("role", "hat")])
    add_sample(conn, lib, "Hat Two.wav", [("role", "hat")])
    loaded = score.load("BM-DRUM-01")
    best = {c.voice: c for c in kit.choose(loaded, conn, tmp_path / "data")}["chh"]
    second_score = replace(
        loaded,
        voices=tuple(
            replace(v, sound=replace(v.sound, pick=2)) if v.name == "chh" else v
            for v in loaded.voices
        ),
    )
    second = {c.voice: c for c in kit.choose(second_score, conn, tmp_path / "data")}["chh"]
    assert {best.label, second.label} == {"Hat One.wav", "Hat Two.wav"}
    assert second.reason == "catalogue: second hat oneshot"
    synth_kit, _ = score.variant_of(loaded, "synth-kit")
    assert all(not c.is_sample for c in kit.choose(synth_kit, conn, tmp_path / "data"))


@pytest.mark.req("REQ-BEN-07")
def test_variants_render_into_their_own_folders_and_list_together(
    conn: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from mph.bench import feedback  # noqa: PLC0415

    add_sample(conn, tmp_path / "lib", "Hat One.wav", [("role", "hat")])
    assert run_cli(tmp_path, "render", "BM-DRUM-01", "--variant", "synth-kit") == 0
    out = capsys.readouterr().out
    assert "BM-DRUM-01 Detroit four-to-the-floor [synth-kit]: pass" in out
    # Every take by default; Renoise isn't installed here, so that take is skipped, not failed.
    assert run_cli(tmp_path, "render", "BM-DRUM-01") == 0
    out = capsys.readouterr().out
    assert "BM-DRUM-01 [renoise]: skipped, Renoise isn't set up here" in out
    assert "[supercollider]: pass" in out and "[other-hat]: pass" in out
    assert run_cli(tmp_path, "render", "BM-DRUM-01", "--variant", "renoise") == 1  # named: must
    assert "BM-DRUM-01 [renoise]: Renoise can't render yet" in capsys.readouterr().err
    renders = cfg_for(tmp_path).data_dir / "renders"
    listed = next(b for b in feedback.listing(renders, None) if b["id"] == "BM-DRUM-01")
    by_name = {v["name"]: v for v in listed["takes"]}
    assert list(by_name) == ["renoise", "supercollider", "synth-kit", "other-hat"]
    assert by_name["renoise"]["report"] is None
    assert by_name["synth-kit"]["report"]["variant_title"] == "every part a SuperCollider synth"
    hashes = {v["report"]["score_hash"] for v in by_name.values() if v["report"]}
    assert len(hashes) == 3
    assert run_cli(tmp_path, "report", "BM-DRUM-01") == 0
    assert capsys.readouterr().out.count("BM-DRUM-01 Detroit four-to-the-floor [") == 3
    assert feedback.latest(renders, "BM-DRUM-01", "supercollider")["engine"] == "supercollider"  # type: ignore[index]


# --- the acid line (BM-BASS-01) ---------------------------------------------------------


def test_acid_notes_slide_on_the_held_synth(tmp_path: Path) -> None:
    loaded = score.load("BM-BASS-01")
    plan = sc.Plan(loaded, kit.choose(loaded, None, tmp_path), 48000, tmp_path / "defs")
    found = score.events(loaded)
    messages = [(b.time_s, m) for b in sc.build(plan, found) for m in b.messages]
    starts = [m for _, m in messages if m.address == "/s_new"]
    glides = [m for _, m in messages if m.address == "/n_set" and "freq" in m.args]
    slid = [e for e in found if e.step % 16 == 5]  # D#2 slides into C2 on step 5
    assert len(glides) == len(slid) == 8  # once a bar
    assert len(starts) == len(found) - len(glides)  # a slid note doesn't retrigger
    for glide in glides:
        _, name, hz, accent_name, accent = glide.args
        assert (name, accent_name, accent) == ("freq", "acc", 0)
        assert hz == pytest.approx(sc.hz(36))
    first = starts[0].args
    assert (
        first[0] == "mph_acid"
        and dict(zip(first[4::2], first[5::2], strict=True))["cutoff"] == 420.0
    )
    releases = [t for t, m in messages if m.address == "/n_set" and m.args[1:] == ("gate", 0)]
    assert releases[0] == pytest.approx(round(loaded.step_s / 2 * 48000) / 48000)


@pytest.mark.req("REQ-BEN-03")
@pytest.mark.req("REQ-AUD-04")
def test_acid_line_renders_in_supercollider_and_measures_its_notes(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    # Keep work/ (REQ-RET-03 deletes it by default) to check the probe renders were made.
    cfg = replace(cfg, retention=replace(cfg.retention, keep_work=True))
    home = renoise_ready(tmp_path, cfg)  # Renoise is ready, but a melodic part needs SuperCollider
    outcome = render.run("BM-BASS-01", cfg, runner(), None, home=home)
    report = outcome.report
    assert report["engine"] == "supercollider"
    measured = {m["name"]: m for m in report["measurements"]}  # type: ignore[attr-defined]
    for name in (
        "accent: acid louder (dB)",
        "accent: acid brighter (centroid ratio)",
        "slide: acid level change into a slid note (dB)",
        "slide: acid pitch error once a glide settles (cents)",
        "aliasing: acid inharmonic energy (dB)",
    ):
        assert measured[name]["passed"], measured[name]
    assert [m for m in measured.values() if not m["passed"]] == []
    assert (outcome.folder / "work" / "probe-acid.wav").is_file()
    assert (outcome.folder / "work" / "glide-acid.wav").is_file()
    with pytest.raises(sc.RenderError, match="only SuperCollider can play"):
        render.run("BM-BASS-01", cfg, runner(), None, home=home, engine="renoise")
    # A variant isn't held to brighter accents: its controls sit at the ends of their range.
    wide = render.run("BM-BASS-01", cfg, runner(), None, home=home, variant="cutoff-high")
    names = {m["name"] for m in wide.report["measurements"]}  # type: ignore[attr-defined]
    assert "accent: acid brighter (centroid ratio)" not in names
    assert wide.folder != outcome.folder


def test_acid_templates_pair_each_accent_with_a_plain_note() -> None:
    loaded = score.load("BM-BASS-01")
    templates = render.template_events(loaded, score.events(loaded))
    kinds = {(e.pitch, e.accent) for e in templates}
    for pitch, accent in list(kinds):
        if accent:
            assert (pitch, False) in kinds
    assert not any(e.tie for e in templates)


@pytest.mark.req("REQ-BEN-05")
def test_kit_passes_over_hats_that_whistle(conn: sqlite3.Connection, tmp_path: Path) -> None:
    lib, hat = tmp_path / "lib", [("role", "hat")]
    add_sample(conn, lib, "A Analogue Hat 17.wav", hat, seconds=0.05, freq=9000.0)  # whistles
    loaded = score.load("BM-DRUM-01")
    # With nothing else to choose, the tonal hat is still used.
    only = {c.voice: c for c in kit.choose(loaded, conn, tmp_path / "data")}["chh"]
    assert only.label.endswith("A Analogue Hat 17.wav") and "passing over" not in only.reason
    add_sample(conn, lib, "B Closed Hat.wav", hat, seconds=0.05, noise=True)
    chosen = {c.voice: c for c in kit.choose(loaded, conn, tmp_path / "data")}["chh"]
    assert chosen.label.endswith("B Closed Hat.wav")
    assert chosen.reason.endswith("passing over 1 that whistle rather than hiss")
    # Other roles aren't judged this way: a kick is meant to have a pitch.
    add_sample(conn, lib, "A Kick.wav", [("role", "kick")])
    assert {c.voice: c for c in kit.choose(loaded, conn, tmp_path / "data")}["kick"].label.endswith(
        "A Kick.wav"
    )
    assert not kit.tonal(tmp_path / "missing.wav")  # unreadable: left for the render to report
