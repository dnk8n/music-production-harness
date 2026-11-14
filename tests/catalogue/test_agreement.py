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


def tag(
    conn: sqlite3.Connection,
    digest: str,
    name: str,
    value: str,
    origin: str,
    *,
    confidence: float = 0.5,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO asset (hash, bytes, origin) VALUES (?, 1, 'source')", (digest,)
    )
    conn.execute(
        "INSERT INTO tag VALUES (?, ?, ?, ?, ?)", (digest, name, value, origin, confidence)
    )


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
    assert results["key or a neighbouring key"].agreed == 1


@pytest.mark.req("REQ-CAT-03")
def test_strongest_prior_is_compared(conn: sqlite3.Connection) -> None:
    # "Horror Trap (Fm, 146bpm)/.../Lost Synth_..._Cm": the file name wins.
    tag(conn, "a", "key", "F:minor", "folder", confidence=0.4)
    tag(conn, "a", "key", "C:minor", "filename", confidence=0.6)
    tag(conn, "a", "key", "C:minor", "analysis")
    results = {r.field: r for r in agreement.measure(conn, ["a"])}
    assert (results["key"].agreed, results["key"].compared) == (1, 1)


@pytest.mark.req("REQ-CAT-03")
def test_neighbouring_keys_counted_separately(conn: sqlite3.Connection) -> None:
    tag(conn, "a", "key", "D:minor", "folder")
    tag(conn, "a", "key", "G:minor", "analysis")  # one step round the circle of fifths
    tag(conn, "b", "key", "D:minor", "folder")
    tag(conn, "b", "key", "E:major", "analysis")  # far away
    results = {r.field: r for r in agreement.measure(conn, ["a", "b"])}
    assert results["key"].agreed == 0
    assert (results["key or a neighbouring key"].agreed, results["key"].compared) == (1, 2)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("A:minor", "C:major", True),
        ("C:major", "G:major", True),
        ("C:major", "F:major", True),
        ("C:major", "D:major", False),
        ("D:minor", "D:major", False),
    ],
)
def test_compatible(a: str, b: str, expected: bool) -> None:
    assert agreement.compatible(a, b) is expected


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


@pytest.mark.req("REQ-CAT-12")
def test_the_files_own_labels_are_measured_apart_from_names(conn: sqlite3.Connection) -> None:
    # The pack names the loop well, but the file's BPM tag is wrong.
    tag(conn, "a", "bpm", "128", "filename", confidence=0.6)
    tag(conn, "a", "bpm", "120", "embedded", confidence=0.35)
    tag(conn, "a", "bpm", "128.2", "analysis")
    tag(conn, "b", "key", "A:minor", "embedded", confidence=0.35)
    tag(conn, "b", "key", "C:major", "analysis")  # the relative key: forgiven
    names = {r.field: r for r in agreement.measure(conn, ["a", "b"])}
    own = {r.field: r for r in agreement.measure(conn, ["a", "b"], agreement.OWN)}
    assert (names["bpm"].agreed, names["bpm"].compared, names["key"].compared) == (1, 1, 0)
    assert (own["bpm"].agreed, own["bpm"].compared) == (0, 1)
    assert (own["key"].agreed, own["key"].compared) == (1, 1)
