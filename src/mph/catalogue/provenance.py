"""Derived audio with recorded recipes, a rebuildable cache, and stashes (REQ-CAT-06).

Everything here writes under ``data_dir`` only:

- ``cache/<hh>/<hash>.wav`` holds derivatives. It can be purged at any time,
  because every file in it can be rebuilt from its recipe and inputs.
- ``stash/<name>/<hash>.wav`` holds derivatives Dean wants to keep. Purging
  the cache never touches it.

Sources are only ever read (REQ-CAT-01). Recipes run through
``mph.proc.Runner`` in a way that makes the same bytes from the same inputs,
and therefore the same hash: ffmpeg with ``-bitexact``, Demucs on the CPU
with no random shifts (REQ-AUD-05). Outputs are 32-bit float WAV at the
input's own rate: an edit or a separation, never a resample (REQ-AUD-01,
REQ-AUD-02).

One run of a recipe can make several sounds, like the four stems of a track
(REQ-CAT-11). Each is recorded with its own recipe, whose ``part`` names it,
so lineage says which stem a sound is, and rebuilding one stem brings back
its siblings too.
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

Param = float | str
Params = Mapping[str, Param]
ArgvBuilder = Callable[[Params, Sequence[Path], Path], list[str]]  # writes into a work folder
OutputFinder = Callable[[Params, Path], dict[str, Path]]  # part -> its file in the work folder
Checker = Callable[[Mapping[str, object]], dict[str, Param]]


@dataclass(frozen=True, slots=True)
class RecipeKind:
    name: str
    version: int  # bump when the argv changes, so old outputs aren't reused
    params: tuple[str, ...]
    inputs: int
    describe: str
    build: ArgvBuilder
    outputs: OutputFinder  # {"": file} for a recipe that makes one sound
    check: Checker  # validated, typed params
    rate: int | None = None  # the one input rate it takes, when it would resample others


FFMPEG_OUT = ("-map_metadata", "-1", "-c:a", "pcm_f32le", "-bitexact", "-f", "wav")
ONE = "out.wav"


def _number(value: object) -> float:
    if isinstance(value, int | float) or (isinstance(value, str) and value.strip()):
        try:
            return float(value)
        except ValueError:
            pass
    raise ProvenanceError(f"not a number: {value!r}")


def _numbers(params: Mapping[str, object]) -> dict[str, Param]:
    return {name: _number(value) for name, value in params.items()}


def _one_output(params: Params, work: Path) -> dict[str, Path]:
    return {"": work / ONE}


def _trim(params: Params, inputs: Sequence[Path], work: Path) -> list[str]:
    start, end = float(params["start"]), float(params["end"])
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
        str(work / ONE),
    ]


# Demucs models and the stems each makes (REQ-CAT-11). All work at 44.1 kHz.
DEMUCS_MODELS: dict[str, tuple[str, ...]] = {
    "htdemucs_ft": ("drums", "bass", "other", "vocals"),  # fine-tuned: best, ~4x slower
    "htdemucs": ("drums", "bass", "other", "vocals"),
    "htdemucs_6s": ("drums", "bass", "other", "vocals", "guitar", "piano"),
}
DEFAULT_MODEL = "htdemucs_ft"
DEMUCS_RATE = 44100


def _stem_params(params: Mapping[str, object]) -> dict[str, Param]:
    model, part = str(params["model"]), str(params["part"])
    parts = DEMUCS_MODELS.get(model)
    if parts is None:
        raise ProvenanceError(f"unknown Demucs model {model!r}; known: {', '.join(DEMUCS_MODELS)}")
    if part not in parts:
        raise ProvenanceError(f"{model} makes {', '.join(parts)}, not {part!r}")
    return {"model": model, "part": part}


def _stems(params: Params, inputs: Sequence[Path], work: Path) -> list[str]:
    """Every stem in one run, on the CPU, without random shifts or rescaling."""
    return [
        "demucs",
        "-n",
        str(params["model"]),
        "-d",
        "cpu",
        "--shifts",
        "0",
        "--overlap",
        "0.25",
        "--float32",
        "--clip-mode",
        "none",
        "-o",
        str(work),
        "--filename",
        "{stem}.{ext}",
        str(inputs[0]),
    ]


def _stem_files(params: Params, work: Path) -> dict[str, Path]:
    model = str(params["model"])  # Demucs writes into a folder named after the model
    return {part: work / model / f"{part}.wav" for part in DEMUCS_MODELS[model]}


RECIPES: dict[str, RecipeKind] = {
    "trim": RecipeKind(
        "trim",
        1,
        ("start", "end"),
        1,
        "cut start..end seconds, native rate, float",
        _trim,
        _one_output,
        _numbers,
    ),
    "stems": RecipeKind(
        "stems",
        1,
        ("model", "part"),
        1,
        "separate with Demucs into drums, bass, other and vocals; 44.1 kHz float",
        _stems,
        _stem_files,
        _stem_params,
        rate=DEMUCS_RATE,
    ),
}


@dataclass(frozen=True, slots=True)
class Recipe:
    kind: RecipeKind
    params: dict[str, Param]

    @property
    def part(self) -> str:
        """Which of the run's sounds this recipe names; "" when it makes one."""
        return str(self.params.get("part", ""))

    def with_part(self, part: str) -> Recipe:
        return Recipe(self.kind, {**self.params, "part": part}) if part else self

    @property
    def canonical(self) -> str:
        body = {"kind": self.kind.name, "version": self.kind.version, "params": self.params}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @property
    def id(self) -> str:
        return hashlib.blake2b(self.canonical.encode(), digest_size=16).hexdigest()


