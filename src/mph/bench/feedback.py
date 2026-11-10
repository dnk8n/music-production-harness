"""Data behind the listening page (spec/components/feedback.md).

The page shows each benchmark's takes, all equal (REQ-BEN-07): what Dean
should hear, each take's measurements and delivered file for A/B, and the
verdicts given so far. Verdicts are stored in the catalogue DB against the
render's score hash, so each one points at exactly what was heard; a verdict
for the whole benchmark is stored once per take (REQ-FBK-02).
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
    """Every readable report for a benchmark, newest first. ``on_disk`` says
    whether its delivered audio is still there, or was pruned (REQ-RET-02)."""
    found = []
    for path in (renders / benchmark_id).glob("*/report.json"):
        try:
            report = json.loads(path.read_text("utf-8"))
            delivery = str(report["files"]["delivery"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        report["on_disk"] = (path.parent / delivery).is_file()
        found.append(report)
    return sorted(found, key=lambda r: str(r.get("rendered_at", "")), reverse=True)


def verdicts(conn: sqlite3.Connection | None, benchmark_id: str) -> list[dict[str, str]]:
    """Every verdict on the benchmark, newest first."""
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT score_hash, verdict, notes, created_at, scope FROM verdict WHERE benchmark = ? "
        "ORDER BY created_at DESC, id DESC",
        (benchmark_id,),
    ).fetchall()
    keys = ("score_hash", "verdict", "notes", "created_at", "scope")
    return [dict(zip(keys, map(str, row), strict=True)) for row in rows]


def variant_of_report(report: dict[str, Any]) -> str:
    return str(report.get("variant") or score.LEGACY_MAIN)  # renders before variants existed


def latest(renders: Path, benchmark_id: str, variant: str) -> dict[str, Any] | None:
    found = (r for r in reports(renders, benchmark_id) if variant_of_report(r) == variant)
    return next(found, None)


def newest_takes(renders: Path, benchmark_id: str) -> list[dict[str, Any]]:
    """The newest report of each take the benchmark has now, in take order."""
    newest: dict[str, dict[str, Any]] = {}
    for report in reports(renders, benchmark_id):  # newest first, so the first of each wins
        newest.setdefault(variant_of_report(report), report)
    takes = score.all_variants(score.load(benchmark_id))
    return [newest[v.name] for v in takes if v.name in newest]


def effective(
    report: dict[str, Any], found: list[dict[str, str]], legacy: frozenset[str]
) -> dict[str, str] | None:
    """The verdict that stands for one take (REQ-FBK-02), from ``found`` (newest
    first): its own, else the newest whole-benchmark one, else one given on a
    ``main`` render (in ``legacy``) after this take was rendered."""
    own = [v for v in found if v["score_hash"] == report["score_hash"]]
    for scope in ("take", "all"):
        if mine := next((v for v in own if v["scope"] == scope), None):
            return mine
    rendered = str(report.get("rendered_at", ""))
    return next(
        (v for v in found if v["score_hash"] in legacy and v["created_at"] >= rendered), None
    )


def listing(renders: Path, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
    out = []
    for benchmark_id in score.available():
        loaded = score.load(benchmark_id)
        found = reports(renders, benchmark_id)
        given = verdicts(conn, benchmark_id)
        legacy = frozenset(
            str(r["score_hash"]) for r in found if variant_of_report(r) == score.LEGACY_MAIN
        )
        newest: dict[str, dict[str, Any]] = {}
        for report in found:  # newest first, so the first of each variant wins
            newest.setdefault(variant_of_report(report), report)
        takes = []
        for take in score.all_variants(loaded):
            made = newest.get(take.name)
            takes.append(
                {
                    "name": take.name,
                    "title": take.title,
                    "report": made,
                    "verdict": effective(made, given, legacy) if made else None,
                }
            )
        out.append(
            {
                "id": benchmark_id,
                "title": loaded.title,
                "hear": loaded.hear,
                "bpm": loaded.bpm,
                "project": loaded.project,
                "takes": takes,
                "verdicts": [{**v, "legacy": v["score_hash"] in legacy} for v in given],
            }
        )
    return sorted(out, key=lambda b: (b["project"], b["id"]))


def _report(renders: Path, benchmark_id: str, render_hash: str) -> dict[str, Any] | None:
    if benchmark_id not in score.available() or not RENDER_HASH.match(render_hash):
        return None
    try:
        found: dict[str, Any] = json.loads(
            (renders / benchmark_id / render_hash / "report.json").read_text("utf-8")
        )
    except (OSError, ValueError):
        return None
    return found


def playable(renders: Path, benchmark_id: str, render_hash: str, name: str) -> Path | None:
    """The file to serve, if the render's own report lists it; otherwise None."""
    report = _report(renders, benchmark_id, render_hash)
    if report is None:
        return None
    files = report["files"]
    listed = {
        files["delivery"],
        *files.get("stems", []),
        *(v["file"] for v in report.get("variants", [])),
    }
    if name not in listed:
        return None
    path = renders / benchmark_id / render_hash / name
    return path if path.is_file() else None


def project(renders: Path, benchmark_id: str, render_hash: str) -> Path | None:
    """The render's Renoise song, if its report lists one (REQ-REN-07)."""
    report = _report(renders, benchmark_id, render_hash)
    name = report["files"].get("project") if report else None
    if not name:
        return None
    path = renders / benchmark_id / render_hash / str(name)
    return path if path.is_file() and path.suffix == ".xrns" else None


def record(conn: sqlite3.Connection, renders: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Store a verdict for one take (its ``score_hash``), or with ``scope: "all"``
    for every take of the benchmark rendered now (REQ-FBK-02)."""
    benchmark_id = str(payload.get("id", ""))
    scope = str(payload.get("scope", "take"))
    verdict = str(payload.get("verdict", ""))
    notes = str(payload.get("notes", "")).strip()
    if verdict not in VERDICTS:
        raise FeedbackError(f"verdict must be one of {', '.join(VERDICTS)}")
    if len(notes) > MAX_NOTES:
        raise FeedbackError(f"notes are limited to {MAX_NOTES} characters")
    if scope == "all":
        if benchmark_id not in score.available():
            raise FeedbackError(f"no benchmark called {benchmark_id!r}")
        hashes = [str(r["score_hash"]) for r in newest_takes(renders, benchmark_id)]
        if not hashes:
            raise FeedbackError("no take has been rendered yet: render one first")
    elif scope == "take":
        render_hash = str(payload.get("score_hash", ""))
        if not any(r.get("score_hash") == render_hash for r in reports(renders, benchmark_id)):
            raise FeedbackError("no such render: render it again and reload the page")
        hashes = [render_hash]
    else:
        raise FeedbackError('scope must be "take" or "all"')
    when = utc_now()
    with conn:
        conn.executemany(
            "INSERT INTO verdict (benchmark, score_hash, verdict, notes, created_at, scope) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(benchmark_id, h, verdict, notes, when, scope) for h in hashes],
        )
    return {
        "score_hashes": hashes,
        "verdict": verdict,
        "notes": notes,
        "created_at": when,
        "scope": scope,
    }
