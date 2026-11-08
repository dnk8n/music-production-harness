"""Read-only summaries of the catalogue for `mph catalogue stats` and `mph status`."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Each sound counts once, under its most confident role (filename beats folder
# on ties, because a file's own name is the most specific evidence).
BEST_ROLE_COUNTS = """
    SELECT value, COUNT(*) FROM (
        SELECT asset_hash, value, ROW_NUMBER() OVER (
            PARTITION BY asset_hash
            ORDER BY confidence DESC, origin = 'filename' DESC, value) AS rank
        FROM tag WHERE name = 'role')
    WHERE rank = 1 GROUP BY value ORDER BY 2 DESC, 1
"""


@dataclass(frozen=True, slots=True)
class Summary:
    assets: int
    files: int
    documents: int
    gigabytes: float
    hours: float
    probe_errors: int
    by_pack: list[tuple[str, int]]
    by_format: list[tuple[str, int]]
    by_role: list[tuple[str, int]]


def _pairs(conn: sqlite3.Connection, sql: str) -> list[tuple[str, int]]:
    return [(str(row[0]), int(row[1])) for row in conn.execute(sql).fetchall()]


def summarise(conn: sqlite3.Connection) -> Summary:
    totals = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(bytes), 0), COALESCE(SUM(duration_s), 0), "
        "COUNT(probe_error) FROM asset"
    ).fetchone()
    return Summary(
        assets=int(totals[0]),
        files=int(conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0]),
        documents=int(conn.execute("SELECT COUNT(*) FROM document").fetchone()[0]),
        gigabytes=round(totals[1] / 1e9, 2),
        hours=round(totals[2] / 3600, 2),
        probe_errors=int(totals[3]),
        by_pack=_pairs(
            conn,
            "SELECT value, COUNT(DISTINCT asset_hash) FROM tag "
            "WHERE name = 'pack' GROUP BY value ORDER BY 2 DESC, 1",
        ),
        by_format=_pairs(
            conn,
            "SELECT COALESCE(container, '?') || ' ' || "
            "COALESCE(sample_rate / 1000.0, '?') || 'k/' || "
            "COALESCE(bit_depth, '?') || 'b', COUNT(*) FROM asset "
            "GROUP BY 1 ORDER BY 2 DESC, 1",
        ),
        by_role=_pairs(conn, BEST_ROLE_COUNTS),
    )
