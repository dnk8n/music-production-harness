"""Data behind the listening page (spec/components/feedback.md).

The page shows each benchmark's takes, all equal (REQ-BEN-07): what Dean
should hear, each take's measurements and delivered file for A/B, and the
rating that stands for it (``ratings``: stars, resolved or unresolved, and
whether it was rendered again since). Ratings are stored in the catalogue DB
against the render's score hash, so each one points at exactly what was
heard; a rating for the whole benchmark is stored once per take
(REQ-FBK-02). Only files listed in a render's own report can be served
(REQ-FBK-05). The query channel reads the same listing, plus what other
branches rendered (REQ-QRY-09).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from mph.bench import ideas, ratings, score
from mph.bench.ratings import Rating
from mph.catalogue.scanner import utc_now

RENDER_HASH = re.compile(r"^[0-9a-f]{16}$")
BENCHMARK_ID = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")  # safe as a folder name
ELSEWHERE = "Not in this checkout"  # the project of a benchmark only another branch defines
NOT_RENDERED = "not rendered"


class FeedbackError(ValueError):
    pass


def reports(renders: Path, benchmark_id: str) -> list[dict[str, Any]]:
    """Every readable report for a benchmark, newest first. ``on_disk`` says
    whether its delivered audio is still there, or was pruned (REQ-RET-02)."""
    if not BENCHMARK_ID.match(benchmark_id):
        return []
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


def rendered(renders: Path) -> list[str]:
    """Every benchmark with a readable render here, whichever branch defined it."""
    if not renders.is_dir():
        return []
    return sorted(p.name for p in renders.iterdir() if p.is_dir() and reports(renders, p.name))


def variant_of_report(report: dict[str, Any]) -> str:
    return str(report.get("variant") or score.LEGACY_MAIN)  # renders before variants existed


def latest(renders: Path, benchmark_id: str, variant: str) -> dict[str, Any] | None:
    found = (r for r in reports(renders, benchmark_id) if variant_of_report(r) == variant)
    return next(found, None)


def newest_by_variant(found: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The newest of ``found`` (newest first) for each variant it has."""
    newest: dict[str, dict[str, Any]] = {}
    for report in found:  # newest first, so the first of each wins
        newest.setdefault(variant_of_report(report), report)
    return newest


def legacy_hashes(found: list[dict[str, Any]]) -> frozenset[str]:
    """Renders from before takes were equal: their ratings count for every take."""
    return frozenset(
        str(r["score_hash"]) for r in found if variant_of_report(r) == score.LEGACY_MAIN
    )


def standing(
    renders: Path, conn: sqlite3.Connection | None, benchmark_id: str, variant: str
) -> tuple[dict[str, Any] | None, Rating | None]:
    """A take's newest render and the rating that stands for it (REQ-BEN-14)."""
    found = reports(renders, benchmark_id)
    made = newest_by_variant(found).get(variant)
    if made is None:
        return None, None
    return made, ratings.standing(made, ratings.history(conn, benchmark_id), legacy_hashes(found))


def newest_takes(renders: Path, benchmark_id: str) -> list[dict[str, Any]]:
    """The newest report of each take the benchmark has now, in take order."""
    newest = newest_by_variant(reports(renders, benchmark_id))
    takes = score.all_variants(score.load(benchmark_id))
    return [newest[v.name] for v in takes if v.name in newest]


def listing(
    renders: Path, conn: sqlite3.Connection | None, *, elsewhere: bool = False
) -> list[dict[str, Any]]:
    """Every benchmark with its takes and ratings, by project. With ``elsewhere``
    (for the query channel, REQ-QRY-09) it also has what was rendered from other
    branches: takes this checkout's scores don't declare, and benchmarks it has
    no score for, read from their reports and listed last."""
    here = score.available()
    out = [_listed(renders, conn, b, score.load(b), elsewhere=elsewhere) for b in here]
    if elsewhere:
        others = [b for b in rendered(renders) if b not in here]
        out += [_listed(renders, conn, b, None, elsewhere=True) for b in others]
    return sorted(out, key=lambda b: (not b["in_checkout"], b["project"], b["id"]))


def _listed(
    renders: Path,
    conn: sqlite3.Connection | None,
    benchmark_id: str,
    loaded: score.Score | None,
    *,
    elsewhere: bool,
) -> dict[str, Any]:
    found = reports(renders, benchmark_id)
    given = ratings.history(conn, benchmark_id)
    legacy = legacy_hashes(found)
    newest = newest_by_variant(found)
    declared = [(v.name, v.title) for v in score.all_variants(loaded)] if loaded else []
    takes = [_take(name, title, newest.get(name), given, legacy) for name, title in declared]
    known = {name for name, _ in declared} | {score.LEGACY_MAIN}
    for name, made in sorted(newest.items()) if elsewhere else ():
        if name not in known:  # rendered from another branch's score
            title = str(made.get("variant_title") or name)
            takes.append({**_take(name, title, made, given, legacy), "in_checkout": False})
    newest_report = found[0] if found else {}
    project = loaded.project if loaded else ELSEWHERE
    take_of = {str(r.get("score_hash")): variant_of_report(r) for r in found}
    return {
        "id": benchmark_id,
        "title": loaded.title if loaded else str(newest_report.get("title", "")),
        "hear": loaded.hear if loaded else "",
        "bpm": loaded.bpm if loaded else newest_report.get("bpm"),
        "project": project,
        "project_id": ideas.slug(project),
        "in_checkout": loaded is not None,
        "takes": takes,
        "ratings": [_given(r, legacy, take_of) for r in given],
    }


