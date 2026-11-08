"""Derived audio with recorded recipes, a rebuildable cache, and stashes (REQ-CAT-06).

Everything here writes under ``data_dir`` only:

- ``cache/<hh>/<hash>.wav`` holds derivatives. It can be purged at any time,
  because every file in it can be rebuilt from its recipe and inputs.
- ``stash/<name>/<hash>.wav`` holds derivatives Dean wants to keep. Purging
  the cache never touches it.

Sources are only ever read (REQ-CAT-01). Recipes run through
``mph.proc.Runner`` with ffmpeg's ``-bitexact`` flag, so the same recipe on the
same inputs gives the same bytes and therefore the same hash (REQ-AUD-05).
Outputs are 32-bit float WAV at the input's own rate: an edit, never a
resample (REQ-AUD-01, REQ-AUD-02).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from mph.catalogue.probe import probe
from mph.catalogue.scanner import blake2b_file, utc_now
from mph.proc import Runner

MIN_PREFIX = 6
CACHE = "cache"
STASH = "stash"


class ProvenanceError(ValueError):
    pass


# --- recipes ---------------------------------------------------------------------

ArgvBuilder = Callable[[Mapping[str, float], Sequence[Path], Path], list[str]]


@dataclass(frozen=True, slots=True)
class RecipeKind:
    name: str
    version: int  # bump when the argv changes, so old outputs aren't reused
    params: tuple[str, ...]
    inputs: int
    describe: str
    build: ArgvBuilder


FFMPEG_OUT = ("-map_metadata", "-1", "-c:a", "pcm_f32le", "-bitexact", "-f", "wav")


def _trim(params: Mapping[str, float], inputs: Sequence[Path], output: Path) -> list[str]:
    start, end = params["start"], params["end"]
    if not 0 <= start < end:
        raise ProvenanceError(f"trim needs 0 <= start < end (got {start:g}..{end:g})")
    return [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{end - start:.6f}",
        "-i",
        str(inputs[0]),
        "-map",
        "0:a:0",
        *FFMPEG_OUT,
        str(output),
    ]


RECIPES: dict[str, RecipeKind] = {
    "trim": RecipeKind(
        "trim", 1, ("start", "end"), 1, "cut start..end seconds, native rate, float", _trim
    ),
}


@dataclass(frozen=True, slots=True)
class Recipe:
    kind: RecipeKind
    params: dict[str, float]

    @property
    def canonical(self) -> str:
        body = {"kind": self.kind.name, "version": self.kind.version, "params": self.params}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @property
    def id(self) -> str:
        return hashlib.blake2b(self.canonical.encode(), digest_size=16).hexdigest()


def recipe(kind: str, params: Mapping[str, float]) -> Recipe:
    found = RECIPES.get(kind)
    if found is None:
        raise ProvenanceError(f"unknown recipe {kind!r}; known: {', '.join(sorted(RECIPES))}")
    missing = set(found.params) - set(params)
    extra = set(params) - set(found.params)
    if missing or extra:
        raise ProvenanceError(f"{kind} takes {', '.join(found.params)}")
    return Recipe(found, {name: float(params[name]) for name in found.params})


# --- locating assets ---------------------------------------------------------------


def cache_path(data_dir: Path, digest: str) -> Path:
    return data_dir / CACHE / digest[:2] / f"{digest}.wav"


def stash_path(data_dir: Path, name: str, digest: str) -> Path:
    return data_dir / STASH / name / f"{digest}.wav"


def resolve(conn: sqlite3.Connection, ref: str) -> str:
    """A full hash, a unique hash prefix (6+ characters) or a source-relative path."""
    by_path = conn.execute(
        "SELECT asset_hash FROM source_file WHERE rel_path = ?", (ref,)
    ).fetchone()
    if by_path:
        return str(by_path[0])
    if len(ref) < MIN_PREFIX:
        raise ProvenanceError(f"{ref!r}: use a path or at least {MIN_PREFIX} hash characters")
    rows = conn.execute(
        "SELECT hash FROM asset WHERE hash >= ? AND hash < ? LIMIT 2", (ref, ref + "g")
    ).fetchall()
    if len(rows) != 1:
        raise ProvenanceError(f"{ref!r} matches {'no' if not rows else 'several'} sounds")
    return str(rows[0][0])


def locate(conn: sqlite3.Connection, data_dir: Path, digest: str) -> Path | None:
    """Where an asset's bytes are right now: a local source, a stash, or the cache."""
    row = conn.execute(
        "SELECT s.location, f.rel_path FROM source_file f JOIN source s ON s.id = f.source_id "
        "WHERE f.asset_hash = ? AND s.kind = 'local' ORDER BY f.rel_path LIMIT 1",
        (digest,),
    ).fetchone()
    if row:
        return Path(str(row[0])).expanduser() / str(row[1])
    for (name,) in conn.execute(
        "SELECT name FROM stash WHERE asset_hash = ? ORDER BY name", (digest,)
    ):
        if (path := stash_path(data_dir, name, digest)).is_file():
            return path
    cached = cache_path(data_dir, digest)
    return cached if cached.is_file() else None


