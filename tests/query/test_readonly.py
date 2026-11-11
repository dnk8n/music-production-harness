from __future__ import annotations

import hashlib
import itertools
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mph.catalogue import db
from mph.query import Unanswerable, readonly


@pytest.fixture
def catalogue(tmp_path: Path) -> Path:
    path = tmp_path / "data" / db.FILENAME
    with closing(db.connect(path)) as conn, conn:
        conn.execute("INSERT INTO meta VALUES ('names_version', '2')")
    return path


def fingerprint(path: Path) -> tuple[str, list[str], int]:
    """What a write would change: the file's bytes, the folder's other files, and
    the write-ahead log. Readers may leave SQLite's index (-shm) and an empty log."""
    digest = hashlib.blake2b(path.read_bytes()).hexdigest()
    others = sorted(p.name for p in path.parent.iterdir() if not p.name.endswith(("-shm", "-wal")))
    wal = path.with_name(path.name + "-wal")
    return digest, others, wal.stat().st_size if wal.exists() else 0


def select(path: Path, sql: str, max_rows: int = 100) -> readonly.Rows:
    return readonly.select(path, sql, max_rows=max_rows, timeout_s=5.0)


@pytest.mark.req("REQ-QRY-02")
@pytest.mark.req("REQ-QRY-03")
def test_a_missing_catalogue_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "data" / db.FILENAME
    with pytest.raises(Unanswerable, match="run `mph catalogue scan` first"):
        readonly.connect(path)
    assert not path.parent.exists()


@pytest.mark.req("REQ-QRY-02")
def test_the_connection_cannot_write(catalogue: Path) -> None:
    before = fingerprint(catalogue)
    with closing(readonly.connect(catalogue)) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO meta VALUES ('k', 'v')")
        conn.execute("PRAGMA query_only = OFF")  # the file is still opened read-only
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM meta")
        assert conn.execute("SELECT value FROM meta").fetchone()["value"] == "2"
    assert fingerprint(catalogue) == before


def at_version(path: Path, version: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        for migration in db.MIGRATIONS[: min(version, len(db.MIGRATIONS))]:
            conn.executescript(migration)
        conn.execute(f"PRAGMA user_version = {version}")
    return path


@pytest.mark.req("REQ-QRY-02")
def test_an_older_catalogue_is_refused_not_upgraded(tmp_path: Path) -> None:
    path = at_version(tmp_path / db.FILENAME, len(db.MIGRATIONS) - 1)
    before = fingerprint(path)
    with pytest.raises(Unanswerable, match=r"older mph.*`uv run mph catalogue stats`"):
        readonly.connect(path)
    assert fingerprint(path) == before


@pytest.mark.req("REQ-QRY-02")
def test_a_newer_catalogue_is_refused(tmp_path: Path) -> None:
    path = at_version(tmp_path / db.FILENAME, len(db.MIGRATIONS) + 1)
    with pytest.raises(Unanswerable, match=r"newer mph.*`git pull`"):
        readonly.connect(path)


@pytest.mark.req("REQ-QRY-06")
def test_a_file_that_is_not_a_catalogue_is_refused(tmp_path: Path) -> None:
    path = tmp_path / db.FILENAME
    path.write_bytes(b"not a database, just bytes " * 100)
    with pytest.raises(Unanswerable, match="cannot read the catalogue"):
        readonly.connect(path)


@pytest.mark.req("REQ-QRY-04")
def test_select_reads_with_json_and_recursion(catalogue: Path) -> None:
    found = select(
        catalogue,
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) "
        "SELECT i, json_extract('{\"a\": 2}', '$.a') AS a FROM n",
    )
    assert found.columns == ["i", "a"]
    assert found.rows == [[1, 2], [2, 2], [3, 2]]
    assert not found.truncated


REFUSED = [
    "INSERT INTO meta VALUES ('a', 'b')",
    "INSERT INTO meta SELECT 'a', 'b'",
    "REPLACE INTO meta VALUES ('a', 'b')",
    "UPDATE meta SET value = 'x'",
    "DELETE FROM meta",
    "WITH x AS (SELECT 1) DELETE FROM meta",
    "DROP TABLE meta",
    "CREATE TABLE x (a)",
    "CREATE TEMP TABLE t (a)",
    "CREATE VIEW v AS SELECT 1",
    "CREATE VIRTUAL TABLE f USING fts5(a)",
    "ALTER TABLE meta ADD COLUMN z",
    "ATTACH DATABASE ':memory:' AS other",
    "DETACH DATABASE main",
    "PRAGMA user_version = 99",
    "PRAGMA table_info(meta)",
    "SELECT * FROM pragma_table_info('meta')",
    "BEGIN",
    "COMMIT",
    "SAVEPOINT a",
    "VACUUM",
    "VACUUM INTO 'copy.sqlite'",
    "REINDEX",
    "ANALYZE",
    "SELECT load_extension('x')",
    "SELECT fts3_tokenizer('simple')",
]


