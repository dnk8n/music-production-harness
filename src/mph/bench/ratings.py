"""Dean's ratings of benchmark takes (spec/components/feedback.md: REQ-FBK-02,
REQ-FBK-06, REQ-FBK-07; ADR-0012).

A rating gives a take 0 to 5 stars (3 or more is a pass), the status Dean
clicked (resolved, unresolved, or none, when the stars decide), and his
feedback. Each save is a row in the catalogue DB, tied to the render it
judged by its score hash, with the take and that render's ``inputs``. The
newest stands, and the rest are its history. A rating for the whole
benchmark is stored once for each take rendered at the time.

``standing`` picks the rating that stands for a take, and ``Rating.state``
says what it means: resolved, unresolved or unrated. The render rule
(``rerender``), the page and the query channel all read them from here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

PASS = 3  # a rating with this many stars or more is a pass
MOST = 5
RESOLVED, UNRESOLVED, UNRATED = "resolved", "unresolved", "unrated"
STATUSES = (RESOLVED, UNRESOLVED)
SCOPES = ("take", "all")
MAX_NOTES = 4000
NO_RATING = (
    "0 stars with neither resolved nor unresolved is no rating: "
    "pick some stars, or click resolved or unresolved"
)
COLUMNS = (
    "id, benchmark, score_hash, scope, stars, status, notes, created_at, take, inputs, via, verdict"
)


class RatingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rating:
    id: int
    benchmark: str
    score_hash: str
    scope: str  # "take", or "all": one of a set given to the whole benchmark
    stars: int
    status: str | None  # as Dean clicked it; None: the stars decide
    notes: str
    created_at: str
    take: str = ""  # the take of the render it judged ("" when carried over)
    inputs: str = ""  # what shaped that render ("" when unknown)
    via: str = "page"  # "page", or "verdict": carried over from one (REQ-FBK-07)
    verdict: str | None = None  # the verdict it was carried over from

    @property
    def cleared(self) -> bool:
        """0 stars and no status is no rating: saved on a take, it clears the take's own."""
        return self.stars == 0 and self.status is None

    @property
    def passed(self) -> bool:
        return self.stars >= PASS

    @property
    def state(self) -> str:
        """Resolved, unresolved or unrated (REQ-FBK-06)."""
        if self.cleared:
            return UNRATED
        if self.status:
            return self.status
        return RESOLVED if self.passed else UNRESOLVED

    @property
    def status_from(self) -> str:
        """Who set the state: "you" (Dean clicked it), "stars", or "" when unrated."""
        if self.cleared:
            return ""
        return "you" if self.status else "stars"

    def to_dict(self) -> dict[str, Any]:
        found = asdict(self)
        found.update(passed=self.passed, state=self.state, status_from=self.status_from)
        return found


def state_of(rating: Rating | None) -> str:
    return UNRATED if rating is None else rating.state


@dataclass(frozen=True, slots=True)
class Asked:
    """A rating as the page sends it, checked."""

    benchmark: str
    scope: str
    score_hash: str  # the render, for one take; "" for the whole benchmark
    stars: int
    status: str | None
    notes: str

    @property
    def cleared(self) -> bool:
        return self.stars == 0 and self.status is None


def asked(payload: Mapping[str, object]) -> Asked:
    """Check what the page sent (REQ-FBK-02)."""
    stars = payload.get("stars")
    if not isinstance(stars, int) or isinstance(stars, bool) or not 0 <= stars <= MOST:
        raise RatingError(f"stars must be a whole number from 0 to {MOST}")
    status = payload.get("status") or None
    if status is not None and status not in STATUSES:
        raise RatingError("status must be resolved, unresolved, or left out")
    scope = str(payload.get("scope", "take"))
    if scope not in SCOPES:
        raise RatingError('scope must be "take" or "all"')
    notes = str(payload.get("notes", "")).strip()
    if len(notes) > MAX_NOTES:
        raise RatingError(f"feedback is limited to {MAX_NOTES} characters")
    score_hash = str(payload.get("score_hash", "")) if scope == "take" else ""
    return Asked(str(payload.get("id", "")), scope, score_hash, stars, status, notes)


def history(conn: sqlite3.Connection | None, benchmark_id: str) -> list[Rating]:
    """Every rating on the benchmark, newest first."""
    if conn is None:
        return []
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM rating WHERE benchmark = ? "  # noqa: S608 - fixed columns
        "ORDER BY created_at DESC, id DESC",
        (benchmark_id,),
    ).fetchall()
    return [from_row(row) for row in rows]


def from_row(row: Sequence[Any]) -> Rating:
    (id_, benchmark, score_hash, scope, stars, status, notes, at, take, inputs, via, verdict) = row
    return Rating(
        int(id_),
        str(benchmark),
        str(score_hash),
        str(scope),
        int(stars),
        status,
        str(notes),
        str(at),
        str(take),
        str(inputs),
        str(via),
        verdict,
    )


def standing(
    report: Mapping[str, Any], found: Sequence[Rating], legacy: frozenset[str]
) -> Rating | None:
    """The rating that stands for one take's newest render (REQ-FBK-02), from
    ``found`` (newest first): its own, else the newest whole-benchmark one, else
    one given on a ``main`` render (in ``legacy``) after this take was rendered.
    A cleared rating of its own leaves the take to the others."""
    own = [r for r in found if r.score_hash == report["score_hash"]]
    mine = next((r for r in own if r.scope == "take"), None)
    if mine is not None and not mine.cleared:
        return mine
    if whole := next((r for r in own if r.scope == "all"), None):
        return whole
    rendered = str(report.get("rendered_at", ""))
    return next((r for r in found if r.score_hash in legacy and r.created_at >= rendered), None)


def rendered_again(rating: Rating, report: Mapping[str, Any]) -> bool:
    """The take's newest render isn't what was rated (REQ-FBK-06): its inputs
    differ, or, when the rating doesn't know them, it was rendered later."""
    now = str(report.get("inputs") or "")
    if rating.inputs and now:
        return rating.inputs != now
    return str(report.get("rendered_at", "")) > rating.created_at


def save(
    conn: sqlite3.Connection,
    asked: Asked,
    rated: Sequence[Mapping[str, Any]],
    *,
    take_of: Mapping[str, str],
    when: str,
    clearing: bool = False,
) -> dict[str, Any]:
    """Store one rating for each render in ``rated``: the one take asked about, or
    every take's newest render for the whole benchmark. ``take_of`` names each
    render's take. ``clearing`` says the take has a rating of its own to clear."""
    if asked.cleared and not clearing:
        raise RatingError(NO_RATING)
    rows = [
        (
            asked.benchmark,
            str(report["score_hash"]),
            asked.scope,
            asked.stars,
            asked.status,
            asked.notes,
            when,
            take_of.get(str(report["score_hash"]), ""),
            str(report.get("inputs") or ""),
        )
        for report in rated
    ]
    with conn:
        conn.executemany(
            "INSERT INTO rating (benchmark, score_hash, scope, stars, status, notes, "
            "created_at, take, inputs, via) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'page')",
            rows,
        )
    saved = Rating(0, asked.benchmark, "", asked.scope, asked.stars, asked.status, "", when)
    return {
        "score_hashes": [row[1] for row in rows],
        "stars": asked.stars,
        "status": asked.status,
        "state": saved.state,
        "notes": asked.notes,
        "created_at": when,
        "scope": asked.scope,
    }
