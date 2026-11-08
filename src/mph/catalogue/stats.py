"""Read-only summaries of the catalogue for `mph catalogue stats` and `mph status`."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


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
        by_role=_pairs(
            conn,
            "SELECT value, COUNT(DISTINCT asset_hash) FROM tag "
            "WHERE name = 'role' GROUP BY value ORDER BY 2 DESC, 1",
        ),
    )
