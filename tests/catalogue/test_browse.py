from __future__ import annotations

import json
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path

import pytest

from mph import config
from mph.catalogue import browse, commands, db
from mph.catalogue.browse import App
from mph.proc import Result
from tests.fakes import FakeRunner

PORT = 8765
LOCAL = {"Host": f"127.0.0.1:{PORT}"}
WAV = "aa" * 32
AIFF = "bb" * 32
MADE = "cc" * 32


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[App, FakeRunner, Path]]:
    root = tmp_path / "samples"
    (root / "Pack").mkdir(parents=True)
    (root / "Pack/Kick 01.wav").write_bytes(b"RIFF....kick-bytes")
    (root / "Pack/Pad.aif").write_bytes(b"FORM....aiff")
    data = tmp_path / "data"
    (data / "cache" / "cc").mkdir(parents=True)
    (data / "cache" / "cc" / f"{MADE}.wav").write_bytes(b"RIFF....made")
    conn = db.connect(data / db.FILENAME)
    with conn:
        conn.execute(
            "INSERT INTO source VALUES ('dj', 'local', ?, 'commercial-pack', NULL)", (str(root),)
        )
        for digest, rel, container in (
            (WAV, "Pack/Kick 01.wav", "wav"),
            (AIFF, "Pack/Pad.aif", "aiff"),
        ):
            conn.execute(
                "INSERT INTO asset (hash, bytes, container, duration_s, origin) "
                "VALUES (?, 1, ?, 0.5, 'source')",
                (digest, container),
            )
            conn.execute("INSERT INTO source_file VALUES ('dj', ?, 1, 1, ?, 1)", (rel, digest))
        conn.execute("INSERT INTO tag VALUES (?, 'role', 'kick', 'filename', 0.6)", (WAV,))
        conn.execute(
            "INSERT INTO asset (hash, bytes, container, duration_s, origin) "
            "VALUES (?, 1, 'wav', 0.2, 'derivative')",
            (MADE,),
        )
        conn.execute("INSERT INTO recipe VALUES ('r1', 'trim', 1, '{\"start\": 0, \"end\": 0.2}')")
        conn.execute("INSERT INTO derivation VALUES (?, 'r1', ?, 't')", (MADE, json.dumps([WAV])))
        conn.execute("INSERT INTO stash VALUES ('try', ?, 't')", (MADE,))
    conn.close()
    runner = FakeRunner(responses={"ffmpeg": Result(0, data=b"RIFF....decoded-aiff")})
    yield App(data / db.FILENAME, data, runner, PORT), runner, root


def get(app: App, target: str, **headers: str) -> browse.Response:
    return app.handle(target, {**LOCAL, **headers})


@pytest.mark.req("REQ-BRW-02")
def test_page_and_search(setup: tuple[App, FakeRunner, Path]) -> None:
    app, _, _ = setup
    home = get(app, "/")
    assert home.status == HTTPStatus.OK
    assert b"Samples and benchmarks" in home.body
    found = json.loads(get(app, "/api/search?role=kick&q=kick").body)
    assert found["matched"] == 1
    assert found["hits"][0]["facts"] == {"role": "kick"}
    assert get(app, "/api/search?bpm=fast").status == HTTPStatus.BAD_REQUEST
    assert get(app, "/api/search?key=purple").status == HTTPStatus.BAD_REQUEST


@pytest.mark.req("REQ-BRW-02")
def test_lineage_and_made_list(setup: tuple[App, FakeRunner, Path]) -> None:
    app, _, _ = setup
    tree = json.loads(get(app, f"/api/lineage/{MADE}").body)
    assert tree["label"] == "trim(start=0, end=0.2)"
    assert tree["inputs"][0] == {
        "hash": WAV,
        "label": "dj:Pack/Kick 01.wav",
        "kind": "source",
        "inputs": [],
    }
    made = json.loads(get(app, "/api/derivatives").body)
    assert made == [
        {
            "hash": MADE,
            "label": "trim(start=0, end=0.2)",
            "created_at": "t",
            "duration_s": 0.2,
            "stashes": "try",
        }
    ]


@pytest.mark.req("REQ-BRW-02")
def test_audio_streams_with_ranges(setup: tuple[App, FakeRunner, Path]) -> None:
    app, _, _ = setup
    whole = get(app, f"/audio/{WAV}")
    assert (whole.status, whole.content_type, whole.body) == (
        HTTPStatus.OK,
        "audio/wav",
        b"RIFF....kick-bytes",
    )
    part = get(app, f"/audio/{WAV}", Range="bytes=8-11")
    assert (part.status, part.body) == (HTTPStatus.PARTIAL_CONTENT, b"kick")
    assert part.headers["Content-Range"] == "bytes 8-11/18"
    assert get(app, f"/audio/{MADE}").body == b"RIFF....made"  # derivative from the cache


