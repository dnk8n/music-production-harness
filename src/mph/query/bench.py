"""What the query channel answers about benchmarks, ratings and ideas
(spec/components/query-channel.md: REQ-QRY-03, REQ-QRY-05, REQ-QRY-09,
REQ-QRY-10, REQ-QRY-12).

Takes and the ratings that stand come from ``feedback.listing``, as on the
listening page, plus what other branches rendered (REQ-QRY-09). Ratings as
Dean gave them come from the ``rating`` table: one for the whole benchmark is
stored once per take, and given here once, with every take it covered
(REQ-QRY-10). ``work`` is what his ratings and ideas ask agents for:
unresolved takes and open ideas (REQ-QRY-12).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from mph.bench import feedback, ideas, ratings, score
from mph.query import Unanswerable
from mph.query.answers import RECENT, Answer, Library

NOTES_PREVIEW = 300  # characters of feedback in summaries; `ratings` has it all
Wanted = Callable[[str, str], bool]  # (benchmark id, project id) -> asked about
BEFORE = 5  # earlier ratings of an unresolved take that `feedback` shows
AGENT, DEAN = "an agent", "Dean"
STATE = (
    "CASE WHEN stars = 0 AND status IS NULL THEN 'unrated' WHEN status IS NOT NULL THEN status "
    f"WHEN stars >= {ratings.PASS} THEN 'resolved' ELSE 'unresolved' END"
)
GIVEN = f"""
    SELECT * FROM (
        SELECT benchmark, stars, status, scope, created_at, notes, via, verdict,
               group_concat(score_hash) AS hashes, group_concat(take) AS named,
               group_concat(id) AS ids, MAX(id) AS last, {STATE} AS state
        FROM rating
        WHERE (?1 IS NULL OR benchmark = ?1) AND (?3 IS NULL OR created_at >= ?3)
        GROUP BY benchmark, scope, created_at, stars, status, notes, via, verdict,
                 CASE scope WHEN 'all' THEN 0 ELSE id END
    ) WHERE ?2 IS NULL OR state = ?2
"""  # noqa: S608 - only constants go in. A whole-benchmark rating is given once (REQ-QRY-10).
HOW = (
    "A take waiting for an agent needs a change to its score, the code that renders it, or "
    "its sounds: the feedback says what Dean wants, ideally ending in a higher rating. Cite "
    "the rating id in the pull request. A take rendered again since it was rated waits for "
    "Dean to listen and rate it again. An idea is built as a spec item "
    "(spec/components/benchmarks.md) and a score with the idea's id; mph then shows it as "
    "built."
)


def listing(lib: Library, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
    """The listening page's benchmarks, plus what other branches rendered (REQ-QRY-09)."""
    return feedback.listing(lib.renders, conn, elsewhere=True)


def _entry(found: list[dict[str, Any]], benchmark_id: str) -> dict[str, Any]:
    entry = next((b for b in found if b["id"] == benchmark_id), None)
    if entry is None:
        known = ", ".join(sorted(b["id"] for b in found))
        raise Unanswerable(f"no benchmark {benchmark_id!r}; there are {known}")
    return entry


def _clip(found: Answer) -> Answer:
    notes = str(found.get("notes") or "")
    if len(notes) <= NOTES_PREVIEW:
        return found
    return {**found, "notes": notes[:NOTES_PREVIEW] + "…", "notes_clipped": True}


# --- benchmarks and their takes ------------------------------------------------------


def benchmarks(lib: Library, project: str | None) -> Answer:
    with lib.reading_if_any() as conn:
        found = listing(lib, conn)
    projects = sorted({b["project"] for b in found})
    if project:
        found = [b for b in found if b["project"].lower() == project.lower()]
        if not found:
            raise Unanswerable(f"no project {project!r}; projects: {', '.join(projects)}")
    return {"projects": projects, "benchmarks": [_brief(b) for b in found]}


def _brief(bench: dict[str, Any]) -> Answer:
    return {
        "id": bench["id"],
        "title": bench["title"],
        "project": bench["project"],
        "bpm": bench["bpm"],
        "in_checkout": bench["in_checkout"],
        "ratings_for_every_take": _wholes(bench["takes"]),
        "takes": [_take_brief(take) for take in bench["takes"]],
    }


