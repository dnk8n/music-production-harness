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


def fake_render(data: Path, benchmark_id: str = "BM-DRUM-02") -> Path:
    folder = data / "renders" / benchmark_id / HASH
    (folder / "variants").mkdir(parents=True)
    (folder / "stems").mkdir()
    (folder / "stems" / "kick.wav").write_bytes(b"RIFF....stem")
    (folder / f"{benchmark_id}.aiff").write_bytes(b"FORM....aiff")
    (folder / "variants" / "x-low.wav").write_bytes(b"RIFF....wav")
    (folder / "secret.txt").write_text("not listed")
    report = {
        "id": benchmark_id,
        "score_hash": HASH,
        "rendered_at": "2026-11-08T21:00:00Z",
        "passed": True,
        "files": {"delivery": f"{benchmark_id}.aiff", "stems": ["stems/kick.wav"]},
        "variants": [{"name": "low", "file": "variants/x-low.wav"}],
    }
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
    assert [b["id"] for b in listed] == score.available()
    shuffle = next(b for b in listed if b["id"] == "BM-DRUM-02")
    assert shuffle["report"]["score_hash"] == HASH
    assert shuffle["hear"].startswith("the same four-on-the-floor")
    assert next(b for b in listed if b["id"] == "BM-DRUM-01")["report"] is None


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
    saved = post(app, {"id": "BM-DRUM-02", "score_hash": HASH, "verdict": "close",
                       "notes": " 0:42 hats too loud "})  # fmt: skip
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
    assert commands.latest_report(cfg_for(tmp_path), "BM-DRUM-01") is None


@pytest.mark.req("REQ-BEN-02")
@pytest.mark.parametrize("benchmark_id", score.available())
def test_the_page_shows_the_spec_description(benchmark_id: str) -> None:
    section = " ".join(spec_sections()[benchmark_id].split())
    assert f"Hear: {score.load(benchmark_id).hear}" in section
