from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from mph.catalogue import db
from mph.catalogue.scanner import Scanner, SourceError, classify
from mph.config import Source
from mph.proc import Result
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
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.connect(tmp_path / "data" / db.FILENAME)


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
    conn = db.connect(path)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
