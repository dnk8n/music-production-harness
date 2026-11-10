from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path

import pytest

from mph.bench import commands, feedback, score
from mph.catalogue import browse, db
from mph.catalogue import commands as catalogue_commands
from mph.proc import Result
from tests.bench.test_render import cfg_for, run_cli
from tests.bench.test_score import spec_sections
from tests.fakes import FakeRunner

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
LOCAL = {"Host": f"127.0.0.1:{PORT}"}
POST = {**LOCAL, "Origin": ORIGIN, "Content-Type": "application/json"}
HASH = "0123456789abcdef"


def fake_render(
    data: Path,
    benchmark_id: str = "BM-DRUM-02",
    *,
    variant: str = "renoise",
    render_hash: str = HASH,
    at: str = "2026-11-08T21:00:00Z",
) -> Path:
    folder = data / "renders" / benchmark_id / render_hash
    (folder / "variants").mkdir(parents=True)
    (folder / "stems").mkdir()
    (folder / "stems" / "kick.wav").write_bytes(b"RIFF....stem")
    (folder / f"{benchmark_id}.aiff").write_bytes(b"FORM....aiff")
    (folder / "variants" / "x-low.wav").write_bytes(b"RIFF....wav")
    (folder / "secret.txt").write_text("not listed")
    report = {
        "id": benchmark_id,
        "score_hash": render_hash,
        "rendered_at": at,
        "passed": True,
        "files": {"delivery": f"{benchmark_id}.aiff", "stems": ["stems/kick.wav"]},
        "variants": [{"name": "low", "file": "variants/x-low.wav"}],
    }
    if variant:  # "" writes a render from before variants existed
        report["variant"] = variant
    (folder / "report.json").write_text(json.dumps(report))
    return folder


@pytest.fixture
def app(tmp_path: Path) -> browse.App:
    data = tmp_path / "data"
    fake_render(data)
    runner = FakeRunner(responses={"ffmpeg": Result(0, data=b"RIFF....decoded")})
    return browse.App(data / db.FILENAME, data, runner, PORT)


def post(app: browse.App, payload: object, headers: dict[str, str] = POST) -> browse.Response:
    return app.handle_post("/api/verdict", headers, json.dumps(payload).encode())


@pytest.mark.req("REQ-FBK-01")
def test_listing_shows_every_benchmark_with_its_latest_render(app: browse.App) -> None:
    listed = json.loads(app.handle("/api/bench", LOCAL).body)
    assert sorted(b["id"] for b in listed) == score.available()
    shuffle = next(b for b in listed if b["id"] == "BM-DRUM-02")
    takes = {t["name"]: t for t in shuffle["takes"]}
    assert list(takes) == ["renoise", "supercollider", "synth-kit"]  # all equal, no main
    assert takes["renoise"]["report"]["score_hash"] == HASH
    assert takes["supercollider"]["report"] is None
    assert shuffle["hear"].startswith("the same four-on-the-floor")
    detroit = next(b for b in listed if b["id"] == "BM-DRUM-01")
    assert all(t["report"] is None for t in detroit["takes"])


@pytest.mark.req("REQ-BEN-11")
@pytest.mark.req("REQ-FBK-01")
def test_benchmarks_are_listed_by_project(app: browse.App) -> None:
    listed = json.loads(app.handle("/api/bench", LOCAL).body)
    projects = [b["project"] for b in listed]
    assert projects == sorted(projects)  # grouped, projects by name
    assert {"Bass", "Drum patterns"} <= set(projects)
    page = (Path(browse.__file__).parent / "browse.html").read_text("utf-8")
    assert "mph.collapsed-projects" in page  # remembers which were folded away