def recipe(kind: str, params: Mapping[str, object]) -> Recipe:
    found = RECIPES.get(kind)
    if found is None:
        raise ProvenanceError(f"unknown recipe {kind!r}; known: {', '.join(sorted(RECIPES))}")
    missing = set(found.params) - set(params)
    extra = set(params) - set(found.params)
    if missing or extra:
        raise ProvenanceError(f"{kind} takes {', '.join(found.params)}")
    return Recipe(found, found.check({name: params[name] for name in found.params}))


def parts(kind: str, params: Mapping[str, object]) -> tuple[str, ...]:
    """The sounds one run of a recipe makes: ("",) for one, a stem per part for stems."""
    if kind == "stems":
        return DEMUCS_MODELS.get(str(params.get("model")), ())
    return ("",)


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
        "WHERE f.asset_hash = ? ORDER BY s.kind = 'local' DESC, f.rel_path LIMIT 1",
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
        made = self._run(made_by, inputs)
        for part, (digest, path) in made.items():  # a run's other sounds are kept too
            self._record(made_by.with_part(part), inputs, digest, path)
        digest, path = made[made_by.part]
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
        made_by = recipe(row[0], json.loads(row[1]))
        made = self._run(made_by, inputs)
        for part, (output, path) in made.items():  # keep only what matches its record
            if self._existing(made_by.with_part(part), inputs) != output:
                path.unlink(missing_ok=True)
        output, path = made[made_by.part]
        if output != digest:
            raise ProvenanceError(
                f"{digest[:12]} rebuilt as {output[:12]}: the tool's output changed "
                f"(a new {made_by.kind.name} tool?). Bump the recipe version to accept "
                "the new bytes."
            )
        return path

    def _existing(self, made_by: Recipe, inputs: Sequence[str]) -> str | None:
        row = self._conn.execute(
            "SELECT output_hash FROM derivation WHERE recipe_id = ? AND inputs_json = ?",
            (made_by.id, json.dumps(list(inputs))),
        ).fetchone()
        return str(row[0]) if row else None

    def _run(self, made_by: Recipe, inputs: Sequence[str]) -> dict[str, tuple[str, Path]]:
        """Run a recipe once and move each sound it made into the cache, by part."""
        paths = [self._input(item, made_by.kind) for item in inputs]
        work = self._data_dir / CACHE / f".building-{os.getpid()}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            result = self._runner.run(made_by.kind.build(made_by.params, paths, work))
            files = made_by.kind.outputs(made_by.params, work)
            missing = [part or "output" for part, path in files.items() if not path.is_file()]
            if not result.ok or missing:
                reason = result.stderr.strip()[-300:] or (
                    f"exit {result.code}" if not result.ok else f"made no {', '.join(missing)}"
                )
                raise ProvenanceError(f"{made_by.kind.name} failed: {reason}")
            return {part: self._keep(path) for part, path in files.items()}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _input(self, item: str, kind: RecipeKind) -> Path:
        path = locate(self._conn, self._data_dir, item)
        if path is None:
            raise ProvenanceError(f"input {item[:12]} is not available on this machine")
        if kind.rate is not None:
            row = self._conn.execute(
                "SELECT sample_rate FROM asset WHERE hash = ?", (item,)
            ).fetchone()
            rate = row[0] if row and row[0] else probe(self._runner, path).sample_rate
            if rate != kind.rate:
                raise ProvenanceError(
                    f"{kind.name} works at {kind.rate} Hz and {item[:12]} is {rate} Hz: "
                    "it would be resampled, so it is refused (REQ-AUD-01)"
                )
        return path

    def _keep(self, path: Path) -> tuple[str, Path]:
        digest = blake2b_file(path)
        final = cache_path(self._data_dir, digest)
        final.parent.mkdir(parents=True, exist_ok=True)
        path.replace(final)
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


@dataclass(frozen=True, slots=True)
class Node:
    """One step in a sound's history: a source file, or a recipe and its inputs."""

    digest: str
    label: str  # "dj:Pack/Bass1.wav" or "trim(start=0, end=2)"
    kind: str  # source | derivative | missing
    inputs: tuple[Node, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "hash": self.digest,
            "label": self.label,
            "kind": self.kind,
            "inputs": [node.to_dict() for node in self.inputs],
        }


def lineage_tree(conn: sqlite3.Connection, digest: str) -> Node:
    row = conn.execute(
        "SELECT r.kind, r.params_json, d.inputs_json FROM derivation d "
        "JOIN recipe r ON r.id = d.recipe_id WHERE d.output_hash = ?",
        (digest,),
    ).fetchone()
    if row is None:
        path = conn.execute(
            "SELECT source_id || ':' || rel_path FROM source_file WHERE asset_hash = ? "
            "ORDER BY rel_path LIMIT 1",
            (digest,),
        ).fetchone()
        return Node(digest, path[0] if path else "(gone)", "source" if path else "missing")
    params = ", ".join(
        f"{k}={v:g}" if isinstance(v, int | float) else f"{k}={v}"
        for k, v in json.loads(row[1]).items()
    )
    parents = tuple(lineage_tree(conn, parent) for parent in json.loads(row[2]))
    return Node(digest, f"{row[0]}({params})", "derivative", parents)


def lineage(conn: sqlite3.Connection, digest: str) -> list[str]:
    """Human-readable chain from a derivative back to its sources."""
    lines: list[str] = []

    def walk(node: Node, depth: int) -> None:
        kind = "source " if node.kind != "derivative" else ""
        lines.append(f"{'  ' * depth}{node.digest[:12]} {kind}{node.label}")
        for parent in node.inputs:
            walk(parent, depth + 1)

    walk(lineage_tree(conn, digest), 0)
    return lines