def _take_brief(take: dict[str, Any]) -> Answer:
    made = take["report"] or {}
    return {
        "take": take["name"],
        "title": take["title"],
        "in_checkout": take["in_checkout"],
        "rendered": bool(made),
        "score_hash": made.get("score_hash"),
        "rendered_at": made.get("rendered_at"),
        "engine": made.get("engine"),
        "checkout": made.get("checkout"),
        "measurements_pass": made.get("passed"),
        "failed_measurements": _failed(made) if made else None,
        "audio_on_disk": made.get("on_disk"),
        "state": take["state"],
        "rating": _standing(take),
    }


def _failed(made: Mapping[str, Any]) -> list[Answer]:
    return [
        {key: m.get(key) for key in ("name", "value", "limit", "note")}
        for m in made.get("measurements", [])
        if not m.get("passed")
    ]


def _for_every_take(rating: Mapping[str, Any], made: Mapping[str, Any]) -> bool:
    """A whole-benchmark rating, or one given on a `main` render, which counts the
    same (REQ-FBK-02), rather than the take's own."""
    return bool(rating["scope"] == "all" or rating["score_hash"] != made["score_hash"])


def _stars(rating: Mapping[str, Any]) -> Answer:
    """A rating's stars and what follows from them (REQ-QRY-10)."""
    found: Answer = {
        "stars": rating["stars"],
        "passed": rating["stars"] >= ratings.PASS,
        "status": rating["status"],  # as Dean clicked it; None: the stars decide
        "state": rating["state"],
    }
    if rating.get("via") == "verdict":
        found["from_verdict"] = rating["verdict"]
    return found


def _standing(take: Mapping[str, Any]) -> Answer | None:
    """The rating that stands for a take. A whole-benchmark one leaves its feedback
    to the benchmark, so it's given once (REQ-QRY-10)."""
    rating = take["rating"]
    if not rating:
        return None
    found = {
        **_stars(rating),
        "at": rating["created_at"],
        "rendered_again": rating["rendered_again"],
    }
    if _for_every_take(rating, take["report"]):
        return {**found, "scope": "all"}
    return _clip({"id": rating["id"], **found, "scope": "take", "notes": rating["notes"]})


def _wholes(takes: Sequence[Mapping[str, Any]]) -> list[Answer]:
    """Each whole-benchmark rating that stands for some take, once, with those takes."""
    found: dict[tuple[str, int, str | None, str], Answer] = {}
    for take in takes:
        rating = take["rating"]
        if not rating or not _for_every_take(rating, take["report"]):
            continue
        key = (rating["created_at"], rating["stars"], rating["status"], rating["notes"])
        if key not in found:
            entry = {**_stars(rating), "at": rating["created_at"], "notes": rating["notes"]}
            found[key] = _clip({"ids": [], **entry, "takes": []})
        if rating["id"] not in found[key]["ids"]:  # a `main` rating is one row for all
            found[key]["ids"].append(rating["id"])
        found[key]["takes"].append(take["name"])
    return list(found.values())


def benchmark(lib: Library, benchmark_id: str) -> Answer:
    with lib.reading_if_any() as conn:
        entry = _entry(listing(lib, conn), benchmark_id)
        total, given = _given(conn, lib.renders, benchmark_id, limit=RECENT)
    history = [_history(r) for r in feedback.reports(lib.renders, benchmark_id)]
    answer: Answer = {
        "id": benchmark_id,
        "title": entry["title"],
        "project": entry["project"],
        "bpm": entry["bpm"],
        "in_checkout": entry["in_checkout"],
        "hear": entry["hear"],
        "score_file": score.source(benchmark_id) if entry["in_checkout"] else None,
        "takes": [_take(take) for take in entry["takes"]],
        "renders": {"count": len(history), "newest": history[:RECENT]},
        "ratings": {"count": total, "newest": [_clip(r) for r in given]},
        "next": "report has a render's whole report.json; ratings has feedback in full",
    }
    if not entry["in_checkout"]:
        answer["note"] = (
            f"another branch defines {benchmark_id}: this checkout has no score for it, so its "
            "takes come from its renders, and there is no score file or hear text"
        )
    return answer