@pytest.mark.req("REQ-FBK-01")
@pytest.mark.req("REQ-FBK-05")
def test_only_listed_render_files_are_served(app: browse.App) -> None:
    aiff = app.handle(f"/bench/BM-DRUM-02/{HASH}/BM-DRUM-02.aiff", LOCAL)
    assert aiff.status == HTTPStatus.OK and aiff.body == b"RIFF....decoded"
    variant = app.handle(
        f"/bench/BM-DRUM-02/{HASH}/variants/x-low.wav", {**LOCAL, "Range": "bytes=0-3"}
    )
    assert variant.status == HTTPStatus.PARTIAL_CONTENT and variant.body == b"RIFF"
    stem = app.handle(f"/bench/BM-DRUM-02/{HASH}/stems/kick.wav", LOCAL)
    assert stem.status == HTTPStatus.OK and stem.body == b"RIFF....stem"
    for path in (
        f"/bench/BM-DRUM-02/{HASH}/secret.txt",
        f"/bench/BM-DRUM-02/{HASH}/report.json",
        f"/bench/BM-DRUM-02/{HASH}/../../../catalogue.db",
        "/bench/BM-NOPE/0123456789abcdef/x.aiff",
        "/bench/BM-DRUM-02/nothex/BM-DRUM-02.aiff",
        f"/bench/BM-DRUM-01/{HASH}/BM-DRUM-01.aiff",
        "/bench/BM-DRUM-02",
    ):
        assert app.handle(path, LOCAL).status == HTTPStatus.NOT_FOUND, path


def test_undecodable_render_is_explained(tmp_path: Path) -> None:
    data = tmp_path / "data"
    fake_render(data)
    app = browse.App(data / db.FILENAME, data, FakeRunner(responses={"ffmpeg": Result(1)}), PORT)
    response = app.handle(f"/bench/BM-DRUM-02/{HASH}/BM-DRUM-02.aiff", LOCAL)
    assert response.status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.req("REQ-FBK-02")
def test_verdicts_are_stored_against_the_render(app: browse.App, tmp_path: Path) -> None:
    saved = post(
        app,
        {
            "id": "BM-DRUM-02",
            "score_hash": HASH,
            "verdict": "close",
            "notes": " 0:42 hats too loud ",
        },
    )  # fmt: skip (scope "take" is the default)
    assert saved.status == HTTPStatus.OK
    assert json.loads(saved.body)["notes"] == "0:42 hats too loud"
    listed = json.loads(app.handle("/api/bench", LOCAL).body)
    shuffle = next(b for b in listed if b["id"] == "BM-DRUM-02")
    assert shuffle["verdicts"][0]["verdict"] == "close"
    assert shuffle["verdicts"][0]["score_hash"] == HASH
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    assert conn.execute("SELECT count(*) FROM verdict").fetchone()[0] == 1
    conn.close()


@pytest.mark.req("REQ-FBK-02")
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"id": "BM-DRUM-02", "score_hash": HASH, "verdict": "great"}, "one of pass"),
        ({"id": "BM-DRUM-02", "score_hash": "ffffffffffffffff", "verdict": "pass"}, "no such"),
        ({"id": "BM-DRUM-02", "score_hash": HASH, "verdict": "pass", "notes": "x" * 5000},
         "limited"),
        ({"id": "BM-DRUM-02", "scope": "some", "verdict": "pass"}, "scope must be"),
        ({"id": "BM-NOPE", "scope": "all", "verdict": "pass"}, "no benchmark"),
        ({"id": "BM-DRUM-01", "scope": "all", "verdict": "pass"}, "no take has been rendered"),
    ],
)  # fmt: skip
def test_bad_verdicts_are_refused(app: browse.App, payload: dict[str, str], message: str) -> None:
    response = post(app, payload)
    assert response.status == HTTPStatus.BAD_REQUEST and message in response.body.decode()


@pytest.mark.req("REQ-FBK-05")
def test_only_this_page_can_post(app: browse.App) -> None:
    ok = {"id": "BM-DRUM-02", "score_hash": HASH, "verdict": "pass"}
    assert post(app, ok, {**POST, "Host": "evil.example"}).status == HTTPStatus.FORBIDDEN
    assert post(app, ok, {**POST, "Origin": "https://evil.example"}).status == HTTPStatus.FORBIDDEN
    plain = {**POST, "Content-Type": "text/plain"}
    assert post(app, ok, plain).status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert app.handle_post("/api/other", POST, b"{}").status == HTTPStatus.NOT_FOUND
    assert app.handle_post("/api/verdict", POST, b"{not json").status == HTTPStatus.BAD_REQUEST
    assert app.handle_post("/api/verdict", POST, b"[1]").status == HTTPStatus.BAD_REQUEST


