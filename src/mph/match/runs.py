"""What a match run leaves (spec/components/matching.md, REQ-MAT-06): a folder
under ``data_dir/matches/<project>/<match>/``, named by the time it started.
When a run finishes, the match's older runs keep only their report."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from mph.config import Config

RUNS = "matches"
REPORT = "report.json"
NAMED = "%Y%m%dT%H%M%SZ"  # UTC


def folder_of(cfg: Config, project: str, match: str) -> Path:
    return cfg.data_dir / RUNS / project / match


def new_run(cfg: Config, project: str, match: str, started: datetime) -> Path:
    """A fresh folder for a run; two runs started in the same second get two."""
    base = folder_of(cfg, project, match) / started.strftime(NAMED)
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}-{n}")
    path.mkdir(parents=True)
    return path


def write_report(run: Path, report: dict[str, Any]) -> None:
    (run / REPORT).write_text(json.dumps(report, indent=2) + "\n", "utf-8")


def prune(cfg: Config, project: str, match: str, keep: Path) -> int:
    """The match's other runs keep only their report; returns how many things went."""
    gone = 0
    for run in sorted(folder_of(cfg, project, match).iterdir()):
        if run == keep or not run.is_dir():
            continue
        for item in run.iterdir():
            if item.name == REPORT:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            gone += 1
    return gone