def _take(take: Mapping[str, Any]) -> Answer:
    """A take and its newest render in brief: what it used and what failed."""
    made = take["report"]
    newest = made and {
        **_history(made),
        "checkout": made.get("checkout"),
        "kit": [
            {key: entry.get(key) for key in ("voice", "sound", "hash", "gain_db")}
            for entry in made.get("kit", [])
        ],
        "failed_measurements": _failed(made),
        "levels": made.get("levels"),
    }
    return {
        "take": take["name"],
        "title": take["title"],
        "in_checkout": take["in_checkout"],
        "state": take["state"],
        "rating": _standing(take),
        "newest": newest,
    }


def _history(made: Mapping[str, Any]) -> Answer:
    return {
        "score_hash": made.get("score_hash"),
        "take": feedback.variant_of_report(dict(made)),
        "rendered_at": made.get("rendered_at"),
        "engine": made.get("engine"),
        "measurements_pass": made.get("passed"),
        "audio_on_disk": made.get("on_disk"),
    }


def report(lib: Library, benchmark_id: str, take: str | None, score_hash: str | None) -> Answer:
    with lib.reading_if_any() as conn:
        entry = _entry(listing(lib, conn), benchmark_id)
        given = ratings.history(conn, benchmark_id)
    chosen = _pick(lib.renders, entry, take, score_hash)
    return {
        "report": chosen,
        "ratings_on_this_render": [
            r.to_dict() for r in given if r.score_hash == chosen["score_hash"]
        ],
    }


def _pick(
    renders: Path, entry: dict[str, Any], take: str | None, score_hash: str | None
) -> dict[str, Any]:
    benchmark_id = entry["id"]
    found = feedback.reports(renders, benchmark_id)
    if score_hash:
        wanted = [r for r in found if r.get("score_hash") == score_hash]
        missing = f"{benchmark_id} has no render {score_hash}; `benchmark` lists them"
    elif take:
        takes = [t["name"] for t in entry["takes"]]
        if take not in takes:
            raise Unanswerable(f"{benchmark_id} has no take {take!r}; takes: {', '.join(takes)}")
        wanted = [r for r in found if feedback.variant_of_report(r) == take]
        missing = f"{benchmark_id} [{take}] isn't rendered: `mph bench render {benchmark_id}`"
    else:
        raise Unanswerable("name a take (such as renoise) or a score_hash")
    if not wanted:
        raise Unanswerable(missing)
    return wanted[0]  # reports come newest first


# --- ratings as Dean gave them ---------------------------------------------------------


def ratings_given(
    lib: Library, benchmark_id: str | None, state: str | None, since: str | None, limit: int
) -> Answer:
    with lib.reading_if_any() as conn:
        if benchmark_id:
            _known(lib, conn, benchmark_id)
        if conn is None:
            return {"total": 0, "shown": 0, "ratings": [], "note": "no catalogue, no ratings"}
        total, found = _given(
            conn, lib.renders, benchmark_id, limit=limit, state=state, since=since
        )
    return {"total": total, "shown": len(found), "ratings": found}


def _known(lib: Library, conn: sqlite3.Connection | None, benchmark_id: str) -> None:
    """Any benchmark with a score, a render or a rating here."""
    rated = [r[0] for r in conn.execute("SELECT DISTINCT benchmark FROM rating")] if conn else []
    known = {b["id"] for b in listing(lib, conn)} | set(rated)
    if benchmark_id not in known:
        raise Unanswerable(f"no benchmark {benchmark_id!r}; there are {', '.join(sorted(known))}")


