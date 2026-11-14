from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mph import cli, config
from mph.catalogue import commands, db, embedded, remote, search
from mph.catalogue.remote import Archive, Fetcher, Freesound, RemoteError
from mph.catalogue.scanner import Scanner
from mph.proc import Result
from tests.fakes import FakeHttp, FakeRunner, FakeStore

FS = "https://freesound.org/apiv2"
PROBE = json.dumps(
    {
        "streams": [{"codec_name": "pcm_s24le", "sample_rate": "48000", "channels": 2}],
        "format": {"format_name": "wav"},
    }
)
KEYS = {"freesound.client_id": "cid-fake", "freesound.client_secret": "csecret-fake"}

FREESOUND_RESULTS = {
    "results": [
        {
            "id": 101,
            "name": "Djembe slap 01.wav",
            "username": "drummer",
            "license": "http://creativecommons.org/publicdomain/zero/1.0/",
            "type": "wav",
            "tags": ["djembe", "percussion", "african"],
            "url": "https://freesound.org/people/drummer/sounds/101/",
            "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/101-hq.mp3"},
        },
        {
            "id": 102,
            "name": "Conga loop 120bpm.aiff",
            "username": "perc",
            "license": "https://creativecommons.org/licenses/by-nc/4.0/",
            "type": "aiff",
            "tags": ["conga", "loop"],
            "url": "https://freesound.org/people/perc/sounds/102/",
            "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/102-hq.mp3"},
        },
    ]
}


def freesound_http() -> FakeHttp:
    return FakeHttp(
        json_by_prefix={f"{FS}/search/text/": FREESOUND_RESULTS},
        files={
            f"{FS}/sounds/101/download/": b"RIFF djembe original",
            f"{FS}/sounds/102/download/": b"FORM conga original",
            "https://cdn.freesound.org/previews/101-hq.mp3": b"ID3 djembe preview",
        },
    )