@pytest.mark.req("REQ-QRY-04")
@pytest.mark.parametrize("sql", REFUSED)
def test_select_refuses_everything_but_reading(catalogue: Path, sql: str) -> None:
    before = fingerprint(catalogue)
    with pytest.raises(Unanswerable, match="only reading is allowed"):
        select(catalogue, sql)
    assert fingerprint(catalogue) == before


@pytest.mark.req("REQ-QRY-04")
@pytest.mark.parametrize("sql", ["SELECT 1; SELECT 2", "SELECT 1; DELETE FROM meta"])
def test_select_runs_one_statement(catalogue: Path, sql: str) -> None:
    with pytest.raises(Unanswerable, match="one statement at a time"):
        select(catalogue, sql)
    assert select(catalogue, "SELECT COUNT(*) FROM meta").rows == [[1]]


@pytest.mark.req("REQ-QRY-04")
@pytest.mark.parametrize("sql", ["", ";", "-- only a comment"])
def test_select_needs_a_statement_that_returns_rows(catalogue: Path, sql: str) -> None:
    with pytest.raises(Unanswerable, match="returns no rows"):
        select(catalogue, sql)


@pytest.mark.req("REQ-QRY-04")
def test_select_says_when_rows_were_left_out(catalogue: Path) -> None:
    ten = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 10) "
        "SELECT i FROM n"
    )
    first = select(catalogue, ten, max_rows=3)
    assert (first.rows, first.truncated) == ([[1], [2], [3]], True)
    every = select(catalogue, ten, max_rows=10)
    assert (len(every.rows), every.truncated) == (10, False)


@pytest.mark.req("REQ-QRY-04")
def test_select_stops_at_the_deadline(catalogue: Path) -> None:
    ticks = itertools.count()  # each look at the clock is a second later
    forever = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    )
    with pytest.raises(Unanswerable, match="ran past 5 s and was stopped"):
        readonly.select(
            catalogue, forever, max_rows=1, timeout_s=5.0, clock=lambda: float(next(ticks))
        )
    assert next(ticks) < 20  # stopped soon after the deadline, not at the end of time


@pytest.mark.req("REQ-QRY-04")
def test_select_refuses_values_over_a_megabyte(catalogue: Path) -> None:
    with pytest.raises(Unanswerable, match="longer than 1,000,000 bytes"):
        select(catalogue, "SELECT zeroblob(2000000)")


@pytest.mark.req("REQ-QRY-04")
def test_select_refuses_long_sql(catalogue: Path) -> None:
    with pytest.raises(Unanswerable, match="longer than 20,000 characters"):
        select(catalogue, "SELECT 1" + " " * readonly.MAX_SQL_CHARS)


@pytest.mark.req("REQ-QRY-05")
def test_select_gives_json_ready_values(catalogue: Path) -> None:
    found = select(catalogue, "SELECT x'00ff', 1e999, -1e999, 'text', 1.5, NULL")
    assert found.rows == [["<2 bytes>", "inf", "-inf", "text", 1.5, None]]


@pytest.mark.req("REQ-QRY-06")
@pytest.mark.parametrize(
    ("sql", "says"),
    [
        ("SELECT ?", "parameters (?) aren't supported"),
        ("SELECT nope FROM meta", "SQLite says: no such column: nope"),
        ("SELEC 1", "SQLite says:"),
    ],
)
def test_select_errors_say_what_to_do(catalogue: Path, sql: str, says: str) -> None:
    with pytest.raises(Unanswerable) as caught:
        select(catalogue, sql)
    assert says in str(caught.value)


@pytest.mark.req("REQ-QRY-02")
def test_reading_while_a_scan_is_writing(catalogue: Path) -> None:
    """A scan holds a write transaction for a while; questions still get the
    last committed state, without waiting."""
    with closing(db.connect(catalogue)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO meta VALUES ('half', 'done')")
        assert select(catalogue, "SELECT key FROM meta ORDER BY key").rows == [["names_version"]]
        writer.execute("COMMIT")
    assert select(catalogue, "SELECT COUNT(*) FROM meta").rows == [[2]]