def test_listing_without_a_catalogue_and_with_broken_reports(tmp_path: Path) -> None:
    folder = fake_render(tmp_path)
    (folder.parent / "broken").mkdir()
    (folder.parent / "broken" / "report.json").write_text("{nope")
    assert feedback.reports(tmp_path / "renders", "BM-DRUM-02")[0]["score_hash"] == HASH
    assert feedback.verdicts(None, "BM-DRUM-02") == []
    (folder / "report.json").write_text("{nope")
    assert feedback.playable(tmp_path / "renders", "BM-DRUM-02", HASH, "BM-DRUM-02.aiff") is None


@pytest.mark.req("REQ-FBK-01")
def test_bench_serve_opens_the_benchmarks_tab(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    class Server:
        def __enter__(self) -> Server:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    original = catalogue_commands.cmd_browse

    def browse_with_fake_server(*args: object, **kwargs: object) -> int:
        return original(*args, serve=lambda app, port: Server(), **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(catalogue_commands, "cmd_browse", browse_with_fake_server)
    fake = FakeRunner(on_path={"open": "/usr/bin/open"})
    assert run_cli(tmp_path, "serve", fake=fake) == 0
    assert "listening page at http://127.0.0.1:8765/#bench" in capsys.readouterr().out
    opened = [call[0][1] for call in fake.calls]
    assert opened == ["http://127.0.0.1:8765/#bench"]
    assert commands.latest_reports(cfg_for(tmp_path), "BM-DRUM-01") == []


@pytest.mark.req("REQ-BEN-02")
@pytest.mark.parametrize("benchmark_id", score.available())
def test_the_page_shows_the_spec_description(benchmark_id: str) -> None:
    section = " ".join(spec_sections()[benchmark_id].split())
    assert f"Hear: {score.load(benchmark_id).hear}" in section


@pytest.mark.req("REQ-REN-07")
def test_open_in_renoise_opens_only_the_song_a_report_lists(tmp_path: Path) -> None:
    data = tmp_path / "data"
    folder = fake_render(data)
    runner = FakeRunner()
    app = browse.App(data / db.FILENAME, data, runner, PORT)

    def open_project(payload: object, headers: dict[str, str] = POST) -> browse.Response:
        return app.handle_post("/api/open-project", headers, json.dumps(payload).encode())

    ask = {"id": "BM-DRUM-02", "score_hash": HASH}
    assert open_project(ask).status == HTTPStatus.NOT_FOUND  # this render has no song
    report = json.loads((folder / "report.json").read_text())
    report["files"]["project"] = "BM-DRUM-02.xrns"
    (folder / "report.json").write_text(json.dumps(report))
    assert open_project(ask).status == HTTPStatus.NOT_FOUND  # listed, but not on disk
    (folder / "BM-DRUM-02.xrns").write_bytes(b"xrns")
    assert (
        open_project(ask, {**POST, "Origin": "https://evil.example"}).status == HTTPStatus.FORBIDDEN
    )
    assert runner.calls == []
    opened = open_project(ask)
    assert opened.status == HTTPStatus.OK
    assert json.loads(opened.body) == {"opened": "BM-DRUM-02.xrns"}
    assert runner.calls == [
        (("open", "-a", "/Applications/Renoise.app", str(folder / "BM-DRUM-02.xrns")), False)
    ]
    assert (
        open_project({"id": "BM-DRUM-02", "score_hash": "../../etc"}).status == HTTPStatus.NOT_FOUND
    )
    report["files"]["project"] = "secret.txt"
    (folder / "report.json").write_text(json.dumps(report))
    assert open_project(ask).status == HTTPStatus.NOT_FOUND  # only a Renoise song
    report["files"]["project"] = "BM-DRUM-02.xrns"
    (folder / "report.json").write_text(json.dumps(report))
    failing = browse.App(
        data / db.FILENAME, data, FakeRunner(responses={"open": Result(1, stderr="no app")}), PORT
    )
    refused = failing.handle_post("/api/open-project", POST, json.dumps(ask).encode())
    assert refused.status == HTTPStatus.BAD_GATEWAY and b"no app" in refused.body


@pytest.mark.req("REQ-FBK-01")
def test_the_page_matches_loudness_and_starts_takes_from_the_top() -> None:
    # The player logic runs in the browser; this pins the switches and their defaults.
    page = (Path(browse.__file__).parent / "browse.html").read_text("utf-8")
    assert "{ match: true, keep: false }" in page  # loudness matched, playhead not kept
    assert "Match loudness" in page and "Keep the playhead when switching takes" in page
    assert "10 ** ((quietest - lufs) / 20)" in page  # turned down to the quietest take
    assert "Math.min(1," in page  # never louder than the file


def takes_of(app: browse.App, benchmark_id: str = "BM-DRUM-02") -> dict[str, dict[str, object]]:
    listed = json.loads(app.handle("/api/bench", LOCAL).body)
    found = next(b for b in listed if b["id"] == benchmark_id)
    return {t["name"]: t for t in found["takes"]}


def said(take: dict[str, object]) -> str | None:
    verdict = take["verdict"]
    return None if verdict is None else str(verdict["verdict"])  # type: ignore[index]


@pytest.mark.req("REQ-FBK-02")
def test_a_verdict_on_the_whole_benchmark_covers_every_take_but_one_judged_alone(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    fake_render(data)  # renoise
    fake_render(data, variant="supercollider", render_hash="1" * 16)
    fake_render(data, variant="synth-kit", render_hash="2" * 16)
    app = browse.App(data / db.FILENAME, data, FakeRunner(), PORT)
    alone = {"id": "BM-DRUM-02", "scope": "take", "score_hash": "2" * 16, "verdict": "fail"}
    assert post(app, alone).status == HTTPStatus.OK
    whole = post(app, {"id": "BM-DRUM-02", "scope": "all", "verdict": "pass", "notes": "lovely"})
    assert whole.status == HTTPStatus.OK
    assert sorted(json.loads(whole.body)["score_hashes"]) == [HASH, "1" * 16, "2" * 16]
    takes = takes_of(app)
    assert said(takes["renoise"]) == "pass" and said(takes["supercollider"]) == "pass"
    assert said(takes["synth-kit"]) == "fail"  # judged on its own before: that verdict stands
    assert post(app, {**alone, "score_hash": HASH, "verdict": "close"}).status == HTTPStatus.OK
    assert said(takes_of(app)["renoise"]) == "close"  # and after, too
    fake_render(data, variant="supercollider", render_hash="3" * 16, at="2026-11-09T09:00:00Z")
    assert takes_of(app)["supercollider"]["verdict"] is None  # a new render is heard afresh


@pytest.mark.req("REQ-FBK-02")
def test_a_verdict_on_an_old_main_render_counts_for_the_takes_rendered_by_then(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    fake_render(data, variant="", render_hash="9" * 16, at="2026-11-08T20:00:00Z")
    fake_render(data)  # renoise, rendered 21:00
    app = browse.App(data / db.FILENAME, data, FakeRunner(), PORT)
    main = {"id": "BM-DRUM-02", "score_hash": "9" * 16, "verdict": "pass"}
    assert post(app, main).status == HTTPStatus.OK  # given now, after both renders
    assert said(takes_of(app)["renoise"]) == "pass"
    assert "main" not in takes_of(app)  # an old main render isn't a take
    listed = json.loads(app.handle("/api/bench", LOCAL).body)
    shuffle = next(b for b in listed if b["id"] == "BM-DRUM-02")
    assert shuffle["verdicts"][0]["legacy"] is True
    fake_render(data, variant="supercollider", render_hash="1" * 16, at="2099-01-01T00:00:00Z")
    assert takes_of(app)["supercollider"]["verdict"] is None  # rendered after the verdict
