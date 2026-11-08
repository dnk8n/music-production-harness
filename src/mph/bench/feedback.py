"""Data behind the listening page (spec/components/feedback.md).

The page shows each benchmark's latest render: what Dean should hear, the
measurements, the delivered file and its variants for A/B, and the verdicts
given so far. Verdicts are stored in the catalogue DB against the render's
score hash, so each one points at exactly what was heard (REQ-FBK-02).
Only files listed in a render's own report can be served (REQ-FBK-05).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from mph.bench import score
from mph.catalogue.scanner import utc_now

VERDICTS = ("pass", "close", "fail")
RENDER_HASH = re.compile(r"^[0-9a-f]{16}$")
MAX_NOTES = 4000


class FeedbackError(ValueError):
    pass


def reports(renders: Path, benchmark_id: str) -> list[dict[str, Any]]:
    """Every readable report for a benchmark, newest first."""
    found = []
    for path in (renders / benchmark_id).glob("*/report.json"):
        try:
            found.append(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda r: str(r.get("rendered_at", "")), reverse=True)


def verdicts(conn: sqlite3.Connection | None, benchmark_id: str) -> list[dict[str, str]]:
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT score_hash, verdict, notes, created_at FROM verdict WHERE benchmark = ? "
        "ORDER BY created_at DESC, id DESC",
        (benchmark_id,),
    ).fetchall()
    keys = ("score_hash", "verdict", "notes", "created_at")
    return [dict(zip(keys, map(str, row), strict=True)) for row in rows]


def listing(renders: Path, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
    out = []
    for benchmark_id in score.available():
        loaded = score.load(benchmark_id)
        latest = next(iter(reports(renders, benchmark_id)), None)
        out.append(
            {
                "id": benchmark_id,
                "title": loaded.title,
                "hear": loaded.hear,
                "bpm": loaded.bpm,
                "report": latest,
                "verdicts": verdicts(conn, benchmark_id),
            }
        )
    return out


def playable(renders: Path, benchmark_id: str, render_hash: str, name: str) -> Path | None:
    """The file to serve, if the render's own report lists it; otherwise None."""
    if benchmark_id not in score.available() or not RENDER_HASH.match(render_hash):
        return None
    folder = renders / benchmark_id / render_hash
    try:
        report = json.loads((folder / "report.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    files = report["files"]
    listed = {
        files["delivery"],
        *files.get("stems", []),
        *(v["file"] for v in report.get("variants", [])),
    }
    if name not in listed:
        return None
    path = folder / name
    return path if path.is_file() else None


def record(conn: sqlite3.Connection, renders: Path, payload: dict[str, Any]) -> dict[str, str]:
    benchmark_id = str(payload.get("id", ""))
    render_hash = str(payload.get("score_hash", ""))
    verdict = str(payload.get("verdict", ""))
    notes = str(payload.get("notes", "")).strip()
    if verdict not in VERDICTS:
        raise FeedbackError(f"verdict must be one of {', '.join(VERDICTS)}")
    if len(notes) > MAX_NOTES:
        raise FeedbackError(f"notes are limited to {MAX_NOTES} characters")
    if not any(r.get("score_hash") == render_hash for r in reports(renders, benchmark_id)):
        raise FeedbackError("no such render: render it again and reload the page")
    when = utc_now()
    with conn:
        conn.execute(
            "INSERT INTO verdict (benchmark, score_hash, verdict, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (benchmark_id, render_hash, verdict, notes, when),
        )
    return {"score_hash": render_hash, "verdict": verdict, "notes": notes, "created_at": when}