def _given(
    conn: sqlite3.Connection | None,
    renders: Path,
    benchmark_id: str | None,
    *,
    limit: int,
    state: str | None = None,
    since: str | None = None,
) -> tuple[int, list[Answer]]:
    """How many ratings Dean gave, and the newest ``limit``, each as given: one for
    a take, or one for the whole benchmark with every take it covered."""
    if conn is None:
        return 0, []
    where = (benchmark_id, state, since)
    total = conn.execute(f"SELECT COUNT(*) FROM ({GIVEN})", where).fetchone()[0]  # noqa: S608
    newest = f"{GIVEN} ORDER BY created_at DESC, last DESC LIMIT ?4"
    rows = conn.execute(newest, (*where, limit)).fetchall()
    takes = _takes_by_render(renders, {r["benchmark"] for r in rows})
    return total, [_as_given(row, takes) for row in rows]


def _as_given(found_row: sqlite3.Row, takes: Mapping[tuple[str, str], str]) -> Answer:
    row = dict(found_row)
    benchmark_id = row["benchmark"]
    hashes = str(row["hashes"]).split(",")
    named = str(row["named"] or "").split(",")
    ids = [int(i) for i in str(row["ids"]).split(",")]
    # The take each render was, from its report, else as stored; None when both are gone.
    stored = dict(zip(hashes, named, strict=False))
    judged = sorted(
        (
            (takes.get((benchmark_id, h)) or stored.get(h) or None, h, i)
            for h, i in zip(hashes, ids, strict=True)
        ),
        key=lambda found: (found[0] or "", found[1]),
    )
    found: Answer = {
        "benchmark": benchmark_id,
        **_stars(row),
        "cleared": row["state"] == ratings.UNRATED,
        "scope": row["scope"],
        "created_at": row["created_at"],
        "notes": row["notes"],
    }
    if row["scope"] == "all":
        return {
            **found,
            "ids": [i for _, _, i in judged],
            "takes": [t for t, _, _ in judged],
            "score_hashes": [h for _, h, _ in judged],
        }
    (take, render_hash, rating_id), *_ = judged
    return {"id": rating_id, **found, "take": take, "score_hash": render_hash}


def _takes_by_render(renders: Path, benchmark_ids: set[str]) -> dict[tuple[str, str], str]:
    return {
        (benchmark_id, str(made.get("score_hash"))): feedback.variant_of_report(made)
        for benchmark_id in sorted(benchmark_ids)
        for made in feedback.reports(renders, benchmark_id)
    }


# --- what the ratings and ideas ask agents for (REQ-QRY-12) ------------------------------


def work(lib: Library, project: str | None, benchmark_id: str | None) -> Answer:
    """Every unresolved take, waiting for an agent first, then every open idea."""
    with lib.reading_if_any() as conn:
        found = listing(lib, conn)
        every = ideas.every(conn)
    known = ideas.known(found, ideas.specced())
    wanted = _narrowed(found, every, known, project, benchmark_id)
    benches = [b for b in found if wanted(b["id"], b["project_id"])]
    queue = [entry for b in benches if (entry := _unresolved(b))]
    queue.sort(key=lambda b: not any(t["waiting_for"] == AGENT for t in b["takes"]))
    takes = [t for b in queue for t in b["takes"]]
    opened = [
        _idea(i, known, every)
        for i in ideas.open_ideas(every, known)
        if wanted(*_place(i, known, every))
    ]
    return {
        "unresolved": {
            "takes": len(takes),
            "waiting_for_an_agent": sum(t["waiting_for"] == AGENT for t in takes),
            "rendered_again_waiting_for_dean": sum(t["waiting_for"] == DEAN for t in takes),
            "benchmarks": queue,
        },
        "to_rate": sum(t["state"] == ratings.UNRATED for b in benches for t in b["takes"]),
        "open_ideas": opened,
        "how": HOW,
    }


def _narrowed(
    found: Sequence[Mapping[str, Any]],
    every: Sequence[ideas.Idea],
    known: ideas.Known,
    project: str | None,
    benchmark_id: str | None,
) -> Wanted:
    """A test of (benchmark id, project id) for what was asked about."""
    projects = {b["project_id"]: b["project"] for b in found}
    projects.update(known.projects)
    projects.update({i.ref: i.title for i in every if i.kind == ideas.PROJECT})
    if benchmark_id:
        benchmark_ids = {b["id"] for b in found} | {i.ref for i in every if i.kind == "benchmark"}
        if benchmark_id not in benchmark_ids:
            there = ", ".join(sorted(benchmark_ids))
            raise Unanswerable(f"no benchmark {benchmark_id!r}; there are {there}")
        return lambda bench, _project: bench == benchmark_id
    if project:
        chosen = next(
            (pid for pid, name in projects.items() if project.lower() in (pid, name.lower())), None
        )
        if chosen is None:
            names = ", ".join(sorted(set(projects.values())))
            raise Unanswerable(f"no project {project!r}; projects: {names}")
        return lambda _bench, project_id: project_id == chosen
    return lambda _bench, _project: True


