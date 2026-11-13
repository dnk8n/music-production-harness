"""`mph bench ...` subcommands (spec/components/benchmarks.md).

Rendering only: the work happens in ``render``. Audio libraries load on
first use, so ``mph bench list`` stays instant.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any

from mph.bench import feedback, jit, rerender, score
from mph.catalogue import commands as catalogue_commands
from mph.catalogue import db
from mph.config import Config
from mph.proc import Runner

RENDERS = "renders"


def dispatch(args: argparse.Namespace, cfg: Config, runner: Runner, home: Path) -> int:
    if args.action == "list":
        return cmd_list(cfg)
    if args.action == "report":
        return cmd_report(cfg, args.id)
    if args.action == "serve":
        return catalogue_commands.cmd_browse(
            cfg,
            runner,
            args.port,
            open_page=not args.no_open,
            page="bench",
            renderer=jit.Renderer(render_on_demand(cfg, runner, home)),
        )
    return cmd_render(
        cfg,
        runner,
        args.ids,
        render_all=args.all,
        engine=args.engine,
        home=home,
        variants=args.variant,
        force=args.force_rerender,
    )


def render_on_demand(cfg: Config, runner: Runner, home: Path) -> jit.RenderOne:
    """What the listening page calls to make a missing render (REQ-RET-06)."""
    from mph.renoise import bridge  # noqa: PLC0415

    session = bridge.Session()  # one Renoise, started by mph, for the page's renders

    def render_one(benchmark_id: str, variant: str) -> str:
        """Renders a take; returns anything the page should say about it (REQ-REN-08)."""
        from mph.bench import render  # noqa: PLC0415 - heavy audio imports, on first use

        before = len(session.kept)
        catalogue = cfg.data_dir / db.FILENAME
        with closing(db.connect(catalogue)) if catalogue.is_file() else nullcontext() as conn:
            render.run(benchmark_id, cfg, runner, conn, home=home, variant=variant, session=session)
        return " ".join(kept.note() for kept in session.kept[before:])

    return render_one


def latest_reports(cfg: Config, benchmark_id: str) -> list[dict[str, Any]]:
    """The newest report of each of the benchmark's takes that has one."""
    return feedback.newest_takes(cfg.data_dir / RENDERS, benchmark_id)


def cmd_list(cfg: Config) -> int:
    for benchmark_id in score.available():
        loaded = score.load(benchmark_id)
        found = latest_reports(cfg, benchmark_id)
        state = "not rendered"
        if found:
            passed = sum(bool(r["passed"]) for r in found)
            state = f"{passed} of {len(found)} takes measure well"
        print(
            f"{benchmark_id:<11} {loaded.title:<30} {loaded.bpm:g} BPM  "
            f"{loaded.project:<14} {state}"
        )
    return 0


def cmd_report(cfg: Config, benchmark_id: str) -> int:
    found = latest_reports(cfg, benchmark_id)
    if not found:
        print(f"{benchmark_id} hasn't been rendered: run `mph bench render {benchmark_id}`")
        return 1
    for report in found:
        print_report(report)
    return 0


def _jobs(ids: Sequence[str], variants: Sequence[str]) -> list[tuple[str, str, bool]]:
    """(benchmark, variant, named) to render: every take unless some are named.
    A named take must render; the others are skipped when they can't (REQ-BEN-07).
    Unknown ids are left for render to report."""
    jobs = []
    for benchmark_id in ids:
        if variants or benchmark_id not in score.available():
            jobs += [(benchmark_id, name, True) for name in variants or [""]]
            continue
        takes = score.all_variants(score.load(benchmark_id))
        jobs += [(benchmark_id, v.name, False) for v in takes]
    return jobs


