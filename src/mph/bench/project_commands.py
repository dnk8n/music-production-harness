"""`mph projects ...` (spec/components/projects.md). Rendering only: finding
projects is ``projects.py``, and placeholder sounds are ``fill.py``.

- ``list``: every project, on or off, mine or Dean's (REQ-PRJ-03).
- ``show``: one project in full, with its benchmarks and planned ones.
- ``copy``: a project's text files into a folder of the user's own (REQ-PRJ-04).
- ``fill``: what the catalogue lacks, and with ``--yes`` free placeholders for
  it (REQ-PRJ-05).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tomllib
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from mph import secrets
from mph.bench import fill, ideas, projects, score
from mph.catalogue import db, remote
from mph.catalogue.scanner import utc_now
from mph.config import Config
from mph.net import Http, UrllibHttp
from mph.proc import Runner

COPIED = (".toml", ".md", ".txt")  # a project's text files; nothing else is copied
SHOWN = 3  # voices named per missing sound
# Freesound originals SuperCollider plays as they are (REQ-AUD-01, kit.READABLE).
READABLE_TYPES = "type:(wav OR aif OR aiff OR flac OR ogg)"
Analyse = Callable[[sqlite3.Connection, list[str]], object]


@dataclass(frozen=True, slots=True)
class Tools:
    """What fill reaches beyond mph through: processes, the Keychain, the web
    and the analyser (REQ-ENG-03)."""

    runner: Runner
    store: secrets.SecretStore | None
    http: Http
    analyse: Analyse


def dispatch(
    args: argparse.Namespace,
    cfg: Config,
    runner: Runner,
    store: secrets.SecretStore | None,
    *,
    http: Http | None = None,
    analyse: Analyse | None = None,
) -> int:
    projects.use(projects.settings_of(cfg))
    try:
        if args.action == "list":
            return cmd_list(projects.current(), as_json=args.json)
        if args.action == "show":
            return cmd_show(projects.current().find(args.project))
        if args.action == "copy":
            return cmd_copy(cfg, projects.current().find(args.project), args.to)
        tools = Tools(runner, store, http or UrllibHttp(), analyse or _analyse)
        return cmd_fill(cfg, args, tools)
    except projects.ProjectError as exc:
        print(exc, file=sys.stderr)
        return 2


# --- list and show -------------------------------------------------------------------


def _state(project: projects.Project) -> str:
    if project.problem:
        return "broken"
    return "on" if project.on else "off"


def cmd_list(selection: projects.Selection, *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "projects": [p.to_dict() for p in selection.projects],
                    "benchmarks_on": selection.available(),
                    "problems": list(selection.problems),
                },
                indent=2,
            )
        )
        return 0 if not selection.problems else 1
    width = max((len(p.id) for p in selection.projects), default=10)
    for project in selection.projects:
        where = "example" if project.example else str(project.folder.parent)
        count = len(project.scores)
        print(
            f"  {_state(project):<6} {project.id:<{width}}  {count:>2} "
            f"benchmark{'' if count == 1 else 's'}  {project.name}  ({where})"
        )
    for problem in selection.problems:
        print(f"  ! {problem}")
    on = len(selection.available())
    print(
        f"{on} benchmark{'' if on == 1 else 's'} on. `mph projects show NAME` says what a "
        "project is and needs. Turn examples on or off with [projects] in your config "
        "(spec/components/projects.md)."
    )
    return 0 if not selection.problems else 1


def _title(path: Path) -> str:
    try:
        return str(tomllib.loads(path.read_text("utf-8")).get("title", ""))
    except (OSError, tomllib.TOMLDecodeError):
        return "(can't be read)"


def cmd_show(project: projects.Project) -> int:
    kind = "Dean's example" if project.example else "your own"
    print(f"{project.name}  ({project.id}: {kind}, {_state(project)})")
    print(f"folder  {project.folder}")
    if project.spec:
        print(f"spec    {project.spec}")
    if project.problem:
        print(f"! it {project.problem}")
    for heading, text in (("", project.about), ("Needs: ", project.needs)):
        if text:
            print(f"\n{heading}{text}")
    print("\nBenchmarks:" if project.scores else "\nNo scores yet.")
    for benchmark_id in project.scores:
        print(f"  {benchmark_id}  {_title(project.path(benchmark_id))}")
    planned = [
        (b, title)
        for b, title, section in ideas.specced()
        if project.example and section == project.name and b not in project.scores
    ]
    if planned:
        print("Planned in the spec, with no score yet:")
        for benchmark_id, title in planned:
            print(f"  {benchmark_id}  {title}")
    return 0


# --- copy ----------------------------------------------------------------------------


def _inside_source(cfg: Config, folder: Path) -> str:
    for source in cfg.sources:
        root = source.local_path
        if root is not None and folder.resolve().is_relative_to(root.resolve()):
            return source.id
    return ""


def cmd_copy(cfg: Config, project: projects.Project, to: Path | None) -> int:
    """A project's text files into a folder of the user's own (REQ-PRJ-04)."""
    if to is None and not cfg.projects.folders:
        print(
            'say where to copy it: --to FOLDER, or add folders = ["~/your/projects"] '
            "under [projects] in your config",
            file=sys.stderr,
        )
        return 2
    folder = (to or cfg.projects.folders[0]).expanduser()
    dest = folder / project.id
    if source := _inside_source(cfg, dest):
        print(f"{dest} is inside source {source}, which mph never writes to", file=sys.stderr)
        return 2
    if dest.exists():
        print(f"{dest} is there already: pick another --to, or move it away", file=sys.stderr)
        return 2
    files = [p for p in sorted(project.folder.iterdir()) if p.is_file() and p.suffix in COPIED]
    dest.mkdir(parents=True)
    for path in files:
        shutil.copy2(path, dest / path.name)
    print(f"copied {len(files)} files of {project.name} to {dest}")
    listed = any(dest.parent.resolve() == f.resolve() for f in cfg.projects.folders)
    if not listed:
        print(
            f"add {dest.parent} to folders under [projects] in your config to use it "
            "(`mph projects list` then shows it)"
        )
    if project.example and project.scores:
        print(
            "your copy keeps the example's benchmark ids, so mph uses yours instead of "
            "the example's; rename a score file and its id to keep both"
        )
    return 0


# --- fill ----------------------------------------------------------------------------


def _scores(selection: projects.Selection, names: Sequence[str]) -> list[score.Score]:
    chosen = [selection.find(n) for n in names] if names else selection.on()
    off = [p.id for p in chosen if not p.on]
    if off:
        raise projects.ProjectError(
            f"turn {', '.join(off)} on first: [projects].examples in your config"
        )
    found = []
    for project in chosen:
        for benchmark_id in project.scores:
            if selection.owners.get(benchmark_id) is not project:
                continue  # the user's own copy of it is used instead
            try:
                found.append(score.load(benchmark_id))
            except score.ScoreError as exc:
                print(f"  ! {benchmark_id}: {exc}", file=sys.stderr)
    return found


def _named(voices: Sequence[str]) -> str:
    more = len(voices) - SHOWN
    return ", ".join(voices[:SHOWN]) + (f" and {more} more" if more > 0 else "")


def _show_plan(found: fill.Plan, benchmarks: int) -> None:
    print(
        f"{found.voices} voices in {benchmarks} benchmarks: {found.synths} are synths on "
        f"purpose, {found.covered} have a sound in the catalogue, {found.missing} need one"
    )
    if found.needs:
        print("missing (one search each, free CC0, public domain or CC-BY sounds only):")
    for need in found.needs:
        what = f"{need.role} {'loops' if need.form == fill.LOOP else 'one-shots'}"
        meanwhile = f"plays {need.meanwhile} meanwhile" if need.meanwhile else "can't render"
        print(f'  {what:<20} "{need.query}", {meanwhile}: {_named(need.voices)}')
    if found.recordings:
        print("recordings no placeholder can stand in for (scan your own copy):")
    for rec in found.recordings:
        print(f"  {rec.hash[: fill.SHORT_HASH]}  {rec.why}")
        print(f"    played by {_named(rec.voices)}")


def _freesound(
    store: secrets.SecretStore | None, http: Http
) -> tuple[remote.Connector, tuple[str, ...], str]:
    """Freesound, and the filters and note that go with how it is set up."""
    if store is None:
        raise remote.RemoteError("Freesound's keys live in the macOS Keychain; try --from archive")
    if store.get(remote.REFRESH_ACCOUNT):
        return remote.Freesound(http, store), (READABLE_TYPES,), "original files"
    connector = remote.Freesound(http, store, previews=True, preview_format="ogg")
    note = "OGG previews (`mph catalogue auth freesound` once gets the original files)"
    return connector, (), note


def _analyse(conn: sqlite3.Connection, hashes: list[str]) -> object:
    # Imported here: librosa takes seconds to load (REQ-CAT-04).
    from mph.catalogue import analysis  # noqa: PLC0415

    return analysis.run(conn, hashes, workers=2, force=False, clock=utc_now)


def cmd_fill(cfg: Config, args: argparse.Namespace, tools: Tools) -> int:
    selection = projects.current()
    scores = _scores(selection, args.project)
    path = cfg.data_dir / db.FILENAME
    if not args.yes:
        if not path.exists():
            found = fill.plan(scores, None, cfg.data_dir)
            _show_plan(found, len(scores))
            print("no catalogue yet, so every sound is missing: `mph catalogue scan` first")
        else:
            with closing(db.connect(path)) as conn:
                found = fill.plan(scores, conn, cfg.data_dir)
            _show_plan(found, len(scores))
        if found.needs:
            print(
                f"nothing downloaded: add --yes to fetch up to {args.limit} for each "
                f"from {args.source}"
            )
        return 0
    with closing(db.connect(path)) as conn:
        return _fetch_and_report(cfg, args, tools, conn, scores)


def _fetch_and_report(
    cfg: Config,
    args: argparse.Namespace,
    tools: Tools,
    conn: sqlite3.Connection,
    scores: list[score.Score],
) -> int:
    found = fill.plan(scores, conn, cfg.data_dir)
    _show_plan(found, len(scores))
    if not found.needs:
        return 0
    try:
        if args.source == "freesound":
            connector, filters, note = _freesound(tools.store, tools.http)
        else:
            connector, filters, note = remote.Archive(tools.http), (), "whole items"
        print(f"fetching from {args.source} ({note}) ...")
        fetcher = remote.Fetcher(conn, tools.runner, tools.http, cfg.data_dir)
        filled = fill.fetch(
            found.needs, connector, fetcher, conn, limit=args.limit, filters=filters
        )
    except remote.RemoteError as exc:
        print(exc, file=sys.stderr)
        return 2
    hashes = sorted({h for f in filled for h in f.report.hashes})
    for done in filled:
        got = len(done.report.hashes)
        print(f"  {done.need.role} {done.need.form}: {got} found, {len(done.report.failed)} failed")
        for line in done.report.failed[:SHOWN]:
            print(f"    ! {line}")
    if hashes:
        print(f"analysing {len(hashes)} sounds so the kit can find them ...")
        tools.analyse(conn, hashes)
    still = fill.plan(scores, conn, cfg.data_dir).needs
    for need in still:
        print(f'  still missing: {need.role} {need.form} ("{need.query}")')
    print(
        "`mph catalogue credits` says who made each sound; CC-BY ones need that credit "
        "if you publish a render"
    )
    return 1 if still else 0
