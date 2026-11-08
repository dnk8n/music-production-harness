"""`mph catalogue ...` subcommands. Rendering only; logic lives in scanner/stats."""

from __future__ import annotations

import sys
from pathlib import Path

from mph.catalogue import db, stats
from mph.catalogue.scanner import Scanner, SourceError
from mph.config import Config
from mph.proc import Runner

MAX_ERRORS_SHOWN = 10


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
