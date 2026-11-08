from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mph.catalogue import db, stats


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "c.sqlite")
    yield connection
    connection.close()


def role(conn: sqlite3.Connection, digest: str, value: str, origin: str, conf: float) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO asset (hash, bytes, origin) VALUES (?, 1, 'source')", (digest,)
    )
    conn.execute("INSERT INTO tag VALUES (?, 'role', ?, ?, ?)", (digest, value, origin, conf))


@pytest.mark.req("REQ-CAT-03")
def test_each_sound_counts_once_under_its_best_role(conn: sqlite3.Connection) -> None:
    # "SOUNDS_&_FX/Kick 01.wav": a weak folder hint must not make it an fx sound.
    role(conn, "a", "fx", "folder", 0.25)
    role(conn, "a", "kick", "filename", 0.6)
    role(conn, "b", "bass", "folder", 0.5)
    role(conn, "b", "fx", "filename", 0.5)  # tie: the file's own name wins
    role(conn, "c", "fx", "folder", 0.2)
    assert stats.summarise(conn).by_role == [("fx", 2), ("kick", 1)]
