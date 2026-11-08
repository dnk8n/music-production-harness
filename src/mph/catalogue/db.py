"""Catalogue database: one SQLite file in data_dir (ADR-0004).

Schema changes are append-only migrations tracked with ``PRAGMA user_version``,
so an old catalogue upgrades in place and a fresh one is built from scratch
by the same code path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

FILENAME = "catalogue.sqlite"

MIGRATIONS: tuple[str, ...] = (
    # 1: sources, content-addressed assets, where each asset lives, tags, scans
    """
    CREATE TABLE source (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('local', 'remote')),
        location TEXT NOT NULL,
        licence TEXT NOT NULL,
        last_scan_at TEXT
    );
    CREATE TABLE asset (
        hash TEXT PRIMARY KEY,               -- BLAKE2b-256 of the file bytes
        bytes INTEGER NOT NULL,
        container TEXT,                      -- wav, aiff, m4a, ...
        codec TEXT,
        sample_rate INTEGER,
        bit_depth INTEGER,
        channels INTEGER,
        duration_s REAL,
        origin TEXT NOT NULL CHECK (origin IN ('source', 'download', 'derivative')),
        probe_error TEXT
    );
    CREATE TABLE source_file (
        source_id TEXT NOT NULL REFERENCES source (id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        asset_hash TEXT NOT NULL REFERENCES asset (hash),
        scan_id INTEGER NOT NULL,
        PRIMARY KEY (source_id, rel_path)
    );
    CREATE INDEX source_file_asset ON source_file (asset_hash);
    CREATE TABLE document (
        source_id TEXT NOT NULL REFERENCES source (id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        kind TEXT NOT NULL,
        scan_id INTEGER NOT NULL,
        PRIMARY KEY (source_id, rel_path)
    );
    CREATE TABLE tag (
        asset_hash TEXT NOT NULL REFERENCES asset (hash) ON DELETE CASCADE,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        origin TEXT NOT NULL CHECK (origin IN ('folder', 'filename', 'analysis', 'human')),
        confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        PRIMARY KEY (asset_hash, name, value, origin)
    );
    CREATE INDEX tag_lookup ON tag (name, value);
    CREATE TABLE scan (
        id INTEGER PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES source (id) ON DELETE CASCADE,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        files_seen INTEGER NOT NULL DEFAULT 0,
        hashed INTEGER NOT NULL DEFAULT 0,
        new_assets INTEGER NOT NULL DEFAULT 0,
        moved INTEGER NOT NULL DEFAULT 0,
        removed INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0
    );
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the catalogue at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> None:
    for version in range(schema_version(conn), len(MIGRATIONS)):
        with conn:
            conn.executescript(MIGRATIONS[version])
            conn.execute(f"PRAGMA user_version = {version + 1}")
