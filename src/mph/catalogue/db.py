"""Catalogue database: one SQLite file in data_dir (ADR-0004).

Schema changes are append-only migrations tracked with ``PRAGMA user_version``,
so an old catalogue upgrades in place and a fresh one is built from scratch
by the same code path. Each migration runs in one transaction.
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
    # 7: Dean's verdicts on benchmark renders, tied to the exact render (REQ-FBK-02).
    """
    CREATE TABLE verdict (
        id INTEGER PRIMARY KEY,
        benchmark TEXT NOT NULL,             -- BM-DRUM-02, ...
        score_hash TEXT NOT NULL,            -- the render folder it judged
        verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'close', 'fail')),
        notes TEXT NOT NULL,                 -- free text, may hold times like "0:42 ..."
        created_at TEXT NOT NULL
    );
    CREATE INDEX verdict_render ON verdict (benchmark, score_hash);
    """,
    # 8: a verdict is for one take, or one of a set given to the whole benchmark (REQ-FBK-02).
    """
    ALTER TABLE verdict ADD COLUMN scope TEXT NOT NULL DEFAULT 'take'
        CHECK (scope IN ('take', 'all'));
    """,
    # 9: star ratings replace verdicts (REQ-FBK-02, REQ-FBK-06, ADR-0012). Every save is a
    # row; the newest stands. Verdicts carry over once (REQ-FBK-07); the verdict table stays
    # as it was, for branches from before ratings. Ideas Dean adds on the page (REQ-FBK-08).
    """
    CREATE TABLE rating (
        id INTEGER PRIMARY KEY,
        benchmark TEXT NOT NULL,             -- BM-DRUM-02, ...
        score_hash TEXT NOT NULL,            -- the render it judged
        scope TEXT NOT NULL CHECK (scope IN ('take', 'all')),
        stars INTEGER NOT NULL CHECK (stars BETWEEN 0 AND 5),  -- 3 to 5 is a pass
        status TEXT CHECK (status IN ('resolved', 'unresolved')),  -- NULL: the stars decide
        notes TEXT NOT NULL,                 -- Dean's feedback, may hold times like "0:42 ..."
        created_at TEXT NOT NULL,
        take TEXT NOT NULL DEFAULT '',       -- the take that render was ('' when carried over)
        inputs TEXT NOT NULL DEFAULT '',     -- what shaped that render (REQ-BEN-14)
        via TEXT NOT NULL CHECK (via IN ('page', 'verdict')),  -- given on the page, or carried over
        verdict TEXT CHECK (verdict IN ('pass', 'close', 'fail'))  -- the verdict it came from
    );
    CREATE INDEX rating_render ON rating (benchmark, score_hash);
    INSERT INTO rating
        (benchmark, score_hash, scope, stars, status, notes, created_at, via, verdict)
    SELECT benchmark, score_hash, scope,
           CASE verdict WHEN 'pass' THEN 3 WHEN 'close' THEN 2 ELSE 1 END,
           CASE WHEN verdict = 'pass' AND trim(notes) != '' THEN 'unresolved' END,
           notes, created_at, 'verdict', verdict
    FROM verdict ORDER BY id;
    CREATE TABLE idea (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('project', 'benchmark', 'variant')),
        ref TEXT NOT NULL,                   -- its id: a project id, BM-AREA-NN or a variant name
        parent TEXT NOT NULL,                -- '' for a project; a project id; a benchmark id
        title TEXT NOT NULL,
        notes TEXT NOT NULL,                 -- what Dean wants, and why
        ref_given INTEGER NOT NULL CHECK (ref_given IN (0, 1)),  -- 1: typed; 0: filled in
        created_at TEXT NOT NULL,
        dropped_at TEXT,                     -- when Dean dropped it; NULL while it's open
        dropped_with INTEGER                 -- the idea whose drop took it (itself, or above it)
    );
    CREATE UNIQUE INDEX idea_open ON idea (kind, parent, ref) WHERE dropped_at IS NULL;
    CREATE UNIQUE INDEX idea_benchmark ON idea (ref)
        WHERE kind = 'benchmark' AND dropped_at IS NULL;
    """,
    # 10: what files say about themselves (REQ-CAT-12): their tags and loop and sampler
    # chunks, as read by one version of the reader. Labels drawn from it have origin
    # 'embedded', which tag's CHECK must allow, so tag is copied into a new table.
    """
    CREATE TABLE tag_v10 (
        asset_hash TEXT NOT NULL REFERENCES asset (hash) ON DELETE CASCADE,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        origin TEXT NOT NULL
            CHECK (origin IN ('folder', 'filename', 'embedded', 'analysis', 'human')),
        confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        PRIMARY KEY (asset_hash, name, value, origin)
    );
    INSERT INTO tag_v10 (asset_hash, name, value, origin, confidence)
        SELECT asset_hash, name, value, origin, confidence FROM tag;
    DROP TABLE tag;
    ALTER TABLE tag_v10 RENAME TO tag;
    CREATE INDEX tag_lookup ON tag (name, value);
    CREATE TABLE embedded (
        asset_hash TEXT PRIMARY KEY REFERENCES asset (hash) ON DELETE CASCADE,
        version INTEGER NOT NULL,            -- the reader that read it (embedded.VERSION)
        read_at TEXT NOT NULL,
        found_json TEXT NOT NULL,            -- tags; acid, smpl, inst, basc; picture; skipped
        error TEXT                           -- why the file couldn't be read, if it couldn't
    );
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the catalogue at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> None:
    """Run each missing migration in one transaction with its version stamp, so a
    migration that fails half way (migration 10 drops and rebuilds ``tag``) leaves
    the catalogue as it was."""
    for version in range(schema_version(conn), len(MIGRATIONS)):
        try:
            conn.executescript(
                f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
