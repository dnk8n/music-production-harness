from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path

import pytest

from mph.catalogue import db
from mph.catalogue.scanner import Scanner, SourceError, classify
from mph.config import Source
from mph.proc import Result
from tests.catalogue import audiofiles as af
from tests.fakes import FakeRunner


def ffprobe(argv: Sequence[str]) -> Result:
    if argv[-1].endswith("broken.wav"):
        return Result(1, stderr="Invalid data found")
    stream = {
        "codec_name": "pcm_s16le",
        "sample_rate": "44100",
        "channels": 2,
        "bits_per_sample": 16,
        "duration": "1.0",
    }
    return Result(0, json.dumps({"streams": [stream], "format": {"format_name": "wav"}}))


def write(root: Path, rel: str, content: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "samples"
    write(root, "Pack A/Acid Techno (Ab, 131bpm)/Drums/Kick 01.wav", b"kick-bytes")
    write(root, "Pack A/Acid Techno (Ab, 131bpm)/Drums/Kick 01 copy.wav", b"kick-bytes")
    write(root, "Pack A/Loops/MXE_128_Vocals_19.wav", b"vocal-bytes")
    write(root, "Pack A/Loops/MXE_128_Vocals_19.wav.asd", b"ableton")
    write(root, "Pack A/.DS_Store", b"junk")
    write(root, "Pack A/._Kick 01.wav", b"resource fork")
    write(root, "Pack A/Read Me.txt", b"licence terms")
    write(root, "Pack B/broken.wav", b"not audio")
    return root


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def source(root: Path) -> Source:
    return Source("dj", "local", str(root), "commercial-pack")


def scanner(conn: sqlite3.Connection) -> Scanner:
    return Scanner(
        conn,
        FakeRunner(responses={"ffprobe": ffprobe}),
        workers=2,
        clock=lambda: "2026-11-08T00:00:00+00:00",
    )


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        p.relative_to(root).as_posix(): (
            p.stat().st_size,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    } | {"dirs": (sum(1 for p in root.rglob("*") if p.is_dir()), 0, "")}


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


@pytest.mark.req("REQ-CAT-01")
def test_scan_never_modifies_the_source(conn: sqlite3.Connection, library: Path) -> None:
    before = snapshot(library)
    scanner(conn).scan(source(library))
    scanner(conn).scan(source(library))
    assert snapshot(library) == before


@pytest.mark.req("REQ-CAT-01")
def test_source_files_only_opened_read_only(
    conn: sqlite3.Connection, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modes: list[str] = []
    real_open = Path.open

    def spy(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        if self.is_relative_to(library):
            modes.append(mode)
        return real_open(self, mode, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", spy)
    scanner(conn).scan(source(library))
    assert modes
    assert set(modes) == {"rb"}


@pytest.mark.req("REQ-CAT-02")
def test_first_scan_indexes_and_dedupes(conn: sqlite3.Connection, library: Path) -> None:
    stats = scanner(conn).scan(source(library))
    assert (stats.files_seen, stats.hashed, stats.new_assets, stats.skipped) == (4, 4, 3, 0)
    assert count(conn, "source_file") == 4
    assert count(conn, "asset") == 3  # the copy of Kick 01 is the same content


@pytest.mark.req("REQ-CAT-02")
def test_rescan_skips_unchanged_files(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    stats = scanner(conn).scan(source(library))
    assert (stats.skipped, stats.hashed, stats.new_assets, stats.removed) == (4, 0, 0, 0)


@pytest.mark.req("REQ-CAT-02")
def test_rename_is_a_move_and_keeps_identity(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    old = library / "Pack A/Loops/MXE_128_Vocals_19.wav"
    os.rename(old, library / "Pack A/Loops/Spoken Vox.wav")  # test-only edit of a fixture
    stats = scanner(conn).scan(source(library))
    assert (stats.moved, stats.removed, stats.new_assets) == (1, 0, 0)
    assert count(conn, "asset") == 3
    paths = {r[0] for r in conn.execute("SELECT rel_path FROM source_file")}
    assert "Pack A/Loops/Spoken Vox.wav" in paths
    assert "Pack A/Loops/MXE_128_Vocals_19.wav" not in paths


@pytest.mark.req("REQ-CAT-02")
def test_changed_content_replaces_asset(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    target = library / "Pack A/Loops/MXE_128_Vocals_19.wav"
    target.write_bytes(b"re-exported vocal")
    os.utime(target, ns=(1, 1))
    stats = scanner(conn).scan(source(library))
    assert (stats.hashed, stats.new_assets) == (1, 1)
    assert count(conn, "asset") == 3  # old content had no other path, so it was dropped


@pytest.mark.req("REQ-CAT-02")
def test_deleted_file_is_removed(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    (library / "Pack B/broken.wav").unlink()
    stats = scanner(conn).scan(source(library))
    assert stats.removed == 1
    assert count(conn, "asset") == 2


@pytest.mark.req("REQ-CAT-03")
def test_name_priors_stored_as_tags(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    tags = {(r["name"], r["value"], r["origin"]) for r in conn.execute("SELECT * FROM tag")}
    assert ("bpm", "131", "folder") in tags
    assert ("key", "G#:major", "folder") in tags
    assert ("role", "kick", "filename") in tags
    assert ("role", "vocal", "filename") in tags
    assert ("pack", "Pack A", "folder") in tags


@pytest.mark.req("REQ-CAT-03")
def test_tags_follow_renames(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    os.rename(library / "Pack A/Loops", library / "Pack A/Kicks")
    scanner(conn).scan(source(library))
    roles = {r[0] for r in conn.execute("SELECT value FROM tag WHERE name = 'role'")}
    assert "kick" in roles
    folder_loops = conn.execute(
        "SELECT COUNT(*) FROM tag WHERE name = 'form' AND value = 'loop'"
    ).fetchone()[0]
    assert folder_loops == 0


@pytest.mark.req("REQ-CAT-08")
def test_junk_ignored_and_documents_recorded(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    paths = {r[0] for r in conn.execute("SELECT rel_path FROM source_file")}
    assert not any(p.endswith((".asd", ".DS_Store")) or "/._" in p for p in paths)
    docs = list(conn.execute("SELECT rel_path, kind FROM document"))
    assert [(d[0], d[1]) for d in docs] == [("Pack A/Read Me.txt", "txt")]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.WAV", "audio"),
        ("a.aif", "audio"),
        ("a.m4a", "audio"),
        ("x.wav.asd", "skip"),
        (".DS_Store", "skip"),
        ("._a.wav", "skip"),
        ("notes.pdf", "document"),
        ("a.png", "skip"),
    ],
)
def test_classify(name: str, kind: str) -> None:
    assert classify(name) == kind


def test_unreadable_audio_is_recorded_not_fatal(conn: sqlite3.Connection, library: Path) -> None:
    stats = scanner(conn).scan(source(library))
    assert stats.probe_errors == ["Pack B/broken.wav: Invalid data found"]
    row = conn.execute("SELECT probe_error FROM asset WHERE probe_error IS NOT NULL").fetchone()
    assert row[0] == "Invalid data found"


def test_scan_history_recorded(conn: sqlite3.Connection, library: Path) -> None:
    scanner(conn).scan(source(library))
    row = conn.execute("SELECT * FROM scan").fetchone()
    assert (row["files_seen"], row["new_assets"], row["finished_at"]) == (
        4,
        3,
        "2026-11-08T00:00:00+00:00",
    )
    assert conn.execute("SELECT last_scan_at FROM source").fetchone()[0] is not None


def test_missing_source_directory(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        scanner(conn).scan(source(tmp_path / "nope"))


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    db.connect(path).close()
    with closing(db.connect(path)) as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)


@pytest.mark.req("REQ-CAT-03")
def test_new_name_rules_retag_everything(
    conn: sqlite3.Connection, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanner(conn).scan(source(library))
    conn.execute("DELETE FROM tag")
    conn.commit()
    scanner(conn).scan(source(library))
    assert count(conn, "tag") == 0  # nothing changed, nothing retagged
    monkeypatch.setattr("mph.catalogue.names.VERSION", 999)
    scanner(conn).scan(source(library))
    assert count(conn, "tag") > 0
    assert db.get_meta(conn, "name_priors_version") == "999"


def test_old_mov_labels_become_m4a(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with closing(sqlite3.connect(path)) as old:  # a catalogue from before migration 4
        for script in db.MIGRATIONS[:3]:
            old.executescript(script)
        old.execute("PRAGMA user_version = 3")
        old.execute(
            "INSERT INTO asset (hash, bytes, container, origin) VALUES ('a', 1, 'mov', 'source')"
        )
        old.commit()
    with closing(db.connect(path)) as conn:
        assert conn.execute("SELECT container FROM asset").fetchone()[0] == "m4a"


def at_version(path: Path, version: int) -> sqlite3.Connection:
    """A catalogue as an older mph left it."""
    old = sqlite3.connect(path)
    for script in db.MIGRATIONS[:version]:
        old.executescript(script)
    old.execute(f"PRAGMA user_version = {version}")
    return old


@pytest.mark.req("REQ-CAT-12")
def test_upgrading_keeps_every_label_and_allows_the_files_own(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with closing(at_version(path, 9)) as old:
        old.execute("INSERT INTO asset (hash, bytes, origin) VALUES ('a', 1, 'source')")
        old.executemany(
            "INSERT INTO tag VALUES ('a', ?, ?, ?, ?)",
            [
                ("role", "kick", "folder", 0.5),
                ("bpm", "128", "filename", 0.6),
                ("bpm", "127.9", "analysis", 0.9),
                ("role", "kick", "human", 1.0),
            ],
        )
        old.commit()
        before = old.execute("SELECT * FROM tag ORDER BY origin").fetchall()
    with closing(db.connect(path)) as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)
        assert [tuple(r) for r in conn.execute("SELECT * FROM tag ORDER BY origin")] == before
        with conn:
            conn.execute("INSERT INTO tag VALUES ('a', 'bpm', '128', 'embedded', 0.9)")
            conn.execute("INSERT INTO embedded VALUES ('a', 1, 'now', '{}', NULL)")
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "tag_lookup" in indexes
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tag VALUES ('a', 'bpm', '1', 'guess', 0.5)")
        with conn:  # an asset that goes takes what was read from it along
            conn.execute("DELETE FROM tag")
            conn.execute("DELETE FROM asset")
        assert count(conn, "embedded") == 0


def test_a_migration_that_fails_leaves_the_catalogue_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "c.sqlite"
    db.connect(path).close()
    broken = "DROP TABLE tag;\nINSERT INTO no_such_table VALUES (1);"
    monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, broken))
    with pytest.raises(sqlite3.OperationalError, match="no_such_table"):
        db.connect(path)
    with closing(sqlite3.connect(path)) as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS) - 1
        assert conn.execute("SELECT count(*) FROM tag").fetchone()[0] == 0  # still there


# --- what files say about themselves (REQ-CAT-12) ------------------------------------


def tagged_ffprobe(argv: Sequence[str]) -> Result:
    """Like ``ffprobe``, plus a length and tags for the loops written below."""
    if argv[-1].endswith("broken.wav"):
        return Result(1, stderr="Invalid data found")
    stream = {"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "44100"}
    tags = {"title": "Deep Loop 01", "artist": "Loopmasters"} if "Deep" in argv[-1] else {}
    out = {"streams": [stream], "format": {"format_name": "wav", "duration": "4.0", "tags": tags}}
    return Result(0, json.dumps(out))


def tagged_scanner(conn: sqlite3.Connection, runner: FakeRunner | None = None) -> Scanner:
    return Scanner(
        conn,
        runner or FakeRunner(responses={"ffprobe": tagged_ffprobe}),
        workers=2,
        clock=lambda: "2026-11-14T00:00:00+00:00",
    )


@pytest.fixture
def loops(library: Path) -> Path:
    acid = af.riff_chunk(b"acid", af.acid(root=57, beats=8, tempo=120.0))  # 8 beats fill 4 s
    write(library, "Pack C/Deep Loop 01.wav", af.wav(4.0, acid))
    return library


def embedded_tags(conn: sqlite3.Connection, path: str) -> set[tuple[str, str, float]]:
    rows = conn.execute(
        "SELECT t.name, t.value, t.confidence FROM tag t JOIN source_file f "
        "ON f.asset_hash = t.asset_hash WHERE f.rel_path = ? AND t.origin = 'embedded'",
        (path,),
    )
    return {(r[0], r[1], r[2]) for r in rows}


@pytest.mark.req("REQ-CAT-12")
def test_scan_reads_what_files_say_about_themselves(conn: sqlite3.Connection, loops: Path) -> None:
    stats = tagged_scanner(conn).scan(source(loops))
    assert embedded_tags(conn, "Pack C/Deep Loop 01.wav") == {
        ("artist", "Loopmasters", 1.0),
        ("bpm", "120", 0.9),
        ("form", "loop", 0.8),
        ("root", "A", 0.8),
        ("title", "Deep Loop 01", 1.0),
    }
    assert (stats.read_inside, stats.unreadable_inside) == (4, 1)  # broken.wav can't be read
    assert stats.told == {"bpm": 1, "form": 1, "root": 1, "artist": 1, "title": 1}
    found = conn.execute(
        "SELECT found_json FROM embedded e JOIN source_file f ON f.asset_hash = e.asset_hash "
        "WHERE f.rel_path = 'Pack C/Deep Loop 01.wav'"
    ).fetchone()[0]
    assert json.loads(found)["acid"]["tempo"] == 120.0


@pytest.mark.req("REQ-CAT-12")
def test_new_files_are_probed_once(conn: sqlite3.Connection, loops: Path) -> None:
    runner = FakeRunner(responses={"ffprobe": tagged_ffprobe})
    tagged_scanner(conn, runner).scan(source(loops))
    probed = [argv[-1] for argv, _ in runner.calls if argv[0] == "ffprobe"]
    assert len(probed) == len(set(probed)) == 4  # four sounds; the copy of Kick 01 is one


@pytest.mark.req("REQ-CAT-12")
def test_a_catalogue_from_before_reads_its_files_without_hashing_them(
    conn: sqlite3.Connection, loops: Path
) -> None:
    tagged_scanner(conn).scan(source(loops))
    with conn:  # as a catalogue scanned before this reader existed
        conn.execute("DELETE FROM embedded")
        conn.execute("DELETE FROM tag WHERE origin = 'embedded'")
    stats = tagged_scanner(conn).scan(source(loops))
    assert (stats.hashed, stats.read_inside) == (0, 4)
    assert ("bpm", "120", 0.9) in embedded_tags(conn, "Pack C/Deep Loop 01.wav")
    again = tagged_scanner(conn).scan(source(loops))
    assert again.read_inside == 1  # only broken.wav, which is tried every time


@pytest.mark.req("REQ-CAT-12")
def test_a_new_reader_reads_every_file_again(
    conn: sqlite3.Connection, loops: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tagged_scanner(conn).scan(source(loops))
    monkeypatch.setattr("mph.catalogue.embedded.VERSION", 2)
    assert tagged_scanner(conn).scan(source(loops)).read_inside == 4
    assert {r[0] for r in conn.execute("SELECT DISTINCT version FROM embedded")} == {2}


@pytest.mark.req("REQ-CAT-12")
def test_download_stores_are_read_and_missing_files_skipped(
    conn: sqlite3.Connection, library: Path, tmp_path: Path
) -> None:
    store = tmp_path / "downloads" / "freesound"
    write(store, "123/rain.wav", af.wav(4.0, af.riff_chunk(b"acid", af.acid(one_shot=True))))
    with conn:  # two downloads fetched before this reader existed; one was deleted since
        conn.execute(
            "INSERT INTO source VALUES ('freesound', 'remote', ?, 'varies', NULL)", (str(store),)
        )
        for digest, rel in (("d1", "123/rain.wav"), ("d2", "456/gone.wav")):
            conn.execute(
                "INSERT INTO asset (hash, bytes, origin) VALUES (?, 1, 'download')", (digest,)
            )
            conn.execute(
                "INSERT INTO source_file VALUES ('freesound', ?, 1, 1, ?, 0)", (rel, digest)
            )
    tagged_scanner(conn).scan(source(library))
    read = {r[0] for r in conn.execute("SELECT asset_hash FROM embedded")}
    assert "d1" in read
    assert "d2" not in read
    labels = conn.execute(
        "SELECT name, value FROM tag WHERE asset_hash = 'd1' AND origin = 'embedded'"
    )
    assert [tuple(row) for row in labels] == [("form", "oneshot")]
