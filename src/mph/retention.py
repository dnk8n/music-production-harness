"""Keeping only what is fresh, and freeing the rest (spec/components/retention.md).

Everything under ``data_dir`` that mph can make again is fair game; nothing
else is. ``plan`` works out what could go and why, without touching a file,
so ``mph prune`` can show it first. ``apply`` deletes exactly what a plan
lists, and only inside the kinds of folder this module owns:

- ``renders/<id>/<hash>/``: a superseded render keeps only ``report.json``
  (REQ-RET-02); a fresh one loses only its ``work/`` folder (REQ-RET-03).
- ``renders/<id>/<anything else>``: a render that never finished.
- ``cache/<hh>/<hash>.wav``: a derivative no fresh render uses (REQ-RET-04).
- ``renoise/jobs/*``: job files the bridge left behind.

Sources, stashes, downloads, the catalogue DB and synthdefs are never listed.
Anything changed in the last hour is left alone, so a render in progress is safe.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mph.config import Retention

RENDERS = "renders"
CACHE = "cache"
JOBS = Path("renoise") / "jobs"
REPORT = "report.json"
WORK = "work"
MAIN = "main"  # the variant of reports written before variants existed
SETTLE_S = 3600.0  # leave anything younger than this alone (a render may be running)

SUPERSEDED = "superseded render"
WORK_FILES = "render work files"
UNFINISHED = "unfinished render"
CACHED = "unused cached derivative"
JOB_FILES = "leftover Renoise job"
KINDS = (SUPERSEDED, WORK_FILES, UNFINISHED, CACHED, JOB_FILES)


@dataclass(frozen=True, slots=True)
class Item:
    """One thing to delete. For a superseded render, ``path`` is the render
    folder and everything in it but ``report.json`` goes."""

    path: Path
    kind: str
    size: int
    why: str


@dataclass(slots=True)
class Plan:
    items: list[Item] = field(default_factory=list)
    kept_renders: int = 0
    renders_size: int = 0  # everything under renders/ now

    @property
    def size(self) -> int:
        return sum(i.size for i in self.items)

    def by_kind(self) -> list[tuple[str, int, int]]:
        """(kind, count, bytes) for every kind with something in it."""
        out = []
        for kind in KINDS:
            chosen = [i for i in self.items if i.kind == kind]
            if chosen:
                out.append((kind, len(chosen), sum(i.size for i in chosen)))
        return out


@dataclass(frozen=True, slots=True)
class Render:
    folder: Path
    benchmark: str
    variant: str
    rendered_at: str
    kit: frozenset[str]


# --- measuring ----------------------------------------------------------------------


def size_of(path: Path) -> int:
    """Bytes on disk under ``path``, not following links."""
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    if not path.is_dir():
        return 0
    return sum(p.lstat().st_size for p in path.rglob("*") if p.is_symlink() or p.is_file())


def _newest_change(path: Path) -> float:
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_mtime
    times = [path.lstat().st_mtime, *(p.lstat().st_mtime for p in path.rglob("*"))]
    return max(times)


UNITS = ((1e9, "GB"), (1e6, "MB"), (1e3, "KB"))


def human(size: int) -> str:
    """Decimal units, as macOS Finder shows them."""
    for scale, unit in UNITS:
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} B"


# --- what is fresh ------------------------------------------------------------------


def _read_report(folder: Path) -> dict[str, Any] | None:
    try:
        found = json.loads((folder / REPORT).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def renders(data_dir: Path) -> tuple[list[Render], list[Path]]:
    """Finished renders (with a report), and folders that never finished."""
    found, unfinished = [], []
    root = data_dir / RENDERS
    for bench in sorted(p for p in root.glob("*") if p.is_dir() and not p.is_symlink()):
        for folder in sorted(p for p in bench.iterdir() if not p.is_symlink()):
            report = _read_report(folder) if folder.is_dir() else None
            if report is None:
                unfinished.append(folder)
                continue
            kit = frozenset(str(k["hash"]) for k in report.get("kit", []) if k.get("hash"))
            found.append(
                Render(
                    folder,
                    bench.name,
                    str(report.get("variant") or MAIN),
                    str(report.get("rendered_at", "")),
                    kit,
                )
            )
    return found, unfinished


def split_fresh(found: Iterable[Render], keep: int) -> tuple[list[Render], list[Render]]:
    """REQ-RET-01: the newest ``keep`` renders of each benchmark variant are fresh."""
    groups: dict[tuple[str, str], list[Render]] = {}
    for render in found:
        groups.setdefault((render.benchmark, render.variant), []).append(render)
    fresh, stale = [], []
    for group in groups.values():
        group.sort(key=lambda r: (r.rendered_at, r.folder.name), reverse=True)
        fresh += group[:keep]
        stale += group[keep:]
    return fresh, stale


def _audio(folder: Path) -> Iterator[Path]:
    """Everything in a render folder except its report."""
    return (p for p in folder.iterdir() if p.name != REPORT)


# --- the plan -----------------------------------------------------------------------


def plan(
    data_dir: Path,
    policy: Retention,
    *,
    keep: int | None = None,
    now: Callable[[], float] = time.time,
    only: tuple[str, str] | None = None,
) -> Plan:
    """What pruning would delete. ``only`` limits it to one benchmark variant's
    renders (used straight after a render); caches and jobs are then left alone."""
    keep = policy.keep_renders if keep is None else keep
    settled = now() - SETTLE_S

    def old(path: Path) -> bool:
        return _newest_change(path) < settled

    found, unfinished = renders(data_dir)
    if only is not None:
        found = [r for r in found if (r.benchmark, r.variant) == only]
        unfinished = []
    fresh, stale = split_fresh(found, keep)
    result = Plan(kept_renders=len(fresh), renders_size=size_of(data_dir / RENDERS))
    result.items += _superseded_items(stale, old)
    if not policy.keep_work:
        result.items += _work_items(fresh, old)
    result.items += [
        Item(f, UNFINISHED, size_of(f), "no report.json: the render stopped before it finished")
        for f in unfinished
        if old(f)
    ]
    if only is None:
        used = frozenset().union(*(r.kit for r in fresh))
        result.items += _cache_items(data_dir, used, old)
        result.items += _job_items(data_dir, old)
    return result


def _superseded_items(stale: Iterable[Render], old: Callable[[Path], bool]) -> list[Item]:
    items = []
    for render in stale:
        size = sum(size_of(p) for p in _audio(render.folder))
        if size and old(render.folder):
            why = f"{render.benchmark} {render.variant}: a newer render replaces it"
            items.append(Item(render.folder, SUPERSEDED, size, why))
    return items


def _work_items(fresh: Iterable[Render], old: Callable[[Path], bool]) -> list[Item]:
    items = []
    for render in fresh:
        work = render.folder / WORK
        if work.is_dir() and not work.is_symlink() and old(work):
            why = f"{render.benchmark} {render.variant}: only needed while rendering"
            items.append(Item(work, WORK_FILES, size_of(work), why))
    return items


def _cache_items(data_dir: Path, used: frozenset[str], old: Callable[[Path], bool]) -> list[Item]:
    """Derivatives can be rebuilt from their recipe (REQ-CAT-06); stashes live elsewhere."""
    items = []
    for path in sorted((data_dir / CACHE).glob("*/*.wav")):
        if path.is_symlink() or not path.is_file() or path.stem in used or not old(path):
            continue
        why = "no fresh render uses it; `mph catalogue refresh` rebuilds it"
        items.append(Item(path, CACHED, size_of(path), why))
    return items


def _job_items(data_dir: Path, old: Callable[[Path], bool]) -> list[Item]:
    items = []
    for path in sorted((data_dir / JOBS).glob("*")):
        if path.is_file() and not path.is_symlink() and old(path):
            items.append(Item(path, JOB_FILES, size_of(path), "the job it belonged to is over"))
    return items


# --- deleting -----------------------------------------------------------------------


class PruneError(RuntimeError):
    pass


def _owned(data_dir: Path, path: Path) -> bool:
    """Only paths inside the folders this module owns, reached without links."""
    roots = [data_dir / RENDERS, data_dir / CACHE, data_dir / JOBS]
    if path.is_symlink():
        return path.parent.resolve().is_relative_to(data_dir.resolve())
    real = path.resolve()
    return any(real.is_relative_to(root.resolve()) and real != root.resolve() for root in roots)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def apply(data_dir: Path, chosen: Plan) -> int:
    """Delete what the plan lists; returns the bytes freed."""
    freed = 0
    for item in chosen.items:
        if not _owned(data_dir, item.path):
            raise PruneError(f"refusing to delete {item.path}: it isn't mph's to delete")
        if item.kind == SUPERSEDED:
            for part in _audio(item.path):
                _remove(part)
        else:
            _remove(item.path)
        freed += item.size
    return freed


def after_render(data_dir: Path, folder: Path, policy: Retention) -> int:
    """REQ-RET-03 and the automatic part of REQ-RET-01, run as a render finishes.
    Its own folder is always kept, whatever ``keep_renders`` says."""
    freed = 0
    work = folder / WORK
    if not policy.keep_work and work.is_dir() and not work.is_symlink():
        freed += size_of(work)
        shutil.rmtree(work)
    if policy.prune_after_render:
        report = _read_report(folder) or {}
        only = (folder.parent.name, str(report.get("variant") or MAIN))
        keep = max(policy.keep_renders, 1)
        chosen = plan(data_dir, policy, keep=keep, only=only, now=lambda: float("inf"))
        chosen.items = [i for i in chosen.items if i.path != folder and i.kind == SUPERSEDED]
        freed += apply(data_dir, chosen)
    return freed
