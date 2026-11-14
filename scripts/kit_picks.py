"""The sounds each benchmark take would pick, for scripts/kit-diff (REQ-BEN-05).

    python scripts/kit_picks.py COPY OUT       picks and labels, as this checkout makes them
    python scripts/kit_picks.py --compare A B  what differs between two such files

The first form copies the catalogue to COPY and scans the copy with this
checkout's naming rules and readers, so the copy labels sounds the way this
checkout would. Then it writes, as JSON in OUT, the sound every voice of
every take would pick (and the tempo a break would be sliced by), every
sound's roles, and every sound's tempo, key and form as search sees them,
from names, what files say about themselves (REQ-CAT-12) and analysis.
Dean's catalogue, sources and renders are only read.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from mph import config, proc
from mph.bench import kit, score
from mph.catalogue import db, names, search
from mph.catalogue.scanner import Scanner

EXAMPLES = 3  # sounds named for each group of changes
FIELDS = ("bpm", "key", "form")  # what search and the kit read besides role


def copy_catalogue(real: Path, copy: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{real}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(copy)) as target,
    ):
        source.backup(target)


def picks(conn: sqlite3.Connection, data_dir: Path) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for benchmark in score.available():
        loaded = score.load(benchmark)
        for variant in score.all_variants(loaded):
            take, _ = score.variant_of(loaded, variant.name)
            try:
                chosen = kit.choose(take, conn, data_dir)
            except kit.KitError as exc:
                found[f"{benchmark} {variant.name}"] = {"(kit)": f"fails: {exc}"}
                continue
            found[f"{benchmark} {variant.name}"] = {c.voice: pick(c) for c in chosen}
    return found


def pick(chosen: kit.Choice) -> str:
    """A sample by path and hash, and a break with the tempo it is sliced by."""
    if not chosen.digest:
        return chosen.label
    tempo = f" at {chosen.bpm:g} BPM" if chosen.bpm else ""
    return f"{chosen.label} ({chosen.digest[:12]}){tempo}"


def roles(conn: sqlite3.Connection) -> dict[str, list[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for digest, value in conn.execute("SELECT asset_hash, value FROM tag WHERE name = 'role'"):
        found[digest].add(value)
    return {digest: sorted(values) for digest, values in found.items()}


def labels(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Each sound's tempo, key and form, the way search weighs its labels."""
    found = {}
    for digest, item in search.load(conn).items():
        if facts := {k: v for k, v in search.facts(item).items() if k in FIELDS}:
            found[digest] = facts
    return found


def reader() -> int | None:
    """The version of the reader of what files say about themselves
    (REQ-CAT-12); None in a checkout from before there was one."""
    try:
        from mph.catalogue import embedded  # noqa: PLC0415 (not in older checkouts)
    except ImportError:
        return None
    return embedded.VERSION


def paths(conn: sqlite3.Connection) -> dict[str, str]:
    """Each sound as people know it: its first path in a sample folder, else
    its download as downloads/SOURCE/TITLE (its own path there is a remote id)."""
    found = dict(
        conn.execute(
            "SELECT f.asset_hash, min(f.rel_path) FROM source_file f "
            "JOIN source s ON s.id = f.source_id WHERE s.kind = 'local' GROUP BY f.asset_hash"
        )
    )
    for digest, source, title in conn.execute(
        "SELECT asset_hash, source_id, title FROM download ORDER BY source_id, remote_id"
    ):
        found.setdefault(digest, f"downloads/{source}/{title}")
    return found


def write(copy: Path, out: Path) -> None:
    cfg = config.load()
    copy_catalogue(cfg.data_dir / db.FILENAME, copy)
    with closing(db.connect(copy)) as conn:
        scanner = Scanner(conn, proc.SubprocessRunner())
        for source in cfg.sources:
            if source.kind == "local":
                scanner.scan(source)  # labels the copy with this checkout's rules
        found = {
            "names_version": names.VERSION,
            "reader_version": reader(),
            "picks": picks(conn, cfg.data_dir),
            "roles": roles(conn),
            "labels": labels(conn),
            "paths": paths(conn),
        }
    out.write_text(json.dumps(found, indent=1, sort_keys=True), "utf-8")


def changed_picks(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for take in sorted(set(before["picks"]) | set(after["picks"])):
        old, new = before["picks"].get(take, {}), after["picks"].get(take, {})
        voices = sorted(v for v in set(old) | set(new) if old.get(v) != new.get(v))
        if voices:
            found[take] = [f"{v}: {old.get(v, '-')}  ->  {new.get(v, '-')}" for v in voices]
    return found


def top(path: str) -> str:
    """A sound's top two folders; a sound known only by its hash has none."""
    return "/".join(PurePosixPath(path).parent.parts[:2]) or path


def biggest_first(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    return dict(sorted(groups.items(), key=lambda g: (-len(g[1]), g[0])))


def changed_roles(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Paths of sounds whose roles change, by change and top two folders."""
    groups: dict[str, list[str]] = defaultdict(list)
    where = {**before["paths"], **after["paths"]}
    for digest in sorted(set(before["roles"]) | set(after["roles"])):
        old, new = set(before["roles"].get(digest, ())), set(after["roles"].get(digest, ()))
        path = where.get(digest, digest)
        for sign, roles_ in (("-", old - new), ("+", new - old)):
            for role in sorted(roles_):
                groups[f"{sign} {role:10} {top(path)}"].append(path)
    return biggest_first(groups)


def changed_labels(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """'path: old -> new' for sounds whose tempo, key or form changes, by field
    and top two folders."""
    groups: dict[str, list[str]] = defaultdict(list)
    where = {**before["paths"], **after["paths"]}
    old_labels, new_labels = before.get("labels", {}), after.get("labels", {})
    for digest in sorted(set(old_labels) | set(new_labels)):
        old, new = old_labels.get(digest, {}), new_labels.get(digest, {})
        path = where.get(digest, digest)
        for name in FIELDS:
            if old.get(name) != new.get(name):
                change = f"{path}: {old.get(name, '-')} -> {new.get(name, '-')}"
                groups[f"{name:4} {top(path)}"].append(change)
    return biggest_first(groups)


def versions(side: dict[str, Any]) -> str:
    found = side.get("reader_version")
    return f"names v{side['names_version']}, " + (
        f"files' own metadata reader v{found}" if found else "files' own metadata not read"
    )


def compare(before_file: Path, after_file: Path) -> None:
    before = json.loads(before_file.read_text("utf-8"))
    after = json.loads(after_file.read_text("utf-8"))
    kits, roles_ = changed_picks(before, after), changed_roles(before, after)
    print(f"{versions(before)}  ->  {versions(after)}")
    print(f"\nKit picks: {len(kits)} of {len(after['picks'])} takes would pick different sounds")
    for take, voices in kits.items():
        print(f"  {take}", *(f"    {line}" for line in voices), sep="\n")
    sounds = len({path for found in roles_.values() for path in found})
    print(f"\nRoles: {sounds} sounds change (- loses, + gains), by top two folders")
    show(roles_)
    fields = changed_labels(before, after)
    sounds = len({line.rsplit(": ", 1)[0] for found in fields.values() for line in found})
    print(f"\nTempo, key and form: {sounds} sounds change (old -> new), by top two folders")
    show(fields)


def show(groups: dict[str, list[str]]) -> None:
    for group, found in groups.items():
        print(
            f"  {len(found):5}  {group}", *(f"{'':9}{line}" for line in found[:EXAMPLES]), sep="\n"
        )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare"]:
        compare(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        write(Path(sys.argv[1]), Path(sys.argv[2]))
