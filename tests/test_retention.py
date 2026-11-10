"""Keeping fresh renders, pruning the rest (spec/components/retention.md)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path

import pytest

from mph import cli, config, retention
from mph.bench import jit, render
from mph.catalogue import browse, db
from mph.components import Context
from mph.platform_info import Host
from tests.bench.test_feedback import LOCAL, PORT, POST
from tests.bench.test_render import cfg_for, runner
from tests.fakes import FakeRunner, FakeStore

LONG_AGO = 1_000_000_000.0  # 2001: well past the settle time
POLICY = config.Retention(keep_renders=1, keep_work=False, prune_after_render=True)


def age(path: Path, when: float = LONG_AGO) -> None:
    """Make a file or folder (and all inside it) look untouched since ``when``."""
    for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.utime(p, (when, when), follow_symlinks=False)


def make_render(
    data: Path,
    digest: str,
    *,
    benchmark: str = "BM-DRUM-01",
    variant: str = "renoise",
    at: str = "2026-11-08T10:00:00Z",
    kit: tuple[str, ...] = (),
    work: bool = True,
) -> Path:
    folder = data / "renders" / benchmark / digest
    (folder / "stems").mkdir(parents=True)
    (folder / "stems" / "kick.wav").write_bytes(b"s" * 1000)
    (folder / "master.wav").write_bytes(b"m" * 2000)
    (folder / f"{benchmark}.aiff").write_bytes(b"a" * 500)
    if work:
        (folder / "work").mkdir()
        (folder / "work" / "again.wav").write_bytes(b"w" * 4000)
    report = {
        "id": benchmark,
        "variant": variant,
        "score_hash": digest,
        "rendered_at": at,
        "kit": [{"voice": "kick", "hash": h} for h in kit],
        "files": {"delivery": f"{benchmark}.aiff", "stems": ["stems/kick.wav"]},
    }
    (folder / "report.json").write_text(json.dumps(report))
    age(folder)
    return folder


def kinds(plan: retention.Plan) -> dict[Path, str]:
    return {i.path: i.kind for i in plan.items}


@pytest.mark.req("REQ-RET-01")
@pytest.mark.req("REQ-RET-03")
def test_only_the_newest_render_of_each_variant_is_fresh(tmp_path: Path) -> None:
    old = make_render(tmp_path, "a" * 16, at="2026-11-01T00:00:00Z")
    new = make_render(tmp_path, "b" * 16, at="2026-11-08T00:00:00Z")
    variant = make_render(tmp_path, "c" * 16, variant="synth-kit", at="2026-11-02T00:00:00Z")
    found = retention.plan(tmp_path, POLICY)
    assert kinds(found) == {
        old: retention.SUPERSEDED,
        new / "work": retention.WORK_FILES,
        variant / "work": retention.WORK_FILES,
    }
    assert found.kept_renders == 2
    assert found.size == 4000 + 1000 + 2000 + 500 + 4000 + 4000
    two = retention.plan(tmp_path, POLICY, keep=2)
    assert retention.SUPERSEDED not in kinds(two).values()
    keeping = retention.plan(tmp_path, replace(POLICY, keep_work=True))
    assert kinds(keeping) == {old: retention.SUPERSEDED}


@pytest.mark.req("REQ-RET-02")
def test_a_pruned_render_keeps_only_its_report(tmp_path: Path) -> None:
    from mph.bench import feedback  # noqa: PLC0415

    old = make_render(tmp_path, "a" * 16, at="2026-11-01T00:00:00Z")
    make_render(tmp_path, "b" * 16, at="2026-11-08T00:00:00Z")
    freed = retention.apply(tmp_path, retention.plan(tmp_path, POLICY))
    assert freed == 4000 + 1000 + 2000 + 500 + 4000
    assert [p.name for p in old.iterdir()] == ["report.json"]
    reports = {r["score_hash"]: r for r in feedback.reports(tmp_path / "renders", "BM-DRUM-01")}
    assert reports["a" * 16]["on_disk"] is False
    assert reports["b" * 16]["on_disk"] is True
    assert retention.plan(tmp_path, POLICY).items == []  # nothing more to do


@pytest.mark.req("REQ-RET-04")
def test_cache_entries_a_fresh_render_uses_are_kept(tmp_path: Path) -> None:
    used, unused, stale_only = "1" * 64, "2" * 64, "3" * 64
    make_render(tmp_path, "a" * 16, at="2026-11-01T00:00:00Z", kit=(stale_only,), work=False)
    make_render(tmp_path, "b" * 16, at="2026-11-08T00:00:00Z", kit=(used,), work=False)
    for digest in (used, unused, stale_only):
        path = tmp_path / "cache" / digest[:2] / f"{digest}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"c" * 10)
        age(path)
    stash = tmp_path / "stash" / "keep" / f"{unused}.wav"
    stash.parent.mkdir(parents=True)
    stash.write_bytes(b"k")
    age(stash)
    cached = {
        i.path.stem
        for i in retention.plan(tmp_path, POLICY).items
        if i.kind == "unused cached derivative"
    }
    assert cached == {unused, stale_only}
    retention.apply(tmp_path, retention.plan(tmp_path, POLICY))
    assert (tmp_path / "cache" / used[:2] / f"{used}.wav").is_file()
    assert stash.is_file()


@pytest.mark.req("REQ-RET-05")
def test_recent_unfinished_and_foreign_things_are_safe(tmp_path: Path) -> None:
    data = tmp_path / "data"
    stopped = data / "renders" / "BM-DRUM-01" / "staging"
    stopped.mkdir(parents=True)
    (stopped / "sounds.wav").write_bytes(b"x" * 10)
    age(stopped)
    running = data / "renders" / "BM-DRUM-02" / ("d" * 16)
    running.mkdir(parents=True)
    (running / "master.wav").write_bytes(b"x")  # no report yet, written just now
    jobs = data / "renoise" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "old.job.lua").write_text("return {}")
    age(jobs / "old.job.lua")
    for keep in ("catalogue.sqlite", "downloads/freesound/1/a.wav", "synthdefs/x/y.scsyndef"):
        (data / keep).parent.mkdir(parents=True, exist_ok=True)
        (data / keep).write_bytes(b"keep")
        age(data / keep)
    outside = tmp_path / "samples"
    outside.mkdir()
    (outside / "kick.wav").write_bytes(b"source")
    link = data / "renders" / "BM-DRUM-03"
    link.symlink_to(outside, target_is_directory=True)

    found = retention.plan(data, POLICY)
    assert kinds(found) == {
        stopped: retention.UNFINISHED,
        jobs / "old.job.lua": retention.JOB_FILES,
    }
    retention.apply(data, found)
    assert not stopped.exists() and running.is_dir()
    assert all(
        (data / keep).is_file() for keep in ("catalogue.sqlite", "downloads/freesound/1/a.wav")
    )
    assert (outside / "kick.wav").is_file()

    sneaky = retention.Plan([retention.Item(outside / "kick.wav", retention.CACHED, 6, "")])
    with pytest.raises(retention.PruneError, match="isn't mph's"):
        retention.apply(data, sneaky)
    assert (outside / "kick.wav").is_file()
    root = retention.Plan([retention.Item(data / "renders", retention.UNFINISHED, 0, "")])
    with pytest.raises(retention.PruneError):
        retention.apply(data, root)


@pytest.mark.req("REQ-RET-05")
def test_prune_shows_the_list_and_deletes_only_with_yes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_for(tmp_path)
    old = make_render(cfg.data_dir, "a" * 16, at="2026-11-01T00:00:00Z")
    new = make_render(cfg.data_dir, "b" * 16, at="2026-11-08T00:00:00Z")
    assert cli.cmd_prune(cfg, keep=None, delete=False, show_all=True) == 0
    out = capsys.readouterr().out
    assert "1 x superseded render" in out and "1 x render work files" in out
    assert "Nothing deleted" in out and str(old) in out
    assert (old / "master.wav").is_file()
    assert cli.cmd_prune(cfg, keep=None, delete=True, show_all=False) == 0
    assert "freed 11.5 KB" in capsys.readouterr().out
    assert not (old / "master.wav").exists()
    age(new)  # deleting its work files just touched it
    assert cli.cmd_prune(cfg, keep=0, delete=False, show_all=False) == 0
    assert "1 x superseded render" in capsys.readouterr().out  # --keep 0: even the newest
    assert cli.cmd_prune(cfg, keep=-1, delete=False, show_all=False) == 2
    assert cli.cmd_prune(cfg_for(tmp_path / "empty"), keep=None, delete=False, show_all=False) == 0
    assert "nothing to prune" in capsys.readouterr().out
    assert cli.main(["prune"]) == 0  # wired into the parser


@pytest.mark.req("REQ-RET-01")
@pytest.mark.req("REQ-RET-03")
def test_a_render_tidies_up_after_itself(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    old = make_render(cfg.data_dir, "a" * 16, at="2000-01-01T00:00:00Z")
    other = make_render(cfg.data_dir, "c" * 16, variant="synth-kit", at="2000-01-01T00:00:00Z")
    outcome = render.run("BM-DRUM-01", cfg, runner(), None, home=tmp_path)
    assert not (outcome.folder / "work").exists()
    assert (outcome.folder / "master.wav").is_file()
    assert [p.name for p in old.iterdir()] == ["report.json"]  # superseded at once
    assert (other / "master.wav").is_file()  # another variant: untouched
    assert (other / "work").is_dir()

    kept = replace(cfg, retention=replace(cfg.retention, keep_work=True, prune_after_render=False))
    again = make_render(cfg.data_dir, "e" * 16, at="2000-01-01T00:00:00Z")
    outcome = render.run("BM-DRUM-01", kept, runner(), None, home=tmp_path)
    assert (outcome.folder / "work").is_dir()
    assert (again / "master.wav").is_file()


@pytest.mark.req("REQ-RET-01")
def test_keep_zero_never_prunes_the_render_just_made(tmp_path: Path) -> None:
    folder = make_render(tmp_path, "a" * 16)
    retention.after_render(tmp_path, folder, replace(POLICY, keep_renders=0))
    assert (folder / "master.wav").is_file()
    assert not (folder / "work").exists()


@pytest.mark.req("REQ-RET-06")
def test_renders_on_demand_one_at_a_time() -> None:
    made: list[tuple[str, str]] = []
    queued: list[object] = []
    renderer = jit.Renderer(lambda b, v: made.append((b, v)), spawn=queued.append)
    assert renderer.status().state == jit.IDLE
    assert renderer.start("BM-DRUM-01", "synth-kit").state == jit.RENDERING
    with pytest.raises(jit.JitError, match="already rendering BM-DRUM-01 synth-kit"):
        renderer.start("BM-DRUM-02", "renoise")
    queued.pop()()  # type: ignore[operator]
    assert made == [("BM-DRUM-01", "synth-kit")]
    assert renderer.status().state == jit.DONE
    with pytest.raises(jit.JitError, match="no benchmark"):
        renderer.start("no-such-benchmark", "renoise")
    with pytest.raises(jit.JitError, match="no variant"):
        renderer.start("BM-DRUM-01", "nope")

    def broken(benchmark: str, variant: str) -> None:
        raise RuntimeError("Renoise is already open")

    failing = jit.Renderer(broken, spawn=lambda work: work())
    failing.start("BM-DRUM-01", "renoise")
    assert failing.status().to_dict() == {
        "state": "failed",
        "id": "BM-DRUM-01",
        "variant": "renoise",
        "message": "Renoise is already open",
    }


@pytest.mark.req("REQ-RET-06")
def test_the_page_starts_a_render_and_reports_progress(tmp_path: Path) -> None:
    made: list[tuple[str, str]] = []
    renderer = jit.Renderer(lambda b, v: made.append((b, v)), spawn=lambda work: work())
    app = browse.App(tmp_path / db.FILENAME, tmp_path, FakeRunner(), PORT, renderer)
    body = json.dumps({"id": "BM-DRUM-01", "variant": "renoise"}).encode()
    started = app.handle_post("/api/render", POST, body)
    assert started.status == HTTPStatus.OK
    assert made == [("BM-DRUM-01", "renoise")]
    status = json.loads(app.handle("/api/render-status", LOCAL).body)
    assert status["state"] == "done"
    refused = app.handle_post("/api/render", POST, json.dumps({"id": "nope"}).encode())
    assert refused.status == HTTPStatus.CONFLICT
    foreign = app.handle_post("/api/render", {**POST, "Origin": "http://evil.example"}, body)
    assert foreign.status == HTTPStatus.FORBIDDEN

    plain = browse.App(tmp_path / db.FILENAME, tmp_path, FakeRunner(), PORT)
    assert plain.handle_post("/api/render", POST, body).status == HTTPStatus.NOT_IMPLEMENTED
    assert json.loads(plain.handle("/api/render-status", LOCAL).body)["state"] == "idle"

    page = (Path(browse.__file__).parent / "browse.html").read_text("utf-8")
    assert '"/api/render"' in page and "audio pruned" in page and "Render again" in page
    assert "on_disk === false" in page  # playing a pruned take renders it first


@pytest.mark.req("REQ-RET-06")
def test_bench_serve_can_render_on_demand(tmp_path: Path) -> None:
    from mph.bench import commands  # noqa: PLC0415

    cfg = cfg_for(tmp_path)
    render_one = commands.render_on_demand(cfg, runner(), tmp_path)
    render_one("BM-DRUM-01", "synth-kit")
    assert list((cfg.data_dir / "renders" / "BM-DRUM-01").glob("*/report.json"))


@pytest.mark.req("REQ-RET-07")
def test_status_shows_disk_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mac_host: Host
) -> None:
    cfg = cfg_for(tmp_path)
    make_render(cfg.data_dir, "a" * 16, at="2026-11-01T00:00:00Z")
    make_render(cfg.data_dir, "b" * 16, at="2026-11-08T00:00:00Z")
    cli.cmd_status(cfg, Context(FakeRunner(), mac_host), FakeStore())
    out = capsys.readouterr().out
    assert "disk         renders use 15." in out and "; 11.5 KB can be freed" in out


@pytest.mark.req("REQ-CFG-07")
@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("keep_renders = -1", "keep_renders must be a whole number"),
        ("keep_renders = true", "keep_renders must be a whole number"),
        ('keep_work = "no"', "keep_work must be true or false"),
        ("prune_after_render = 1", "prune_after_render must be true or false"),
        ("keep_forever = true", "keep_forever"),
    ],
)
def test_retention_settings_are_checked(tmp_path: Path, table: str, message: str) -> None:
    user = tmp_path / "c.toml"
    user.write_text(f"[retention]\n{table}\n")
    with pytest.raises(config.ConfigError, match=message):
        config.load(user)


def test_sizes_read_like_finder() -> None:
    assert [retention.human(n) for n in (0, 999, 1500, 2_500_000, 21_400_000_000)] == [
        "0 B",
        "999 B",
        "1.5 KB",
        "2.5 MB",
        "21.4 GB",
    ]


@pytest.mark.req("REQ-RET-01")
@pytest.mark.parametrize("old_style", ["missing", "null"])
def test_renders_from_before_takes_were_equal_are_superseded(
    tmp_path: Path, old_style: str
) -> None:
    old = make_render(tmp_path, "a" * 16, at="2026-11-08T21:39:00Z", work=False)
    report = json.loads((old / "report.json").read_text())
    if old_style == "missing":
        del report["variant"]
    else:
        report["variant"] = None
    (old / "report.json").write_text(json.dumps(report))
    age(old)
    assert kinds(retention.plan(tmp_path, POLICY)) == {
        old: retention.SUPERSEDED
    }  # no take plays it
    main = make_render(tmp_path, "b" * 16, variant="main", at="2026-11-09T06:41:00Z", work=False)
    take = make_render(tmp_path, "c" * 16, at="2026-11-09T06:42:00Z", work=False)
    planned = retention.plan(tmp_path, POLICY)
    assert kinds(planned) == {old: retention.SUPERSEDED, main: retention.SUPERSEDED}
    assert take not in kinds(planned)
    assert any("from before takes were equal" in i.why for i in planned.items)
