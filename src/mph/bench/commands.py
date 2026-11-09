"""`mph bench ...` subcommands (spec/components/benchmarks.md).

Rendering only: the work happens in ``render``. Audio libraries load on
first use, so ``mph bench list`` stays instant.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any

from mph.bench import feedback, score
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
            cfg, runner, args.port, open_page=not args.no_open, page="bench"
        )
    return cmd_render(
        cfg,
        runner,
        args.ids,
        render_all=args.all,
        engine=args.engine,
        home=home,
        variants=args.variant,
        every_variant=args.variants,
    )


def latest_report(cfg: Config, benchmark_id: str) -> dict[str, Any] | None:
    return feedback.latest(cfg.data_dir / RENDERS, benchmark_id)


def cmd_list(cfg: Config) -> int:
    for benchmark_id in score.available():
        loaded = score.load(benchmark_id)
        report = latest_report(cfg, benchmark_id)
        state = "not rendered"
        if report:
            state = ("pass" if report["passed"] else "FAIL") + f" ({report['rendered_at']})"
        print(f"{benchmark_id:<11} {loaded.title:<30} {loaded.bpm:g} BPM   {state}")
    return 0


def cmd_report(cfg: Config, benchmark_id: str) -> int:
    report = latest_report(cfg, benchmark_id)
    if report is None:
        print(f"{benchmark_id} hasn't been rendered: run `mph bench render {benchmark_id}`")
        return 1
    print_report(report)
    return 0


def _jobs(
    ids: Sequence[str], variants: Sequence[str], *, every_variant: bool
) -> list[tuple[str, str]]:
    """(benchmark, variant) pairs to render; unknown ids are left for render to report."""
    jobs = []
    for benchmark_id in ids:
        names: Sequence[str] = variants or [score.MAIN]
        if every_variant and benchmark_id in score.available():
            names = [v.name for v in score.all_variants(score.load(benchmark_id))]
        jobs += [(benchmark_id, name) for name in names]
    return jobs


def print_report(report: dict[str, Any]) -> None:
    verdict = "pass" if report["passed"] else "FAIL"
    variant = report.get("variant", score.MAIN)
    shown = "" if variant == score.MAIN else f" [{variant}]"
    print(f"{report['id']} {report['title']}{shown}: {verdict}   score {report['score_hash']}")
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
    every_variant: bool = False,
) -> int:
    from mph.bench import kit, render, sc  # noqa: PLC0415 - heavy audio imports, on demand
    from mph.renoise import bridge  # noqa: PLC0415

    chosen = score.available() if render_all else ids
    if not chosen:
        print("name a benchmark (see `mph bench list`) or use --all", file=sys.stderr)
        return 2
    catalogue = cfg.data_dir / db.FILENAME
    failed = 0
    session = bridge.Session()  # one Renoise, started by mph, for every job (REQ-REN-03)
    with closing(db.connect(catalogue)) if catalogue.is_file() else nullcontext() as conn:
        for benchmark_id, variant in _jobs(chosen, variants, every_variant=every_variant):
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
                print(f"{benchmark_id} ({variant}): {exc}", file=sys.stderr)
                failed += 1
                continue
            print_report(outcome.report)
            print(f"  files  {outcome.folder}   (made in {outcome.report['engine']})")
            failed += not outcome.passed
    print("\nlisten and give verdicts: mph bench serve")
    return 1 if failed else 0