def archive_http() -> FakeHttp:
    return FakeHttp(
        json_by_prefix={
            "https://archive.org/advancedsearch.php": {
                "response": {
                    "docs": [
                        {"identifier": "poems_01", "title": "Poems", "creator": ["A. Reader"]},
                        {"identifier": "talk", "title": "Talk", "licenseurl": ""},
                    ]
                }
            },
            "https://archive.org/metadata/poems_01": {
                "files": [
                    {"name": "poem_64kb.mp3", "format": "64Kbps MP3"},
                    {"name": "poem.flac", "format": "Flac"},
                    {"name": "poem.png", "format": "PNG"},
                ]
            },
            "https://archive.org/metadata/talk": {"files": [{"name": "t.txt", "format": "Text"}]},
        },
        files={"https://archive.org/download/poems_01/poem.flac": b"fLaC spoken word"},
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def fetcher(conn: sqlite3.Connection, http: FakeHttp, tmp_path: Path) -> Fetcher:
    runner = FakeRunner(responses={"ffprobe": Result(0, PROBE)})
    return Fetcher(conn, runner, http, tmp_path / "data", clock=lambda: "t")


def signed_in() -> FakeStore:
    return FakeStore({**KEYS, remote.REFRESH_ACCOUNT: "ref1"})


@pytest.mark.req("REQ-CAT-07")
def test_freesound_originals_with_licence_and_attribution(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    http, store = freesound_http(), signed_in()
    report = fetcher(conn, http, tmp_path).fetch(
        Freesound(http, store), "djembe", 5, clear_only=False
    )
    assert len(report.fetched) == 2
    assert "(cc0)" in report.fetched[0]
    assert "(cc-by-nc)" in report.fetched[1]
    assert http.downloads[0] == (f"{FS}/sounds/101/download/", {"Authorization": "Bearer acc"})
    assert store.values[remote.REFRESH_ACCOUNT] == "ref2"  # Freesound rotates refresh tokens
    assert "token=csecret-fake" in http.gets[0]
    path = tmp_path / "data/downloads/freesound/101/Djembe slap 01.wav"
    assert path.read_bytes() == b"RIFF djembe original"
    row = conn.execute("SELECT * FROM download WHERE remote_id = '101'").fetchone()
    assert (row["licence"], row["creator"], row["rel_path"]) == (
        "cc0",
        "drummer",
        "101/Djembe slap 01.wav",
    )
    roles = {r[0] for r in conn.execute("SELECT value FROM tag WHERE name = 'role'")}
    assert {"djembe", "perc", "conga"} <= roles
    assert remote.attribution(conn, [row["asset_hash"]]) == [
        '"Djembe slap 01.wav" by drummer (cc0), https://freesound.org/people/drummer/sounds/101/'
    ]


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.req("REQ-CAT-07")
def test_new_name_rules_leave_downloads_labels_alone(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = freesound_http()
    fetcher(conn, http, tmp_path).fetch(Freesound(http, signed_in()), "x", 5, clear_only=False)
    labels = "SELECT name, value, origin, confidence FROM tag WHERE asset_hash = ? ORDER BY 1, 2, 3"
    conga, djembe = (
        conn.execute(
            "SELECT asset_hash FROM download WHERE remote_id = ?", (remote_id,)
        ).fetchone()[0]
        for remote_id in ("102", "101")
    )
    fetched = conn.execute(labels, (conga,)).fetchall()
    samples = tmp_path / "samples"
    (samples / "Hand Drums").mkdir(parents=True)
    (samples / "Hand Drums/Djembe Slap.wav").write_bytes(b"RIFF djembe original")  # in a pack too
    # New name rules: the next scan reads every name again.
    monkeypatch.setattr("mph.catalogue.names.VERSION", 999)
    runner = FakeRunner(responses={"ffprobe": Result(0, PROBE)})
    Scanner(conn, runner, clock=lambda: "t").scan(
        config.Source("dj", "local", str(samples), "commercial-pack")
    )
    assert conn.execute(labels, (conga,)).fetchall() == fetched  # from its title and keywords
    packs = {row[0] for row in conn.execute("SELECT value FROM tag WHERE name = 'pack'")}
    assert packs == {"freesound", "Hand Drums"}  # never a remote id such as 101
    found = {tuple(row) for row in conn.execute(labels, (djembe,))}
    assert ("role", "perc", "filename", 0.3) in found  # from its keywords, kept
    assert ("pack", "Hand Drums", "folder", 1.0) in found  # and its local path is read too


@pytest.mark.req("REQ-CAT-07")
def test_downloads_are_searchable_by_licence(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = freesound_http()
    fetcher(conn, http, tmp_path).fetch(Freesound(http, signed_in()), "x", 5, clear_only=False)
    clear = search.search(conn, search.Query(licence="cc0"))
    assert [h.path for h in clear.hits] == ["101/Djembe slap 01.wav"]
    hits = search.search(conn, search.Query(roles=("djembe",)))
    assert hits.matched == 1


@pytest.mark.req("REQ-CAT-07")
def test_clear_only_filters_at_the_source(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = freesound_http()
    fetcher(conn, http, tmp_path).fetch(Freesound(http, signed_in()), "x", 5, clear_only=True)
    assert "Creative+Commons+0" in http.gets[0]


def test_previews_need_no_sign_in(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = freesound_http()
    store = FakeStore(dict(KEYS))
    report = fetcher(conn, http, tmp_path).fetch(
        Freesound(http, store, previews=True), "djembe", 1, clear_only=False
    )
    assert len(report.fetched) == 1
    assert http.downloads == [("https://cdn.freesound.org/previews/101-hq.mp3", {})]
    assert (tmp_path / "data/downloads/freesound/101/Djembe slap 01.preview.mp3").is_file()


def test_freesound_explains_missing_setup(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = freesound_http()
    with pytest.raises(RemoteError, match="mph secrets set freesound"):
        Freesound(http, FakeStore()).search("x", 1, clear_only=False)
    with pytest.raises(RemoteError, match="mph catalogue auth freesound"):
        Freesound(http, FakeStore(dict(KEYS))).search("x", 1, clear_only=False)


@pytest.mark.req("REQ-CAT-07")
def test_sign_in_stores_only_the_refresh_token() -> None:
    http, store = FakeHttp(), FakeStore(dict(KEYS))
    freesound = Freesound(http, store)
    url = freesound.authorize_url(verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
    assert url == (
        f"{FS}/oauth2/authorize/?client_id=cid-fake&response_type=code"
        "&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256"
    )  # the RFC 7636 appendix B example pair
    freesound.sign_in("  the-code \n")
    assert http.posts[0][1]["code"] == "the-code"
    assert http.posts[0][1]["code_verifier"] == "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert http.posts[0][1]["grant_type"] == "authorization_code"
    assert store.values[remote.REFRESH_ACCOUNT] == "ref2"
    assert "acc" not in store.values.values()  # short-lived token stays in memory


def test_fresh_verifiers_are_valid_pkce() -> None:
    first, second = remote.new_verifier(), remote.new_verifier()
    assert first != second
    assert 43 <= len(first) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_sign_in_failures() -> None:
    with pytest.raises(RemoteError, match="sign-in link first"):
        Freesound(FakeHttp(), FakeStore(dict(KEYS))).sign_in("code")
    http = FakeHttp(post_reply={"error": "invalid_grant"})
    freesound = Freesound(http, FakeStore(dict(KEYS)))
    freesound.authorize_url()
    with pytest.raises(RemoteError, match="sign-in failed"):
        freesound.sign_in("bad")
    freesound = Freesound(FakeHttp(), FakeStore(dict(KEYS), fail_puts=True))
    freesound.authorize_url()
    with pytest.raises(RemoteError, match="Keychain"):
        freesound.sign_in("ok")


@pytest.mark.req("REQ-CAT-07")
def test_archive_picks_best_quality_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = archive_http()
    report = fetcher(conn, http, tmp_path).fetch(Archive(http), "poems", 5, clear_only=False)
    assert len(report.fetched) == 1  # "talk" has no audio files
    row = conn.execute("SELECT * FROM download").fetchone()
    assert (row["remote_id"], row["licence"], row["creator"]) == (
        "poems_01/poem.flac",
        "unknown",
        "A. Reader",
    )
    assert "mediatype%3Aaudio" in http.gets[0]


@pytest.mark.req("REQ-CAT-07")
def test_librivox_is_public_domain_spoken_word(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = archive_http()
    report = fetcher(conn, http, tmp_path).fetch(remote.librivox(http), "poems", 5, clear_only=True)
    assert len(report.fetched) == 1
    assert "collection%3Alibrivoxaudio" in http.gets[0]
    assert conn.execute("SELECT licence FROM download").fetchone()[0] == "public-domain"
    assert remote.attribution(conn, ["nope"]) == []


@pytest.mark.req("REQ-CAT-07")
def test_already_fetched_and_refresh(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = archive_http()
    get = fetcher(conn, http, tmp_path)
    get.fetch(Archive(http), "poems", 5, clear_only=False)
    assert get.fetch(Archive(http), "poems", 5, clear_only=False).already == 1
    path = tmp_path / "data/downloads/archive/poems_01_poem.flac/poem.flac"
    path.unlink()
    again = get.refresh({"archive": Archive(http)})
    assert (len(again.fetched), again.failed) == (1, [])
    assert path.read_bytes() == b"fLaC spoken word"
    assert get.refresh({"archive": Archive(http)}).already == 1
    path.unlink()
    http.files["https://archive.org/download/poems_01/poem.flac"] = b"fLaC re-uploaded"
    changed = get.refresh({"archive": Archive(http)})
    assert "changed since it was fetched" in changed.failed[0]
    assert not path.exists()
    assert "no connector" in get.refresh({}).failed[0]


@pytest.mark.req("REQ-CAT-12")
def test_a_download_says_what_it_is(conn: sqlite3.Connection, tmp_path: Path) -> None:
    tags = {"TITLE": "Poem One", "ARTIST": "A. Reader", "DATE": "1921-03-01"}
    tagged = {"streams": [{"codec_name": "flac"}], "format": {"format_name": "flac", "tags": tags}}
    runner = FakeRunner(responses={"ffprobe": Result(0, json.dumps(tagged))})
    http = archive_http()
    get = Fetcher(conn, runner, http, tmp_path / "data", clock=lambda: "t")
    get.fetch(Archive(http), "poems", 5, clear_only=False)
    labels = conn.execute("SELECT name, value, confidence FROM tag WHERE origin = 'embedded'")
    assert {tuple(row) for row in labels} == {
        ("title", "Poem One", 1.0),
        ("artist", "A. Reader", 1.0),
        ("year", "1921", 1.0),
    }
    row = conn.execute("SELECT version, read_at, error FROM embedded").fetchone()
    assert tuple(row) == (embedded.VERSION, "t", None)


def test_failed_download_is_reported(conn: sqlite3.Connection, tmp_path: Path) -> None:
    http = archive_http()
    http.files.clear()
    report = fetcher(conn, http, tmp_path).fetch(Archive(http), "poems", 5, clear_only=False)
    assert report.fetched == []
    assert "404" in report.failed[0]


@pytest.mark.parametrize(
    ("text", "short"),
    [
        ("http://creativecommons.org/publicdomain/zero/1.0/", "cc0"),
        ("Creative Commons 0", "cc0"),
        ("https://creativecommons.org/publicdomain/mark/1.0/", "public-domain"),
        ("http://creativecommons.org/licenses/by/3.0/", "cc-by"),
        ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "cc-by-nc-sa"),
        ("http://creativecommons.org/licenses/sampling+/1.0/", "sampling-plus"),
        ("Attribution NonCommercial", "cc-by-nc"),
        ("Attribution", "cc-by"),
        ("", "unknown"),
    ],
)
def test_short_licence(text: str, short: str) -> None:
    assert remote.short_licence(text) == short


@pytest.mark.parametrize(
    ("name", "safe"),
    [("../../etc/passwd", "passwd"), ("a:b?.wav", "a_b_.wav"), ("...", "sound")],
)
def test_safe_name(name: str, safe: str) -> None:
    assert remote.safe_name(name) == safe


# --- commands --------------------------------------------------------------------------


def cfg_for(tmp_path: Path) -> config.Config:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    return config.parse(raw)


def args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["catalogue", *argv])


RUNNER = FakeRunner(responses={"ffprobe": Result(0, PROBE)})


@pytest.mark.req("REQ-CAT-07")
def test_cli_fetch_credits_refresh(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, http = cfg_for(tmp_path), archive_http()
    run = commands.dispatch
    assert run(args("fetch", "librivox", "poems"), cfg, RUNNER, None, http) == 0
    out = capsys.readouterr().out
    assert "1 downloaded, 0 already here, 0 failed" in out
    assert run(args("credits"), cfg, RUNNER, None, http) == 0
    assert '"Poems" by A. Reader (public-domain)' in capsys.readouterr().out
    assert run(args("refresh"), cfg, RUNNER, None, http) == 0
    assert "downloads: 1 present" in capsys.readouterr().out


def test_cli_freesound_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg, http = cfg_for(tmp_path), freesound_http()
    assert commands.dispatch(args("fetch", "freesound", "x"), cfg, RUNNER, None, http) == 2
    assert "Keychain" in capsys.readouterr().err
    store = FakeStore(dict(KEYS))
    assert commands.dispatch(args("fetch", "freesound", "x"), cfg, RUNNER, store, http) == 2
    assert "auth freesound" in capsys.readouterr().err
    assert commands.dispatch(args("credits"), cfg, RUNNER, store, http) == 0
    assert "nothing downloaded yet" in capsys.readouterr().out
    assert commands.dispatch(args("auth", "freesound"), cfg, RUNNER, None, http) == 2


def test_cli_sign_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store, http = FakeStore(dict(KEYS)), FakeHttp()
    assert commands._sign_in(store, http, ask=lambda _: "code-1") == 0
    out = capsys.readouterr().out
    assert "oauth2/authorize/?client_id=cid-fake" in out
    assert "code_challenge_method=S256" in out
    assert "code_verifier" in http.posts[0][1]
    assert store.values[remote.REFRESH_ACCOUNT] == "ref2"


def test_cli_fetch_all_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    http = archive_http()
    http.files.clear()
    assert (
        commands.dispatch(args("fetch", "archive", "x"), cfg_for(tmp_path), RUNNER, None, http) == 1
    )
    assert "  ! Poems" in capsys.readouterr().out