# --- deriving ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Derived:
    digest: str
    path: Path
    reused: bool


class Workshop:
    """Runs recipes into the cache and records what made what."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        runner: Runner,
        data_dir: Path,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._conn = conn
        self._runner = runner
        self._data_dir = data_dir
        self._clock = clock

    def derive(self, made_by: Recipe, inputs: Sequence[str]) -> Derived:
        if len(inputs) != made_by.kind.inputs:
            raise ProvenanceError(f"{made_by.kind.name} takes {made_by.kind.inputs} input(s)")
        existing = self._existing(made_by, inputs)
        if existing and (path := locate(self._conn, self._data_dir, existing)):
            return Derived(existing, path, reused=True)
        digest, path = self._run(made_by, inputs)
        self._record(made_by, inputs, digest, path)
        return Derived(digest, path, reused=False)

    def rebuild(self, digest: str) -> Path:
        """Recreate a purged derivative and check it came out byte-identical."""
        row = self._conn.execute(
            "SELECT r.kind, r.params_json, d.inputs_json FROM derivation d "
            "JOIN recipe r ON r.id = d.recipe_id WHERE d.output_hash = ?",
            (digest,),
        ).fetchone()
        if row is None:
            raise ProvenanceError(f"{digest[:12]} is not a derivative")
        if located := locate(self._conn, self._data_dir, digest):
            return located
        inputs = json.loads(row[2])
        for item in inputs:  # inputs may themselves be purged derivatives
            if locate(self._conn, self._data_dir, item) is None:
                self.rebuild(item)
        made, path = self._run(recipe(row[0], json.loads(row[1])), inputs)
        if made != digest:
            path.unlink()
            raise ProvenanceError(
                f"{digest[:12]} rebuilt as {made[:12]}: the tool's output changed "
                "(new ffmpeg?). Bump the recipe version to accept the new bytes."
            )
        return path

    def _existing(self, made_by: Recipe, inputs: Sequence[str]) -> str | None:
        row = self._conn.execute(
            "SELECT output_hash FROM derivation WHERE recipe_id = ? AND inputs_json = ?",
            (made_by.id, json.dumps(list(inputs))),
        ).fetchone()
        return str(row[0]) if row else None

    def _run(self, made_by: Recipe, inputs: Sequence[str]) -> tuple[str, Path]:
        paths = []
        for item in inputs:
            path = locate(self._conn, self._data_dir, item)
            if path is None:
                raise ProvenanceError(f"input {item[:12]} is not available on this machine")
            paths.append(path)
        scratch = self._data_dir / CACHE / f".building-{os.getpid()}.wav"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        result = self._runner.run(made_by.kind.build(made_by.params, paths, scratch))
        if not result.ok or not scratch.is_file():
            scratch.unlink(missing_ok=True)
            reason = result.stderr.strip()[:300] or f"exit {result.code}"
            raise ProvenanceError(f"{made_by.kind.name} failed: {reason}")
        digest = blake2b_file(scratch)
        final = cache_path(self._data_dir, digest)
        final.parent.mkdir(parents=True, exist_ok=True)
        scratch.replace(final)
        return digest, final

    def _record(self, made_by: Recipe, inputs: Sequence[str], digest: str, path: Path) -> None:
        facts = probe(self._runner, path)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO recipe (id, kind, version, params_json) VALUES (?, ?, ?, ?)",
                (made_by.id, made_by.kind.name, made_by.kind.version, json.dumps(made_by.params)),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO asset (hash, bytes, container, codec, sample_rate, "
                "bit_depth, channels, duration_s, origin, probe_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'derivative', ?)",
                (
                    digest,
                    path.stat().st_size,
                    facts.container,
                    facts.codec,
                    facts.sample_rate,
                    facts.bit_depth,
                    facts.channels,
                    facts.duration_s,
                    facts.error,
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO derivation (output_hash, recipe_id, inputs_json, "
                "created_at) VALUES (?, ?, ?, ?)",
                (digest, made_by.id, json.dumps(list(inputs)), self._clock()),
            )


# --- cache and stashes ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefreshReport:
    present: int
    rebuilt: list[str]
    failed: list[str]  # "hash: reason"


def purge_cache(data_dir: Path) -> int:
    """Delete every cached file. Stashes and the catalogue rows stay."""
    root = data_dir / CACHE
    if not root.is_dir():
        return 0
    count = sum(1 for p in root.rglob("*.wav") if p.is_file())
    shutil.rmtree(root)
    return count


def refresh(workshop: Workshop, conn: sqlite3.Connection, data_dir: Path) -> RefreshReport:
    """Rebuild every derivative whose bytes are missing."""
    present, rebuilt, failed = 0, [], []
    for (digest,) in conn.execute(
        "SELECT output_hash FROM derivation ORDER BY created_at, output_hash"
    ).fetchall():
        if locate(conn, data_dir, digest):
            present += 1
            continue
        try:
            workshop.rebuild(digest)
            rebuilt.append(digest)
        except ProvenanceError as exc:
            failed.append(f"{digest[:12]}: {exc}")
    return RefreshReport(present, rebuilt, failed)


def stash(conn: sqlite3.Connection, data_dir: Path, digest: str, name: str) -> Path:
    if not name or "/" in name or name.startswith("."):
        raise ProvenanceError(f"bad stash name {name!r}: use letters, digits, - or _")
    origin = conn.execute("SELECT origin FROM asset WHERE hash = ?", (digest,)).fetchone()
    if origin is None:
        raise ProvenanceError(f"unknown sound {digest[:12]}")
    if origin[0] == "source":
        raise ProvenanceError("source sounds are already permanent; stash derivatives")
    source = locate(conn, data_dir, digest)
    if source is None:
        raise ProvenanceError(f"{digest[:12]} is not on disk: run `mph catalogue refresh`")
    target = stash_path(data_dir, name, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        shutil.copyfile(source, target)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO stash (name, asset_hash, created_at) VALUES (?, ?, ?)",
            (name, digest, utc_now()),
        )
    return target


def lineage(conn: sqlite3.Connection, digest: str) -> list[str]:
    """Human-readable chain from a derivative back to its sources."""
    lines: list[str] = []

    def walk(item: str, depth: int) -> None:
        pad = "  " * depth
        row = conn.execute(
            "SELECT r.kind, r.params_json, d.inputs_json FROM derivation d "
            "JOIN recipe r ON r.id = d.recipe_id WHERE d.output_hash = ?",
            (item,),
        ).fetchone()
        if row is None:
            path = conn.execute(
                "SELECT source_id || ':' || rel_path FROM source_file WHERE asset_hash = ? "
                "ORDER BY rel_path LIMIT 1",
                (item,),
            ).fetchone()
            lines.append(f"{pad}{item[:12]} source {path[0] if path else '(gone)'}")
            return
        params = ", ".join(f"{k}={v:g}" for k, v in json.loads(row[1]).items())
        lines.append(f"{pad}{item[:12]} {row[0]}({params})")
        for parent in json.loads(row[2]):
            walk(parent, depth + 1)

    walk(digest, 0)
    return lines
