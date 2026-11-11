"""Read-only access to the catalogue (REQ-QRY-02, REQ-QRY-04).

``connect`` opens ``catalogue.sqlite`` with SQLite's read-only mode and
``query_only``, so nothing the channel runs can change the file. Unlike
``db.connect`` it never creates the file or runs migrations: a catalogue from
another mph version is refused with what to run instead. Like any reader of a
WAL database, SQLite may leave its index (``-shm``) and an empty ``-wal``
beside the file; the next write tidies them up.

``select`` runs SQL written by an agent. On top of the read-only connection it
installs an authorizer that allows reading only (no writes, ``ATTACH``,
``PRAGMA`` or transactions), and limits the rows returned, the time taken and
the length of any one value.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from mph.catalogue import db
from mph.query import Unanswerable

MAX_SQL_CHARS = 20_000
MAX_VALUE_BYTES = 1_000_000  # longest single string or blob a query may produce
PROGRESS_STEPS = 1_000  # SQLite VM steps between checks of the deadline
READ_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
BLOCKED_FUNCTIONS = frozenset({"load_extension", "fts3_tokenizer"})
READ_ONLY = "only reading is allowed: send one SELECT (or WITH ... SELECT)"


@dataclass(frozen=True, slots=True)
class Rows:
    columns: list[str]
    rows: list[list[object]]
    truncated: bool  # there were more rows than were asked for


def connect(path: Path) -> sqlite3.Connection:
    """The catalogue at ``path``, opened so it can only be read."""
    if not path.is_file():
        raise Unanswerable("no catalogue yet: run `mph catalogue scan` first")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
    try:
        conn.execute("PRAGMA query_only = ON")
        found = db.schema_version(conn)
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise Unanswerable(f"cannot read the catalogue at {path}: {exc}") from exc
    wanted = len(db.MIGRATIONS)
    if found != wanted:
        conn.close()
        raise Unanswerable(_version_problem(found, wanted))
    conn.row_factory = sqlite3.Row
    return conn


def _version_problem(found: int, wanted: int) -> str:
    if found < wanted:
        return (
            f"the catalogue is from an older mph (schema {found}, this one reads {wanted}): "
            "run `uv run mph catalogue stats` once to upgrade it, then ask again"
        )
    return (
        f"the catalogue was made by a newer mph (schema {found}, this one reads {wanted}): "
        "update this checkout (`git pull`) and ask again"
    )


def authorize(
    action: int, arg1: str | None, arg2: str | None, database: str | None, trigger: str | None
) -> int:
    """SQLite authorizer: reading only. Everything else fails to prepare."""
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in READ_ACTIONS else sqlite3.SQLITE_DENY


def select(
    path: Path,
    sql: str,
    *,
    max_rows: int,
    timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> Rows:
    """Run one read-only statement and return at most ``max_rows`` rows."""
    if len(sql) > MAX_SQL_CHARS:
        raise Unanswerable(f"the query is longer than {MAX_SQL_CHARS:,} characters")
    with closing(connect(path)) as conn:
        conn.row_factory = None
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.set_authorizer(authorize)
        deadline = clock() + timeout_s
        conn.set_progress_handler(lambda: clock() > deadline, PROGRESS_STEPS)
        try:
            cursor = conn.execute(sql)
            fetched = cursor.fetchmany(max_rows + 1)
        except (sqlite3.Error, sqlite3.Warning) as exc:
            raise Unanswerable(explain(exc, timeout_s)) from exc
        if cursor.description is None:
            raise Unanswerable(f"that statement returns no rows: {READ_ONLY}")
        columns = [str(d[0]) for d in cursor.description]
    rows = [[_plain(value) for value in row] for row in fetched[:max_rows]]
    return Rows(columns, rows, truncated=len(fetched) > max_rows)


def explain(exc: Exception, timeout_s: float) -> str:
    """SQLite's error, said in a way that tells the caller what to do next."""
    message = str(exc)
    if "not authorized" in message or "authorization denied" in message:
        return f"{READ_ONLY}; writes, PRAGMA, ATTACH and transactions are refused"
    if "interrupted" in message:
        return f"the query ran past {timeout_s:g} s and was stopped: narrow it with WHERE or LIMIT"
    if "one statement at a time" in message:
        return "send one statement at a time"
    if "too big" in message:
        return f"a value in the result is longer than {MAX_VALUE_BYTES:,} bytes"
    if "bindings" in message:
        return "write values into the query itself: parameters (?) aren't supported"
    return f"SQLite says: {message}"


def _plain(value: object) -> object:
    """JSON-ready: text and numbers as they are, blobs by size, infinity as text."""
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # JSON has no infinity
    return value