def print_report(report: dict[str, Any]) -> None:
    verdict = "pass" if report["passed"] else "FAIL"
    variant = feedback.variant_of_report(report)
    print(f"{report['id']} {report['title']} [{variant}]: {verdict}   score {report['score_hash']}")
    for entry in report["kit"]:
        print(f"  sound  {entry['voice']:<7} {entry['sound']}  ({entry['why']})")
    for check in report["measurements"]:
        mark = "✓" if check["passed"] else "✗"
        note = f"  [{check['note']}]" if check["note"] else ""
        print(f"  {mark} {check['name']}: {check['value']} (want {check['limit']}){note}")
    levels = report["levels"]
    print(
        f"  level  {levels['integrated_lufs']} LUFS, true peak {levels['true_peak_dbtp']} dBTP"
        f"   {report['licence_marker']}"
    )


def cmd_render(
    cfg: Config,
    runner: Runner,
    ids: list[str],
    *,
    render_all: bool,
    engine: str = "auto",
    home: Path | None = None,
    variants: Sequence[str] = (),
    force: bool = False,
) -> int:
    from mph.bench import kit, render, sc  # noqa: PLC0415 - heavy audio imports, on demand
    from mph.renoise import bridge  # noqa: PLC0415

    chosen = score.available() if render_all else ids
    if not chosen:
        print("name a benchmark (see `mph bench list`) or use --all", file=sys.stderr)
        return 2
    catalogue = cfg.data_dir / db.FILENAME
    failed = skipped = 0
    session = bridge.Session()  # one Renoise, started by mph, for every job (REQ-REN-03)
    with closing(db.connect(catalogue)) if catalogue.is_file() else nullcontext() as conn:
        for benchmark_id, variant, named in _jobs(chosen, variants):
            if not named and (why := _cannot(benchmark_id, variant, home)):
                print(f"{benchmark_id} [{variant}]: skipped, {why}")
                continue
            decision = _decide(
                cfg, conn, benchmark_id, variant, engine=engine, home=home, force=force
            )
            if not decision.render:
                print(f"{benchmark_id} [{variant}]: skipped, {decision.why}")
                skipped += 1
                continue
            print(f"{benchmark_id} [{variant or 'first take'}]: rendering, {decision.why}")
            try:
                outcome = render.run(
                    benchmark_id,
                    cfg,
                    runner,
                    conn,
                    engine=engine,
                    home=home,
                    variant=variant,
                    session=session,
                )
            except (score.ScoreError, kit.KitError, sc.RenderError, bridge.BridgeError) as exc:
                print(f"{benchmark_id} [{variant or 'first take'}]: {exc}", file=sys.stderr)
                failed += 1
                continue
            print_report(outcome.report)
            print(f"  files  {outcome.folder}   (made in {outcome.report['engine']})")
            failed += not outcome.passed
    for kept in session.kept:  # songs someone had open in Renoise (REQ-REN-08)
        print(f"\n{kept.note()}")
    if skipped:
        print(f"\n{skipped} skipped: a pass is a pass. --force-rerender renders them anyway.")
    print("\nlisten and give verdicts: mph bench serve")
    return 1 if failed else 0


def _decide(
    cfg: Config,
    conn: sqlite3.Connection | None,
    benchmark_id: str,
    variant: str,
    *,
    engine: str,
    home: Path | None,
    force: bool,
) -> rerender.Decision:
    """Whether this take renders (REQ-BEN-14). One that can't even be planned
    renders, so that render explains what's wrong."""
    from mph.bench import render, sc  # noqa: PLC0415 - heavy audio imports, on demand

    try:
        take, chosen_variant, chosen = render.take(
            benchmark_id, variant, engine, Path.home() if home is None else home
        )
    except (score.ScoreError, sc.RenderError, OSError):
        return rerender.Decision(True, "to report what's wrong")
    renders = cfg.data_dir / RENDERS
    made, verdict = feedback.standing(renders, conn, benchmark_id, chosen_variant.name)
    current = render.take_inputs(cfg, take, chosen)
    return rerender.decide(made, verdict, current, force=force)


def _cannot(benchmark_id: str, variant: str, home: Path | None) -> str:
    from mph.bench import render  # noqa: PLC0415 - heavy audio imports, on demand

    loaded = score.load(benchmark_id)
    _, take = score.variant_of(loaded, variant)
    return render.can_render(
        take, Path.home() if home is None else home, only=loaded.supercollider_only
    )