@pytest.mark.req("REQ-BRW-03")
def test_aiff_is_decoded_in_memory_once(setup: tuple[App, FakeRunner, Path]) -> None:
    app, runner, root = setup
    first = get(app, f"/audio/{AIFF}")
    again = get(app, f"/audio/{AIFF}", Range="bytes=0-3")
    assert (first.content_type, first.body) == ("audio/wav", b"RIFF....decoded-aiff")
    assert again.body == b"RIFF"
    ffmpeg_calls = [argv for argv, _ in runner.calls if argv[0] == "ffmpeg"]
    assert len(ffmpeg_calls) == 1
    assert ffmpeg_calls[0][-1] == "-"  # to a pipe, never a file
    assert sorted(p.name for p in (root / "Pack").iterdir()) == ["Kick 01.wav", "Pad.aif"]


def test_failed_decode_is_reported(setup: tuple[App, FakeRunner, Path]) -> None:
    app, runner, _ = setup
    runner.responses["ffmpeg"] = Result(1, stderr="bad")
    assert get(app, f"/audio/{AIFF}").status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.req("REQ-BRW-01")
def test_only_known_sounds_and_local_hosts(setup: tuple[App, FakeRunner, Path]) -> None:
    app, _, root = setup
    assert app.handle("/", {"Host": "evil.example:8765"}).status == HTTPStatus.FORBIDDEN
    assert app.handle("/", {}).status == HTTPStatus.FORBIDDEN
    assert app.handle("/", {"Host": f"localhost:{PORT}"}).status == HTTPStatus.OK
    for target in ("/audio/../../etc/passwd", "/audio/" + "dd" * 32, "/api/lineage/xyz", "/nope"):
        assert get(app, target).status == HTTPStatus.NOT_FOUND
    (root / "Pack/Kick 01.wav").unlink()
    assert get(app, f"/audio/{WAV}").status == HTTPStatus.GONE


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 10, None),
        ("bytes=0-", 10, (0, 9)),
        ("bytes=2-4", 10, (2, 4)),
        ("bytes=-3", 10, (7, 9)),
        ("bytes=5-99", 10, (5, 9)),
        ("bytes=9-2", 10, None),
        ("items=0-1", 10, None),
        ("bytes=0-1", 0, None),
    ],
)
def test_byte_range(header: str | None, size: int, expected: tuple[int, int] | None) -> None:
    assert browse.byte_range(header, size) == expected


def cfg_for(tmp_path: Path) -> config.Config:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    return config.parse(raw)


class FakeServer:
    def __init__(self, interrupt: bool) -> None:
        self.interrupt = interrupt
        self.served = False

    def __enter__(self) -> FakeServer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def serve_forever(self) -> None:
        self.served = True
        if self.interrupt:
            raise KeyboardInterrupt


def test_cmd_browse_opens_the_page(
    setup: tuple[App, FakeRunner, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer(interrupt=True)
    runner = FakeRunner(on_path={"open": "/usr/bin/open"})
    code = commands.cmd_browse(
        cfg_for(tmp_path),
        runner,
        PORT,
        open_page=True,
        serve=lambda app, port: server,  # type: ignore[arg-type,return-value]
    )
    assert code == 0
    assert server.served
    assert runner.calls == [(("open", f"http://127.0.0.1:{PORT}/"), False)]
    assert "stopped" in capsys.readouterr().out


def test_cmd_browse_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.cmd_browse(cfg_for(tmp_path), FakeRunner(), PORT, open_page=False) == 1
    (tmp_path / "data").mkdir()
    db.connect(tmp_path / "data" / db.FILENAME).close()

    def busy(app: App, port: int) -> FakeServer:
        raise OSError("address in use")

    code = commands.cmd_browse(cfg_for(tmp_path), FakeRunner(), PORT, open_page=False, serve=busy)  # type: ignore[arg-type]
    assert code == 1
    assert "Try --port" in capsys.readouterr().err


def test_real_server_answers(setup: tuple[App, FakeRunner, Path]) -> None:
    import http.client  # noqa: PLC0415
    import threading  # noqa: PLC0415

    app, _, _ = setup
    server = browse.serve(app, 0)
    port = server.server_address[1]
    real = App(app._db_file, app._data_dir, app._runner, port)
    server.RequestHandlerClass = browse.make_handler(real)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        client.request("GET", f"/audio/{WAV}", headers={"Range": "bytes=0-3"})
        response = client.getresponse()
        assert (response.status, response.read()) == (206, b"RIFF")
        assert response.getheader("Accept-Ranges") == "bytes"
        origin = {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
        client.request("POST", "/api/rating", body=b"{}", headers=origin)
        refused = client.getresponse()
        assert (refused.status, refused.read()) == (
            400,
            b"stars must be a whole number from 0 to 5",
        )
        client.request("POST", "/api/rating", body=b"x" * (browse.MAX_BODY + 1), headers=origin)
        assert client.getresponse().status == 413
        client.close()
    finally:
        server.shutdown()
        server.server_close()
