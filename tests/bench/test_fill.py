"""Copying a project and filling its sounds with free placeholders
(spec/components/projects.md: REQ-PRJ-04, REQ-PRJ-05)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from mph.bench import fill, kit, project_commands, projects, score
from mph.catalogue import db, remote
from mph.proc import Result
from tests.bench.test_projects import args, cfg_with, make_project, run
from tests.bench.test_render import add_sample
from tests.fakes import FakeHttp, FakeRunner, FakeStore

FS = "https://freesound.org/apiv2"
PROBE = json.dumps(
    {
        "streams": [{"codec_name": "pcm_s16le", "sample_rate": "44100", "channels": 1}],
        "format": {"format_name": "wav"},
    }
)
KEYS = {"freesound.client_id": "cid-fake", "freesound.client_secret": "csecret-fake"}
FOUND = {
    "results": [
        {
            "id": 7,
            "name": "Deep kick.wav",
            "username": "maker",
            "license": "http://creativecommons.org/publicdomain/zero/1.0/",
            "type": "wav",
            "tags": ["drum"],
            "url": "https://freesound.org/people/maker/sounds/7/",
            "previews": {
                "preview-hq-mp3": "https://cdn.freesound.org/previews/7-hq.mp3",
                "preview-hq-ogg": "https://cdn.freesound.org/previews/7-hq.ogg",
            },
        }
    ]
}
GROOVE = """id = "BM-GRV-00"
bpm = 120
bars = 1
[voices.kick]
grid = "x . . . x . . . x . . . x . . ."
sound = {{ role = "kick"{words} }}
[voices.ride]
grid = ". . x . . . x . . . x . . . x ."
sound = {{ role = "ride" }}
[voices.pad]
notes = "C3 - - - - - - - - - - - - - - -"
gate  = "x  - - - - - - - - - - - - - - ."
sound = {{ role = "pad", synth = "mph_pad" }}
"""


def groove(root: Path, words: str = "") -> Path:
    folder = make_project(root, "grooves", "Grooves")
    (folder / "BM-GRV-00.toml").write_text(GROOVE.format(words=words))
    return folder


def freesound_http() -> FakeHttp:
    return FakeHttp(
        json_by_prefix={f"{FS}/search/text/": FOUND},
        files={
            "https://cdn.freesound.org/previews/7-hq.ogg": b"OggS kick preview",
            f"{FS}/sounds/7/download/": b"RIFF kick original",
        },
    )


def analysed_as(form: str) -> tuple[list[list[str]], project_commands.Analyse]:
    calls: list[list[str]] = []

    def analyse(conn: sqlite3.Connection, hashes: list[str]) -> object:
        calls.append(list(hashes))
        with conn:
            for digest in hashes:
                conn.execute(
                    "INSERT OR REPLACE INTO analysis VALUES (?, 'core', 2, 't', ?, NULL)",
                    (digest, json.dumps({"form": form})),
                )
        return None

    return calls, analyse


def fill_run(
    cfg: object,
    *argv: str,
    store: FakeStore | None = None,
    http: FakeHttp | None = None,
    analyse: project_commands.Analyse | None = None,
) -> int:
    runner = FakeRunner(responses={"ffprobe": Result(0, PROBE)})
    return project_commands.dispatch(
        args("fill", *argv),
        cfg,  # type: ignore[arg-type]
        runner,
        store,
        http=http or FakeHttp(),
        analyse=analyse or analysed_as("oneshot")[1],
    )


# --- copy (REQ-PRJ-04) ---------------------------------------------------------------------


@pytest.mark.req("REQ-PRJ-04")
def test_copy_makes_a_project_your_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mine = tmp_path / "mine"
    mine.mkdir()
    cfg = cfg_with(tmp_path, {"projects": {"folders": [str(mine)]}})
    assert run(cfg, "copy", "sidechain") == 0
    copied = mine / "sidechain"
    assert sorted(p.name for p in copied.iterdir()) == [
        "BM-SC-01.toml",
        "README.md",
        "project.toml",
    ]
    out = capsys.readouterr().out
    assert f"copied 3 files of Sidechain to {copied}" in out
    assert "mph uses yours instead of the example's" in out and "add " not in out
    assert projects.current().owner("BM-SC-01").folder == copied
    assert run(cfg, "copy", "sidechain") == 2  # never over what is there
    assert "is there already" in capsys.readouterr().err


@pytest.mark.req("REQ-PRJ-04")
def test_copy_needs_a_folder_and_never_writes_in_a_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = cfg_with(tmp_path, {})
    assert run(plain, "copy", "bass") == 2
    assert "--to FOLDER" in capsys.readouterr().err
    elsewhere = tmp_path / "elsewhere"
    assert run(plain, "copy", "bass", "--to", str(elsewhere)) == 0
    assert f"add {elsewhere} to folders under [projects]" in capsys.readouterr().out
    assert (elsewhere / "bass" / "BM-BASS-01.toml").is_file()
    samples = tmp_path / "samples"
    samples.mkdir()
    source = {"sources": [{"id": "dj", "kind": "local", "path": str(samples)}]}
    assert run(cfg_with(tmp_path, source), "copy", "bass", "--to", str(samples)) == 2
    assert "inside source dj" in capsys.readouterr().err
    assert not any(samples.iterdir())
    empty = make_project(tmp_path / "plans", "plans", "Plans")
    assert run(plain, "copy", "Plans", "--to", str(tmp_path / "out")) == 2  # not listed yet
    planned = cfg_with(tmp_path, {"projects": {"folders": [str(empty.parent)]}})
    assert run(planned, "copy", "Plans", "--to", str(tmp_path / "out")) == 0
    assert "keeps the example's benchmark ids" not in capsys.readouterr().out


# --- the plan (REQ-PRJ-05) -----------------------------------------------------------------


@pytest.mark.req("REQ-PRJ-05")
def test_the_plan_lists_what_the_catalogue_lacks(tmp_path: Path) -> None:
    projects.use(projects.Settings(examples=False, folders=(groove(tmp_path / "mine").parent,)))
    loaded = [score.load("BM-GRV-00")]
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    try:
        bare = fill.plan(loaded, conn, tmp_path / "data")
        assert (bare.voices, bare.synths, bare.covered, bare.missing) == (3, 1, 0, 2)
        kick, ride = bare.needs
        assert (kick.role, kick.form, kick.meanwhile, kick.query) == (
            "kick",
            "oneshot",
            "mph_kick",
            "kick drum",
        )
        assert kick.voices == ("BM-GRV-00 kick",)
        assert (ride.meanwhile, ride.query) == ("", "ride cymbal")  # no synth stands in
        assert ride.filters == ("duration:[0 TO 5]",)
        add_sample(conn, tmp_path / "lib", "Kick.wav", [("role", "kick")])
        found = fill.plan(loaded, conn, tmp_path / "data")
        assert [n.role for n in found.needs] == ["ride"] and found.covered == 1
        assert json.loads(json.dumps(found.to_dict()))["needs"][0]["query"] == "ride cymbal"
    finally:
        conn.close()
    assert len(fill.plan(loaded, None, tmp_path).needs) == 2  # no catalogue at all


@pytest.mark.req("REQ-PRJ-05")
def test_deans_examples_need_only_free_sounds_and_his_two_stems(tmp_path: Path) -> None:
    every = [score.load(b) for b in score.available()]
    found = fill.plan(every, None, tmp_path)
    assert {r.hash[:12] for r in found.recordings} == {"c2b24168a2cb", "4ed4ea4d21f7"}
    unrenderable = {(n.role, n.form) for n in found.needs if not n.meanwhile}
    assert unrenderable == {("break", "loop"), ("ride", "oneshot")}
    breaks = next(n for n in found.needs if n.role == "break")
    assert breaks.query == "breakbeat loop" and breaks.filters == ("duration:[1 TO 30]",)
    assert found.voices == found.synths + found.covered + found.missing


@pytest.mark.req("REQ-PRJ-05")
def test_a_pinned_sound_or_recording_that_is_here_covers_its_voice(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    try:
        digest = add_sample(conn, tmp_path / "lib", "Ride.wav", [("role", "cymbal")])
        pinned = score.load_text(
            'id = "BM-PIN-00"\nbpm = 120\nbars = 1\n[voices.ride]\n'
            'grid = "x . . . x . . . x . . . x . . ."\n'
            f'sound = {{ role = "ride", hash = "{digest}", why = "the only ride" }}\n'
        )
        assert kit.covered(conn, tmp_path / "data", pinned.voice("ride"))
        assert fill.plan([pinned], conn, tmp_path / "data").needs == ()
    finally:
        conn.close()


# --- fetching (REQ-PRJ-05) -----------------------------------------------------------------


@pytest.mark.req("REQ-PRJ-05")
def test_fill_lists_first_and_downloads_nothing_without_yes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = tmp_path / "mine"
    groove(mine)
    cfg = cfg_with(tmp_path, {"projects": {"examples": False, "folders": [str(mine)]}})
    http = freesound_http()
    assert fill_run(cfg, http=http) == 0
    out = capsys.readouterr().out
    assert "3 voices in 1 benchmarks: 1 are synths on purpose, 0 have a sound" in out
    assert '"kick drum", plays mph_kick meanwhile: BM-GRV-00 kick' in out
    assert '"ride cymbal", can\'t render: BM-GRV-00 ride' in out
    assert "no catalogue yet" in out and "add --yes" in out
    db.connect(tmp_path / "data" / db.FILENAME).close()
    assert fill_run(cfg, http=http) == 0
    assert "no catalogue yet" not in capsys.readouterr().out
    assert http.gets == [] and http.downloads == []


@pytest.mark.req("REQ-PRJ-05")
def test_fill_takes_free_previews_labels_and_analyses_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = tmp_path / "mine"
    groove(mine)
    cfg = cfg_with(tmp_path, {"projects": {"examples": False, "folders": [str(mine)]}})
    http = freesound_http()
    calls, analyse = analysed_as("oneshot")
    store = FakeStore(dict(KEYS))  # keys, but no sign-in: previews
    assert fill_run(cfg, "--yes", "--limit", "1", store=store, http=http, analyse=analyse) == 0
    asked = [parse_qs(urlsplit(url).query) for url in http.gets]
    assert [a["query"][0] for a in asked] == ["kick drum", "ride cymbal"]
    assert all('"Creative Commons 0" OR "Attribution"' in a["filter"][0] for a in asked)
    assert all("duration:[0 TO 5]" in a["filter"][0] for a in asked)
    assert all("type:" not in a["filter"][0] for a in asked)  # previews are all OGG
    assert http.downloads[0][0].endswith("7-hq.ogg")
    (digest,) = {h for c in calls for h in c}
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    try:
        roles = {r[0] for r in conn.execute("SELECT value FROM tag WHERE name = 'role'")}
        assert {"kick", "ride"} <= roles  # what it was found for, both times
        assert conn.execute("SELECT licence FROM download").fetchone()[0] == "cc0"
    finally:
        conn.close()
    out = capsys.readouterr().out
    assert "OGG previews" in out and "mph catalogue credits" in out
    assert "still missing" not in out and digest


@pytest.mark.req("REQ-PRJ-05")
def test_fill_takes_originals_when_signed_in_and_says_what_is_still_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = tmp_path / "mine"
    groove(mine)
    cfg = cfg_with(tmp_path, {"projects": {"examples": False, "folders": [str(mine)]}})
    http = freesound_http()
    store = FakeStore({**KEYS, remote.REFRESH_ACCOUNT: "ref1"})
    _, loops = analysed_as("loop")  # the analysis says they are loops, not one-shots
    assert fill_run(cfg, "--yes", "grooves", store=store, http=http, analyse=loops) == 1
    filters = parse_qs(urlsplit(http.gets[0]).query)["filter"][0]
    assert "type:(wav OR aif OR aiff OR flac OR ogg)" in filters
    assert http.downloads[0][0] == f"{FS}/sounds/7/download/"
    out = capsys.readouterr().out
    assert "original files" in out
    assert 'still missing: kick oneshot ("kick drum")' in out


@pytest.mark.req("REQ-PRJ-05")
def test_fill_from_the_archive_and_its_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = tmp_path / "mine"
    groove(mine)
    cfg = cfg_with(tmp_path, {"projects": {"examples": False, "folders": [str(mine)]}})
    assert fill_run(cfg, "--yes", store=None) == 2  # Freesound needs the Keychain
    assert "macOS Keychain" in capsys.readouterr().err
    assert fill_run(cfg, "--yes", store=FakeStore()) == 2  # and its keys
    assert "mph secrets set freesound" in capsys.readouterr().err
    archive = FakeHttp(
        json_by_prefix={
            "https://archive.org/advancedsearch.php": {
                "response": {
                    "docs": [
                        {
                            "identifier": "drums",
                            "title": "Drums",
                            "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/",
                        }
                    ]
                }
            },
            "https://archive.org/metadata/drums": {
                "files": [{"name": "kick.flac", "format": "Flac"}]
            },
        },
        files={},  # the download fails
    )
    assert fill_run(cfg, "--yes", "--from", "archive", http=archive) == 1
    out = capsys.readouterr().out
    assert "fetching from archive (whole items)" in out and "! Drums" in out
    assert "still missing: kick oneshot" in out


@pytest.mark.req("REQ-PRJ-05")
def test_fill_needs_the_projects_on_and_skips_what_needs_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_with(tmp_path, {"projects": {"examples": ["bass"]}})
    assert fill_run(cfg, "drum-patterns") == 2
    assert "turn drum-patterns on first" in capsys.readouterr().err
    db.connect(tmp_path / "data" / db.FILENAME).close()
    assert fill_run(cfg, "--yes", "bass") == 0  # the acid line is a synth
    out = capsys.readouterr().out
    assert "1 voices in 1 benchmarks: 1 are synths on purpose" in out
    assert "fetching" not in out
    mine = tmp_path / "mine"
    make_project(mine, "mixed", "Mixed", "BM-MXA-00")
    (mine / "mixed" / "BM-MXB-00.toml").write_text("not = [toml")
    (mine / "mixed" / "BM-MXC-00.toml").write_text(
        'id = "BM-MXC-00"\nbpm = 120\nbars = 1\n[voices.kick]\n'
        'grid = "x . . . x . . . x . . . x . . ."\n'
    )
    shadow = make_project(mine, "copy-of-bass", "Bass", "BM-BASS-01")
    assert shadow.is_dir()
    own = cfg_with(tmp_path, {"projects": {"examples": True, "folders": [str(mine)]}})
    assert fill_run(own, "mixed", "bass") == 0
    captured = capsys.readouterr()
    assert "BM-MXB-00" in captured.err
    assert "2 voices in 2 benchmarks" in captured.out  # bass is the user's copy, not Dean's
