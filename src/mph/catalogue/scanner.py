"""Incremental, read-only scan of a local source (REQ-CAT-01, -02, -08).

Source files are only ever ``stat``-ed and opened ``rb``. Assets are keyed by
content hash, so renames and moves keep their identity (and later, their
analysis). A rescan re-hashes only files whose size or mtime changed.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mph.catalogue import db, names
from mph.catalogue.probe import Probe, probe
from mph.config import Source
from mph.proc import Runner

AUDIO_SUFFIXES = frozenset(
    {".wav", ".wave", ".aif", ".aiff", ".aifc", ".flac", ".mp3", ".m4a", ".ogg"}
)
DOC_SUFFIXES = frozenset({".pdf", ".txt", ".doc", ".docx", ".rtf", ".url", ".md"})
JUNK_SUFFIXES = frozenset({".asd", ".reapeaks", ".pkf"})
JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
CHUNK = 1 << 20
PRIORS_KEY = "name_priors_version"


class SourceError(ValueError):
    pass


@dataclass(slots=True)
class ScanStats:
    files_seen: int = 0
    hashed: int = 0
    new_assets: int = 0
    moved: int = 0
    removed: int = 0
    skipped: int = 0
    documents: int = 0
    probe_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Entry:
    rel: str
    size: int
    mtime_ns: int


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def classify(name: str) -> str:
    """'audio', 'document' or 'skip' for a file name."""
    suffix = Path(name).suffix.lower()
    if name in JUNK_NAMES or name.startswith("._") or suffix in JUNK_SUFFIXES:
        return "skip"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return "document" if suffix in DOC_SUFFIXES else "skip"


def walk(root: Path) -> Iterator[tuple[str, str]]:
    """Yield (kind, posix relative path) in a stable order, without following links."""
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            rel = (Path(directory) / name).relative_to(root).as_posix()
            yield classify(name), rel


def blake2b_file(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=32)
    with path.open("rb") as fh:  # read-only by construction (REQ-CAT-01)
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class Scanner:
    def __init__(
        self,
        conn: sqlite3.Connection,
        runner: Runner,
        *,
        workers: int = 4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._conn = conn
        self._runner = runner
        self._workers = workers
        self._clock = clock

    def scan(self, source: Source) -> ScanStats:
        root = source.local_path
        if root is None or not root.is_dir():
            raise SourceError(f"source {source.id}: {source.location} is not a local directory")
        scan_id = self._begin(source)
        stats = ScanStats()
        changed: list[Entry] = []
        for kind, rel in walk(root):
            if kind == "document":
                self._upsert_document(source.id, rel, scan_id)
                stats.documents += 1
            elif kind == "audio":
                stats.files_seen += 1
                entry = self._entry(root, rel)
                if self._unchanged(source.id, entry, scan_id):
                    stats.skipped += 1
                else:
                    changed.append(entry)
            # kind == "skip": junk is ignored entirely (REQ-CAT-08)
        touched = self._link_changed(source, root, changed, scan_id, stats)
        self._sweep(source.id, scan_id, touched, stats)
        self._finish(source.id, scan_id, stats)
        return stats

    # --- phases ---------------------------------------------------------------

    def _begin(self, source: Source) -> int:
        with self._conn:
            self._conn.execute(
                "INSERT INTO source (id, kind, location, licence) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, "
                "location = excluded.location, licence = excluded.licence",
                (source.id, source.kind, source.location, source.license),
            )
            cursor = self._conn.execute(
                "INSERT INTO scan (source_id, started_at) VALUES (?, ?)", (source.id, self._clock())
            )
        return int(cursor.lastrowid or 0)

    @staticmethod
    def _entry(root: Path, rel: str) -> Entry:
        st = (root / rel).stat()
        return Entry(rel, st.st_size, st.st_mtime_ns)

    def _unchanged(self, source_id: str, entry: Entry, scan_id: int) -> bool:
        row = self._conn.execute(
            "SELECT size, mtime_ns FROM source_file WHERE source_id = ? AND rel_path = ?",
            (source_id, entry.rel),
        ).fetchone()
        if row is None or (row["size"], row["mtime_ns"]) != (entry.size, entry.mtime_ns):
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE source_file SET scan_id = ? WHERE source_id = ? AND rel_path = ?",
                (scan_id, source_id, entry.rel),
            )
        return True

    def _link_changed(
        self, source: Source, root: Path, changed: list[Entry], scan_id: int, stats: ScanStats
    ) -> set[str]:
        """Hash changed files, probe never-seen content, link paths. Returns touched hashes."""
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            hashes = list(pool.map(lambda e: blake2b_file(root / e.rel), changed))
            stats.hashed = len(hashes)
            known = self._known_hashes(set(hashes))
            new = sorted(
                {h: e for h, e in zip(hashes, changed, strict=True) if h not in known}.items()
            )
            probes = list(pool.map(lambda item: probe(self._runner, root / item[1].rel), new))
        with self._conn:
            for (digest, entry), result in zip(new, probes, strict=True):
                self._insert_asset(digest, entry.size, result)
                if result.error:
                    stats.probe_errors.append(f"{entry.rel}: {result.error}")
            stats.new_assets = len(new)
            for digest, entry in zip(hashes, changed, strict=True):
                self._conn.execute(
                    "INSERT INTO source_file (source_id, rel_path, size, mtime_ns, asset_hash, "
                    "scan_id) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_id, rel_path) DO UPDATE SET "
                    "size = excluded.size, mtime_ns = excluded.mtime_ns, "
                    "asset_hash = excluded.asset_hash, scan_id = excluded.scan_id",
                    (source.id, entry.rel, entry.size, entry.mtime_ns, digest, scan_id),
                )
        return set(hashes)

    def _sweep(self, source_id: str, scan_id: int, touched: set[str], stats: ScanStats) -> None:
        """Drop paths not seen this scan; a vanished path whose content reappeared is a move."""
        stale = self._conn.execute(
            "SELECT rel_path, asset_hash FROM source_file WHERE source_id = ? AND scan_id != ?",
            (source_id, scan_id),
        ).fetchall()
        stale_hashes = {row["asset_hash"] for row in stale}
        stats.moved = len(stale_hashes & touched)
        stats.removed = len(stale) - stats.moved
        with self._conn:
            self._conn.execute(
                "DELETE FROM source_file WHERE source_id = ? AND scan_id != ?", (source_id, scan_id)
            )
            self._conn.execute(
                "DELETE FROM document WHERE source_id = ? AND scan_id != ?", (source_id, scan_id)
            )
            self._conn.execute(
                "DELETE FROM asset WHERE origin = 'source' AND hash NOT IN "
                "(SELECT asset_hash FROM source_file)"
            )
            stale_rules = db.get_meta(self._conn, PRIORS_KEY) != str(names.VERSION)
            to_retag = self._all_hashes() if stale_rules else touched | stale_hashes
            for digest in sorted(to_retag):
                self._retag(digest)
        db.set_meta(self._conn, PRIORS_KEY, str(names.VERSION))

    def _finish(self, source_id: str, scan_id: int, stats: ScanStats) -> None:
        now = self._clock()
        with self._conn:
            self._conn.execute(
                "UPDATE scan SET finished_at = ?, files_seen = ?, hashed = ?, new_assets = ?, "
                "moved = ?, removed = ?, skipped = ? WHERE id = ?",
                (
                    now,
                    stats.files_seen,
                    stats.hashed,
                    stats.new_assets,
                    stats.moved,
                    stats.removed,
                    stats.skipped,
                    scan_id,
                ),
            )
            self._conn.execute("UPDATE source SET last_scan_at = ? WHERE id = ?", (now, source_id))

    # --- helpers --------------------------------------------------------------

    def _all_hashes(self) -> set[str]:
        return {row[0] for row in self._conn.execute("SELECT hash FROM asset")}

    def _known_hashes(self, hashes: set[str]) -> set[str]:
        query = "SELECT 1 FROM asset WHERE hash = ?"
        return {h for h in hashes if self._conn.execute(query, (h,)).fetchone()}

    def _insert_asset(self, digest: str, size: int, result: Probe) -> None:
        self._conn.execute(
            "INSERT INTO asset (hash, bytes, container, codec, sample_rate, bit_depth, channels, "
            "duration_s, origin, probe_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'source', ?)",
            (
                digest,
                size,
                result.container,
                result.codec,
                result.sample_rate,
                result.bit_depth,
                result.channels,
                result.duration_s,
                result.error,
            ),
        )

    def _upsert_document(self, source_id: str, rel: str, scan_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO document (source_id, rel_path, kind, scan_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (source_id, rel_path) DO UPDATE SET scan_id = excluded.scan_id",
                (source_id, rel, Path(rel).suffix.lower().lstrip("."), scan_id),
            )

    def _retag(self, digest: str) -> None:
        """Rebuild name-derived priors from every path the asset currently lives at."""
        self._conn.execute(
            "DELETE FROM tag WHERE asset_hash = ? AND origin IN ('folder', 'filename')", (digest,)
        )
        paths = self._conn.execute(
            "SELECT rel_path FROM source_file WHERE asset_hash = ?", (digest,)
        ).fetchall()
        for row in paths:
            for prior in names.priors_for(row["rel_path"]):
                self._conn.execute(
                    "INSERT OR IGNORE INTO tag (asset_hash, name, value, origin, confidence) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (digest, prior.name, prior.value, prior.origin, prior.confidence),
                )
