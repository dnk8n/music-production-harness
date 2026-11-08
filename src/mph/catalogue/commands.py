"""`mph catalogue ...` subcommands. Rendering only; logic lives in scanner/stats."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from mph.catalogue import agreement, db, stats
from mph.catalogue.scanner import Scanner, SourceError, utc_now
from mph.config import Config
from mph.proc import Runner

MAX_ERRORS_SHOWN = 10


def dispatch(args: argparse.Namespace, cfg: Config, runner: Runner) -> int:
    if args.action == "scan":
        return cmd_scan(cfg, runner, args.source, args.workers)
    if args.action == "analyse":
        return cmd_analyse(cfg, args.role, args.pack, args.limit, args.workers, force=args.force)
    return cmd_stats(cfg)


def db_path(cfg: Config) -> Path:
    return cfg.data_dir / db.FILENAME


def cmd_scan(cfg: Config, runner: Runner, source_ids: list[str], workers: int) -> int:
    local = [s for s in cfg.sources if s.kind == "local"]
    chosen = [s for s in local if not source_ids or s.id in source_ids]
    unknown = set(source_ids) - {s.id for s in local}
    if unknown or not chosen:
        print(
            f"no such local source: {', '.join(sorted(unknown)) or '(none configured)'}",
            file=sys.stderr,
        )
        return 2
    conn = db.connect(db_path(cfg))
    scanner = Scanner(conn, runner, workers=workers)
    code = 0
    for source in chosen:
        print(f"scanning {source.id} ({source.location}) read-only ...")
        try:
            result = scanner.scan(source)
        except SourceError as exc:
            print(f"  {exc}", file=sys.stderr)
            code = 1
            continue
        print(
            f"  {result.files_seen} audio files: {result.skipped} unchanged, "
            f"{result.hashed} hashed, {result.new_assets} new, {result.moved} moved, "
            f"{result.removed} removed; {result.documents} documents"
        )
        for error in result.probe_errors[:MAX_ERRORS_SHOWN]:
            print(f"  ! {error}")
        if len(result.probe_errors) > MAX_ERRORS_SHOWN:
            print(
                f"  ! ... and {len(result.probe_errors) - MAX_ERRORS_SHOWN} more unreadable files"
            )
    conn.close()
    return code


def select_assets(
    conn: sqlite3.Connection, roles: list[str], packs: list[str], limit: int | None
) -> list[str]:
    """Asset hashes matching every given filter (role/pack tags of any origin)."""
    sql = "SELECT hash FROM asset a WHERE probe_error IS NULL"
    params: list[object] = []
    exists = "SELECT 1 FROM tag t WHERE t.asset_hash = a.hash AND t.name = ? AND t.value IN "
    for name, values in (("role", roles), ("pack", packs)):
        if values:
            placeholders = "(" + ", ".join("?" * len(values)) + ")"  # only "?" is interpolated
            sql += f" AND EXISTS ({exists}{placeholders})"
            params.extend([name, *values])
    sql += " ORDER BY hash"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [row[0] for row in conn.execute(sql, params)]


def cmd_analyse(
    cfg: Config, roles: list[str], packs: list[str], limit: int | None, workers: int, *, force: bool
) -> int:
    path = db_path(cfg)
    if not path.exists():
        print("no catalogue yet: run `mph catalogue scan`", file=sys.stderr)
        return 1
    # Imported here so commands that don't analyse stay fast to start (librosa is heavy).
    from mph.catalogue import analysis  # noqa: PLC0415

    with closing(db.connect(path)) as conn:
        hashes = select_assets(conn, roles, packs, limit)
        print(f"analysing up to {len(hashes)} sounds with {workers} workers ...")
        result = analysis.run(conn, hashes, workers=workers, force=force, clock=utc_now)
        agreements = agreement.measure(conn, hashes)
    print(
        f"  {result.analysed} analysed, {result.cached} already up to date, "
        f"{result.failed} could not be decoded"
    )
    for item in agreements:
        if item.compared:
            print(
                f"  {item.field}: analysis agrees with names on {item.agreed}/{item.compared} "
                f"({item.rate:.0%})"
            )
    return 0


def cmd_stats(cfg: Config) -> int:
    path = db_path(cfg)
    if not path.exists():
        print("no catalogue yet: run `mph catalogue scan`", file=sys.stderr)
        return 1
    conn = db.connect(path)
    s = stats.summarise(conn)
    conn.close()
    print(
        f"{s.assets} unique sounds ({s.files} files, {s.gigabytes} GB, {s.hours} h), "
        f"{s.documents} documents, {s.probe_errors} unreadable"
    )
    for title, pairs in (
        ("packs", s.by_pack),
        ("formats", s.by_format),
        ("roles (from names)", s.by_role),
    ):
        print(f"\n{title}:")
        for label, count in pairs[:15]:
            print(f"  {count:>6}  {label}")
    return 0
