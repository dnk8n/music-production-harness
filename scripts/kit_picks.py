"""The sounds each benchmark take would pick, for scripts/kit-diff (REQ-BEN-05).

    python scripts/kit_picks.py COPY OUT       picks and roles, as this checkout makes them
    python scripts/kit_picks.py --compare A B  what differs between two such files

The first form copies the catalogue to COPY and scans the copy with this
checkout's naming rules, so the copy labels sounds the way this checkout
would. Then it writes, as JSON in OUT, the sound every voice of every take
would pick, and every sound's roles. Dean's catalogue, sources and renders
are only read.
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
from mph.catalogue import db, names
from mph.catalogue.scanner import Scanner

EXAMPLES = 3  # sounds named for each group of role changes


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
            found[f"{benchmark} {variant.name}"] = {
                c.voice: f"{c.label} ({c.digest[:12]})" if c.digest else c.label for c in chosen
            }
    return found


def roles(conn: sqlite3.Connection) -> dict[str, list[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for digest, value in conn.execute("SELECT asset_hash, value FROM tag WHERE name = 'role'"):
        found[digest].add(value)
    return {digest: sorted(values) for digest, values in found.items()}


def paths(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT f.asset_hash, min(f.rel_path) FROM source_file f "
        "JOIN source s ON s.id = f.source_id WHERE s.kind = 'local' GROUP BY f.asset_hash"
    )
    return {digest: path for digest, path in rows}


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
            "picks": picks(conn, cfg.data_dir),
            "roles": roles(conn),
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


def changed_roles(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Paths of sounds whose roles change, by change and top two folders."""
    groups: dict[str, list[str]] = defaultdict(list)
    where = {**before["paths"], **after["paths"]}
    for digest in sorted(set(before["roles"]) | set(after["roles"])):
        old, new = set(before["roles"].get(digest, ())), set(after["roles"].get(digest, ()))
        path = where.get(digest, digest)
        top = "/".join(PurePosixPath(path).parts[:2])
        for sign, roles_ in (("-", old - new), ("+", new - old)):
            for role in sorted(roles_):
                groups[f"{sign} {role:10} {top}"].append(path)
    return dict(sorted(groups.items(), key=lambda g: (-len(g[1]), g[0])))


def compare(before_file: Path, after_file: Path) -> None:
    before = json.loads(before_file.read_text("utf-8"))
    after = json.loads(after_file.read_text("utf-8"))
    kits, labels = changed_picks(before, after), changed_roles(before, after)
    print(f"names version {before['names_version']} -> {after['names_version']}")
    print(f"\nKit picks: {len(kits)} of {len(after['picks'])} takes would pick different sounds")
    for take, voices in kits.items():
        print(f"  {take}", *(f"    {line}" for line in voices), sep="\n")
    sounds = len({path for found in labels.values() for path in found})
    print(f"\nRoles: {sounds} sounds change (- loses, + gains), by top two folders")
    for group, found in labels.items():
        print(
            f"  {len(found):5}  {group}", *(f"{'':9}{path}" for path in found[:EXAMPLES]), sep="\n"
        )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare"]:
        compare(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        write(Path(sys.argv[1]), Path(sys.argv[2]))
