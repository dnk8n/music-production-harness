"""`mph catalogue ...` subcommands. Rendering only; logic lives in scanner/stats."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from mph import secrets
from mph.bench import jit
from mph.catalogue import agreement, browse, db, provenance, remote, search, stats
from mph.catalogue.scanner import Scanner, SourceError, utc_now
from mph.config import Config
from mph.net import Http, UrllibHttp
from mph.proc import Runner

MAX_ERRORS_SHOWN = 10
REMOTE_ACTIONS = frozenset({"fetch", "auth", "credits", "refresh"})
PROVENANCE_ACTIONS = frozenset({"derive", "stash", "purge-cache", "refresh", "lineage"})


def dispatch(
    args: argparse.Namespace,
    cfg: Config,
    runner: Runner,
    store: secrets.SecretStore | None = None,
    http: Http | None = None,
) -> int:
    handlers: dict[str, Callable[[], int]] = {
        "scan": lambda: cmd_scan(cfg, runner, args.source, args.workers),
        "analyse": lambda: cmd_analyse(
            cfg, args.role, args.pack, args.limit, args.workers, force=args.force
        ),
        "search": lambda: cmd_search(cfg, args),
        "browse": lambda: cmd_browse(cfg, runner, args.port, open_page=not args.no_open),
        "stats": lambda: cmd_stats(cfg),
    }
    if args.action in REMOTE_ACTIONS:
        return cmd_remote(cfg, runner, store, http or UrllibHttp(), args)
    if args.action in PROVENANCE_ACTIONS:
        return cmd_provenance(cfg, runner, args)
    return handlers[args.action]()


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
    for line in result.errors[:MAX_ERRORS_SHOWN]:
        print(f"    {line}")
    if len(result.errors) > MAX_ERRORS_SHOWN:
        print(f"    ... and {len(result.errors) - MAX_ERRORS_SHOWN} more")
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


def query_from(args: argparse.Namespace) -> search.Query:
    return search.Query(
        roles=tuple(args.role),
        bpm=args.bpm,
        bpm_tolerance=args.bpm_tolerance / 100,
        key=search.parse_key_query(args.key) if args.key else None,
        form=args.form,
        min_lufs=args.min_lufs,
        max_lufs=args.max_lufs,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        licence=args.licence,
        text=tuple(args.text),
        limit=args.limit,
    )


def cmd_search(cfg: Config, args: argparse.Namespace) -> int:
    path = db_path(cfg)
    if not path.exists():
        print("no catalogue yet: run `mph catalogue scan`", file=sys.stderr)
        return 1
    try:
        query = query_from(args)
    except search.QueryError as exc:
        print(exc, file=sys.stderr)
        return 2
    with closing(db.connect(path)) as conn:
        result = search.search(conn, query)
    if args.json:
        print(json.dumps([hit.to_dict() for hit in result.hits], indent=2))
        return 0
    print(render_search(result, query))
    return 0 if result.hits else 1


def render_search(result: search.Result, query: search.Query) -> str:
    lines = [f"{result.matched} matches" + (f", best {len(result.hits)}:" if result.hits else "")]
    for rank, hit in enumerate(result.hits, start=1):
        length = f"{hit.duration_s:.1f}s" if hit.duration_s is not None else "?s"
        loud = f"{hit.lufs:.1f} LUFS" if hit.lufs is not None else "not analysed"
        lines.append(f"{rank:>3}. {hit.path}")
        lines.append(f"     score {hit.score:g} | {length} | {loud} | {hit.licence}")
        if hit.reasons:
            lines.append(f"     why: {'; '.join(hit.reasons)}")
    if result.unanalysed_skipped:
        lines.append(
            f"note: {result.unanalysed_skipped} sounds have no loudness yet and were left out; "
            "run `mph catalogue analyse` to include them"
        )
    if not result.hits and (query.bpm or query.key):
        lines.append("tip: widen --bpm-tolerance, or drop --key to see more")
    return "\n".join(lines)


def cmd_provenance(cfg: Config, runner: Runner, args: argparse.Namespace) -> int:
    if args.action == "purge-cache":
        print(f"removed {provenance.purge_cache(cfg.data_dir)} cached files; stashes kept")
        return 0
    path = db_path(cfg)
    if not path.exists():
        print("no catalogue yet: run `mph catalogue scan`", file=sys.stderr)
        return 1
    with closing(db.connect(path)) as conn:
        try:
            return _provenance_action(conn, cfg.data_dir, runner, args)
        except provenance.ProvenanceError as exc:
            print(exc, file=sys.stderr)
            return 2


def _provenance_action(
    conn: sqlite3.Connection, data_dir: Path, runner: Runner, args: argparse.Namespace
) -> int:
    workshop = provenance.Workshop(conn, runner, data_dir)
    if args.action == "refresh":
        report = provenance.refresh(workshop, conn, data_dir)
        print(
            f"{report.present} present, {len(report.rebuilt)} rebuilt, {len(report.failed)} failed"
        )
        for line in report.failed[:MAX_ERRORS_SHOWN]:
            print(f"  ! {line}")
        return 1 if report.failed else 0
    digest = provenance.resolve(conn, args.sound)
    if args.action == "lineage":
        print("\n".join(provenance.lineage(conn, digest)))
        return 0
    if args.action == "stash":
        print(f"stashed: {provenance.stash(conn, data_dir, digest, args.name)}")
        return 0
    made = workshop.derive(
        provenance.recipe(args.recipe, {"start": args.start, "end": args.end}), [digest]
    )
    print(f"{'reused' if made.reused else 'made'} {made.digest[:12]}: {made.path}")
    if args.stash:
        print(f"stashed: {provenance.stash(conn, data_dir, made.digest, args.stash)}")
    return 0


def cmd_browse(
    cfg: Config,
    runner: Runner,
    port: int,
    *,
    open_page: bool,
    serve: Callable[[browse.App, int], browse.Server] = browse.serve,
    page: str = "",
    renderer: jit.Renderer | None = None,
) -> int:
    """``page`` is a tab to open on, e.g. "bench"; the benchmarks tab works
    without a scanned catalogue."""
    path = db_path(cfg)
    if not path.exists() and not page:
        print("no catalogue yet: run `mph catalogue scan`", file=sys.stderr)
        return 1
    try:
        server = serve(browse.App(path, cfg.data_dir, runner, port, renderer), port)
    except OSError as exc:
        print(f"cannot listen on port {port}: {exc}. Try --port 8766", file=sys.stderr)
        return 1
    url = f"http://{browse.HOST}:{port}/" + (f"#{page}" if page else "")
    print(f"{'listening page' if page else 'sample browser'} at {url}  (Ctrl-C to stop)")
    opener = next((name for name in ("open", "xdg-open") if runner.which(name)), None)
    if open_page and opener:
        runner.run([opener, url])
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


def connectors(
    http: Http, store: secrets.SecretStore | None, *, previews: bool = False
) -> dict[str, remote.Connector]:
    found: dict[str, remote.Connector] = {
        "archive": remote.Archive(http),
        "librivox": remote.librivox(http),
    }
    if store is not None:
        found["freesound"] = remote.Freesound(http, store, previews=previews)
    return found


def cmd_remote(
    cfg: Config,
    runner: Runner,
    store: secrets.SecretStore | None,
    http: Http,
    args: argparse.Namespace,
) -> int:
    with closing(db.connect(db_path(cfg))) as conn:
        try:
            if args.action == "credits":
                hashes = [r[0] for r in conn.execute("SELECT asset_hash FROM download ORDER BY 1")]
                print("\n".join(remote.attribution(conn, hashes)) or "nothing downloaded yet")
                return 0
            if args.action == "auth":
                return _sign_in(store, http)
            if args.action == "refresh":
                fetcher = remote.Fetcher(conn, runner, http, cfg.data_dir)
                return _refresh(fetcher, cfg, runner, connectors(http, store), args)
            fetcher = remote.Fetcher(conn, runner, http, cfg.data_dir)
            return _fetch(fetcher, connectors(http, store, previews=args.previews), args)
        except remote.RemoteError as exc:
            print(exc, file=sys.stderr)
            return 2


def _sign_in(
    store: secrets.SecretStore | None, http: Http, ask: Callable[[str], str] = input
) -> int:
    if store is None:
        print("Freesound sign-in is kept in the macOS Keychain", file=sys.stderr)
        return 2
    freesound = remote.Freesound(http, store)
    print("1. Open this page, sign in to Freesound and press Authorize:")
    print(f"   {freesound.authorize_url()}")
    print("2. Freesound then shows a code. Paste it here.")
    freesound.sign_in(ask("   code: "))
    print("Signed in. Original-quality downloads now work, and you won't need to do this again.")
    return 0


def _refresh(
    fetcher: remote.Fetcher,
    cfg: Config,
    runner: Runner,
    available: dict[str, remote.Connector],
    args: argparse.Namespace,
) -> int:
    report = fetcher.refresh(available)
    print(
        f"downloads: {report.already} present, {len(report.fetched)} fetched again, "
        f"{len(report.failed)} failed"
    )
    for line in report.failed[:MAX_ERRORS_SHOWN]:
        print(f"  ! {line}")
    derived = cmd_provenance(cfg, runner, args)
    return 1 if report.failed else derived


def _fetch(
    fetcher: remote.Fetcher, available: dict[str, remote.Connector], args: argparse.Namespace
) -> int:
    if args.connector not in available:
        print("Freesound needs the macOS Keychain for its keys", file=sys.stderr)
        return 2
    query = " ".join(args.query)
    print(f"searching {args.connector} for {query!r} ...")
    report = fetcher.fetch(
        available[args.connector],
        query,
        args.limit,
        clear_only=args.clear_only,
        max_bytes=args.max_mb * 1_000_000,
    )
    for line in report.fetched:
        print(f"  + {line}")
    for line in report.failed[:MAX_ERRORS_SHOWN]:
        print(f"  ! {line}")
    print(
        f"{len(report.fetched)} downloaded, {report.already} already here, "
        f"{len(report.failed)} failed. Listen with `mph catalogue browse`."
    )
    return 1 if report.failed and not report.fetched else 0