def _given(rating: Rating, legacy: frozenset[str], take_of: dict[str, str]) -> dict[str, Any]:
    """A rating in the history. One carried over from a verdict doesn't name its
    take, so the report of the render it judged does, while that report is kept."""
    found = rating.to_dict()
    found["take"] = rating.take or take_of.get(rating.score_hash, "")
    found["legacy"] = rating.score_hash in legacy
    return found


def _take(
    name: str,
    title: str,
    made: dict[str, Any] | None,
    given: list[Rating],
    legacy: frozenset[str],
) -> dict[str, Any]:
    stands = ratings.standing(made, given, legacy) if made else None
    return {
        "name": name,
        "title": title,
        "in_checkout": True,
        "report": made,
        "rating": _rating(stands, made),
        "state": ratings.state_of(stands) if made else NOT_RENDERED,
    }


def _rating(stands: Rating | None, made: dict[str, Any] | None) -> dict[str, Any] | None:
    """The rating that stands, and whether the take was rendered again since (REQ-FBK-06)."""
    if stands is None or made is None:
        return None
    return {**stands.to_dict(), "rendered_again": ratings.rendered_again(stands, made)}


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


def rate(conn: sqlite3.Connection, renders: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Store a rating for one take (its ``score_hash``), or with ``scope: "all"``
    for every take of the benchmark rendered now (REQ-FBK-02)."""
    try:
        asked = ratings.asked(payload)
    except ratings.RatingError as exc:
        raise FeedbackError(str(exc)) from exc
    found = reports(renders, asked.benchmark)
    if asked.scope == "all":
        if asked.benchmark not in score.available():
            raise FeedbackError(f"no benchmark called {asked.benchmark!r}")
        rated = newest_takes(renders, asked.benchmark)
        if not rated:
            raise FeedbackError("no take has been rendered yet: render one first")
    else:
        rated = [r for r in found if r.get("score_hash") == asked.score_hash][:1]
        if not rated:
            raise FeedbackError("no such render: render it again and reload the page")
    take_of = {str(r["score_hash"]): variant_of_report(r) for r in rated}
    clearing = asked.scope == "take" and _has_own(conn, asked)
    try:
        return ratings.save(conn, asked, rated, take_of=take_of, when=utc_now(), clearing=clearing)
    except ratings.RatingError as exc:
        raise FeedbackError(str(exc)) from exc


def idea_board(renders: Path, conn: sqlite3.Connection | None) -> dict[str, Any]:
    """Every idea with its status, and every project a benchmark idea can go in
    (REQ-FBK-08). An idea is built once a score here or a render from another
    branch has it, so other branches' renders count."""
    known = _known(renders, conn)
    found = ideas.every(conn)
    return {
        "ideas": [idea.to_dict(known) for idea in found],
        "projects": ideas.projects(known, found, ELSEWHERE),
    }


def add_idea(conn: sqlite3.Connection, renders: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Store an idea for a project, benchmark or variant (REQ-FBK-08)."""
    known = _known(renders, conn)
    try:
        return ideas.add(conn, payload, known, when=utc_now()).to_dict(known)
    except ideas.IdeaError as exc:
        raise FeedbackError(str(exc)) from exc


def drop_idea(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop an open idea and the open ideas under it, or with ``undo`` bring them back."""
    idea_id = payload.get("idea")
    if not isinstance(idea_id, int) or isinstance(idea_id, bool):
        raise FeedbackError("say which idea, by its number")
    undo = payload.get("undo") is True
    try:
        changed = ideas.drop(conn, idea_id, when=utc_now(), undo=undo)
    except ideas.IdeaError as exc:
        raise FeedbackError(str(exc)) from exc
    return {"undo": undo, "ideas": [i.id for i in changed], "refs": [i.ref for i in changed]}


def _known(renders: Path, conn: sqlite3.Connection | None) -> ideas.Known:
    return ideas.known(listing(renders, conn, elsewhere=True), ideas.specced())


def _has_own(conn: sqlite3.Connection, asked: ratings.Asked) -> bool:
    """The render has a rating of its own that stands, for 0 stars to clear."""
    own = [
        r
        for r in ratings.history(conn, asked.benchmark)
        if r.score_hash == asked.score_hash and r.scope == "take"
    ]
    return bool(own) and not own[0].cleared