def _unresolved(bench: Mapping[str, Any]) -> Answer | None:
    takes = [t for t in bench["takes"] if t["state"] == ratings.UNRESOLVED]
    if not takes:
        return None
    entries = [_open_take(t, bench["ratings"]) for t in takes]
    entries.sort(key=lambda t: t["waiting_for"] != AGENT)
    return {
        "id": bench["id"],
        "title": bench["title"],
        "project": bench["project"],
        "in_checkout": bench["in_checkout"],
        "hear": bench["hear"],
        "ratings_for_every_take": _wholes(takes),
        "takes": entries,
    }


def _open_take(take: Mapping[str, Any], given: Sequence[Mapping[str, Any]]) -> Answer:
    made = take["report"]
    standing = take["rating"]
    before = [
        r
        for r in given
        if (r["take"] == take["name"] or r["legacy"])
        and r["id"] != standing["id"]
        and r["created_at"] <= standing["created_at"]
    ]
    return {
        "take": take["name"],
        "waiting_for": DEAN if standing["rendered_again"] else AGENT,
        "state": take["state"],
        "rating": _standing(take),
        "before": [
            _clip(
                {
                    "id": r["id"],
                    **_stars(r),
                    "cleared": r["state"] == ratings.UNRATED,
                    "scope": r["scope"],
                    "at": r["created_at"],
                    "notes": r["notes"],
                }
            )
            for r in before[:BEFORE]
        ],
        "score_hash": made.get("score_hash"),
        "rendered_at": made.get("rendered_at"),
        "engine": made.get("engine"),
        "checkout": made.get("checkout"),
        "failed_measurements": _failed(made),
    }


# --- ideas (REQ-FBK-08) ------------------------------------------------------------------


def _place(idea: ideas.Idea, known: ideas.Known, every: Sequence[ideas.Idea]) -> tuple[str, str]:
    """(benchmark id, project id) an idea belongs to, for narrowing."""
    if idea.kind == ideas.PROJECT:
        return "", idea.ref
    if idea.kind == ideas.BENCHMARK:
        return idea.ref, idea.parent
    above = next((i for i in every if i.kind == ideas.BENCHMARK and i.ref == idea.parent), None)
    return idea.parent, known.benchmarks.get(idea.parent) or (above.parent if above else "")


def _idea(idea: ideas.Idea, known: ideas.Known, every: Sequence[ideas.Idea]) -> Answer:
    opened = {i.ref for i in ideas.open_ideas(every, known)}
    if idea.kind == ideas.PROJECT:
        where = "a new project"
    elif idea.kind == ideas.BENCHMARK:
        named = (i.title for i in every if i.kind == ideas.PROJECT and i.ref == idea.parent)
        name = known.projects.get(idea.parent) or next(named, idea.parent)
        where = f"a new benchmark in {'the project idea ' if idea.parent in opened else ''}{name}"
    else:
        what = "the benchmark idea" if idea.parent in opened else "benchmark"
        where = f"a new take of {what} {idea.parent}"
    return {**idea.to_dict(known), "where": where}


def ideas_given(lib: Library, status: str | None, kind: str | None, limit: int) -> Answer:
    with lib.reading_if_any() as conn:
        found = listing(lib, conn)
        every = ideas.every(conn)
    known = ideas.known(found, ideas.specced())
    chosen = [
        i
        for i in reversed(every)  # newest first
        if (status is None or i.status(known) == status) and (kind is None or i.kind == kind)
    ]
    return {
        "total": len(chosen),
        "shown": min(limit, len(chosen)),
        "ideas": [_idea(i, known, every) for i in chosen[:limit]],
    }
