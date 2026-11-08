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
    # 2: cached, versioned analysis results (REQ-CAT-04)
    """
    CREATE TABLE analysis (
        asset_hash TEXT NOT NULL REFERENCES asset (hash) ON DELETE CASCADE,
        analyser TEXT NOT NULL,
        version INTEGER NOT NULL,
        analysed_at TEXT NOT NULL,
        result_json TEXT NOT NULL,           -- feature name -> value
        error TEXT,
        PRIMARY KEY (asset_hash, analyser, version)
    );
    """,
    # 3: small key/value store for catalogue-wide state (e.g. which name-parser version
    # produced the current tags)
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 4: early scans labelled M4A files "mov" (ffprobe's first alias for the family);
    # rescans skip unchanged files, so relabel them here.
    """
    UPDATE asset SET container = 'm4a' WHERE container = 'mov';
    """,
    # 5: provenance (REQ-CAT-06). A recipe is a named, versioned transform with fixed
    # parameters; a derivation says which recipe turned which inputs into an asset.
    # Stashed assets are kept outside the cache and survive purges.
    """
    CREATE TABLE recipe (
        id TEXT PRIMARY KEY,                 -- BLAKE2b of the canonical recipe JSON
        kind TEXT NOT NULL,                  -- trim, ...
        version INTEGER NOT NULL,
        params_json TEXT NOT NULL
    );
    CREATE TABLE derivation (
        output_hash TEXT PRIMARY KEY REFERENCES asset (hash) ON DELETE CASCADE,
        recipe_id TEXT NOT NULL REFERENCES recipe (id),
        inputs_json TEXT NOT NULL,           -- ordered list of input asset hashes
        created_at TEXT NOT NULL
    );
    CREATE INDEX derivation_recipe ON derivation (recipe_id, inputs_json);
    CREATE TABLE stash (
        name TEXT NOT NULL,
        asset_hash TEXT NOT NULL REFERENCES asset (hash) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (name, asset_hash)
    );
    """,
    # 6: where downloaded sounds came from and under what licence (REQ-CAT-07). The
    # file itself is recorded like any other in source_file, under a 'remote' source
    # whose location is its folder in data_dir/downloads.
    """
    CREATE TABLE download (
        source_id TEXT NOT NULL REFERENCES source (id) ON DELETE CASCADE,
        remote_id TEXT NOT NULL,             -- Freesound sound id, archive.org item/file
        asset_hash TEXT NOT NULL REFERENCES asset (hash) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,              -- under the source's download folder
        url TEXT NOT NULL,                   -- where the bytes came from (for refresh)
        page_url TEXT NOT NULL,              -- the human page, for attribution
        title TEXT NOT NULL,
        creator TEXT NOT NULL,
        licence TEXT NOT NULL,               -- short form: cc0, cc-by, public-domain, ...
        licence_url TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (source_id, remote_id)
    );
    CREATE INDEX download_asset ON download (asset_hash);
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


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
