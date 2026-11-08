from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mph.catalogue import agreement, db


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "c.sqlite")
    yield connection
    connection.close()


def tag(conn: sqlite3.Connection, digest: str, name: str, value: str, origin: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO asset (hash, bytes, origin) VALUES (?, 1, 'source')", (digest,)
    )
    conn.execute("INSERT INTO tag VALUES (?, ?, ?, ?, 0.5)", (digest, name, value, origin))


@pytest.mark.req("REQ-CAT-03")
def test_agreement_counts_tempo_and_key(conn: sqlite3.Connection) -> None:
    tag(conn, "a", "bpm", "131", "folder")
    tag(conn, "a", "bpm", "131.5", "analysis")  # within 2%
    tag(conn, "b", "bpm", "124", "folder")
    tag(conn, "b", "bpm", "62", "analysis")  # half time: disagreement
    tag(conn, "a", "key", "A:minor", "folder")
    tag(conn, "a", "key", "C:major", "analysis")  # relative key: forgiven
    tag(conn, "c", "key", "F:minor", "filename")
    tag(conn, "c", "key", "D:major", "analysis")
    tag(conn, "d", "bpm", "128", "folder")  # never analysed: not compared
    results = {r.field: r for r in agreement.measure(conn, ["a", "b", "c", "d"])}
    assert (results["bpm"].agreed, results["bpm"].compared) == (1, 2)
    assert (results["key"].agreed, results["key"].compared) == (1, 2)
    assert results["key"].rate == 0.5


def test_empty_selection(conn: sqlite3.Connection) -> None:
    assert agreement.measure(conn, []) == []
    assert agreement.Agreement("bpm", 0, 0).rate == 0.0


@pytest.mark.parametrize(
    ("key", "relative"),
    [
        ("A:minor", "C:major"),
        ("C:major", "A:minor"),
        ("F#:minor", "A:major"),
        ("C:minor", "D#:major"),
    ],
)
def test_relative_key(key: str, relative: str) -> None:
    assert agreement.relative_key(key) == relative
