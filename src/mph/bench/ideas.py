"""Ideas Dean adds on the listening page (spec/components/feedback.md, REQ-FBK-08).

An idea is a project, benchmark or variant Dean wants before agents build it
as spec and score. It has a title, his notes, and an id: one he typed, or
one filled in here. Ideas are rows in the catalogue DB. Whether one is built
isn't stored: it is built once a score here declares its project, benchmark
or variant, or a render from another branch has it (``Known``). A dropped
idea stays, so a filled-in id is never handed out twice.

Ids take the spec's forms: a project id is lower-case words joined by
hyphens; a benchmark id is ``BM-AREA-NN``, with the area its project uses
most and the next number in that area; a variant name is like the declared
ones, from the title.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

KINDS = ("project", "benchmark", "variant")
PROJECT, BENCHMARK, VARIANT = KINDS
OPEN, BUILT, DROPPED = "open", "built", "dropped"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BENCHMARK_ID = re.compile(r"^BM-([A-Z]{2,5})-(\d{2})$")  # the spec's form (REQ-BEN-04)
SPEC_ITEM = re.compile(r"^### (BM-[A-Z]{2,5}-\d{2}):\s*(.*)$")
SECTION = re.compile(r"^## (.+)$")
STANDARD_TAKES = frozenset({"renoise", "supercollider", "synth-kit", "main"})
MAX_TITLE = 120
MAX_NOTES = 4000
MAX_SLUG = 40
LAST_NUMBER = 99
FALLBACK_AREA = "NEW"
CHECKOUT = Path(__file__).resolve().parents[3]  # src/mph/bench/ideas.py -> the repo
SPEC = CHECKOUT / "spec" / "components" / "benchmarks.md"
TRACK_SPECS = CHECKOUT / "spec" / "tracks"  # tracks and their blocks (REQ-TRK-03)
COLUMNS = "id, kind, ref, parent, title, notes, ref_given, created_at, dropped_at, dropped_with"
BELOW = {"project": "benchmark", "benchmark": "variant"}  # the kind of idea that goes in each


class IdeaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Idea:
    id: int
    kind: str
    ref: str  # its id: a project id, a benchmark id or a variant name
    parent: str  # "" for a project; a project id; a benchmark id
    title: str
    notes: str
    ref_given: bool  # Dean typed the id
    created_at: str
    dropped_at: str | None = None
    dropped_with: int | None = None  # the idea Dean dropped, which took this one with it

    def status(self, known: Known) -> str:
        if self.dropped_at:
            return DROPPED
        return BUILT if known.has(self) else OPEN

    def to_dict(self, known: Known) -> dict[str, Any]:
        return {**asdict(self), "status": self.status(known)}


@dataclass(frozen=True, slots=True)
class Known:
    """What exists, for ids to stay clear of and ideas to be built by."""

    projects: Mapping[str, str]  # project id -> name, from scores, renders and the spec
    built_projects: frozenset[str]  # project ids with a score here or a render
    benchmarks: Mapping[str, str]  # benchmark id -> project id, from all three
    takes: Mapping[str, frozenset[str]]  # benchmark id -> takes declared here or rendered
    titles: Mapping[str, str]  # benchmark id -> title

    def has(self, idea: Idea) -> bool:
        """The idea's project, benchmark or variant exists (REQ-FBK-08)."""
        if idea.kind == PROJECT:
            return idea.ref in self.built_projects or slug(idea.title) in self.built_projects
        if idea.kind == BENCHMARK:
            return idea.ref in self.takes
        return idea.ref in self.takes.get(idea.parent, frozenset())


def slug(text: str) -> str:
    """Lower-case words joined by hyphens: "Hats up 3 dB" -> "hats-up-3-db"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:MAX_SLUG].strip("-")


def specced(path: Path = SPEC) -> list[tuple[str, str, str]]:
    """(id, title, project) of every benchmark the spec describes, with the
    tracks and their blocks in ``spec/tracks`` (REQ-TRK-03). Its project is the
    section heading it is written under (REQ-BEN-11)."""
    tracks = sorted(TRACK_SPECS.glob("*.md")) if path == SPEC else []
    return [item for file in (path, *tracks) for item in _specced_in(file)]


def _specced_in(path: Path) -> list[tuple[str, str, str]]:
    try:
        lines = path.read_text("utf-8").splitlines()
    except OSError:  # not run from a checkout
        return []
    section, found = "", []
    for line in lines:
        if heading := SECTION.match(line):
            section = heading.group(1).strip()
        elif item := SPEC_ITEM.match(line):
            found.append((item.group(1), item.group(2).strip(), section))
    return found


def known(listing: Iterable[Mapping[str, Any]], spec: Iterable[tuple[str, str, str]] = ()) -> Known:
    """``listing`` is the listening page's (with other branches' renders, for the
    query channel); ``spec`` is ``specced()``."""
    projects: dict[str, str] = {}
    benchmarks: dict[str, str] = {}
    takes: dict[str, frozenset[str]] = {}
    titles: dict[str, str] = {}
    for bench in listing:
        project_id = slug(str(bench["project"]))
        projects.setdefault(project_id, str(bench["project"]))
        benchmarks[str(bench["id"])] = project_id
        takes[str(bench["id"])] = frozenset(str(t["name"]) for t in bench["takes"])
        titles[str(bench["id"])] = str(bench["title"])
    built = frozenset(projects)
    for benchmark_id, title, section in spec:
        projects.setdefault(slug(section), section)
        benchmarks.setdefault(benchmark_id, slug(section))
        titles.setdefault(benchmark_id, title)
    return Known(projects, built, benchmarks, takes, titles)


# --- reading -------------------------------------------------------------------------


def from_row(row: Sequence[Any]) -> Idea:
    id_, kind, ref, parent, title, notes, ref_given, created_at, dropped_at, dropped_with = row
    return Idea(
        int(id_),
        str(kind),
        str(ref),
        str(parent),
        str(title),
        str(notes),
        bool(ref_given),
        str(created_at),
        dropped_at,
        dropped_with,
    )


def every(conn: sqlite3.Connection | None) -> list[Idea]:
    """Every idea, oldest first."""
    if conn is None:
        return []
    rows = conn.execute(f"SELECT {COLUMNS} FROM idea ORDER BY id").fetchall()  # noqa: S608
    return [from_row(row) for row in rows]


def open_ideas(found: Iterable[Idea], known: Known) -> list[Idea]:
    return [idea for idea in found if idea.status(known) == OPEN]


# --- ids -----------------------------------------------------------------------------


def area_of(project_id: str, known: Known, found: Iterable[Idea]) -> str:
    """The area a project's benchmarks use most, or one from its id."""
    ids = sorted(b for b, p in known.benchmarks.items() if p == project_id)
    ids += [i.ref for i in found if i.kind == BENCHMARK and i.parent == project_id]
    areas = Counter(m.group(1) for b in ids if (m := BENCHMARK_ID.match(b)))
    if areas:
        return areas.most_common(1)[0][0]
    first = re.sub(r"[^A-Z]", "", project_id.split("-", maxsplit=1)[0].upper())[:5]
    whole = re.sub(r"[^A-Z]", "", project_id.upper())[:5]
    return next((a for a in (first, whole) if len(a) >= 2), FALLBACK_AREA)  # noqa: PLR2004


def next_benchmark_id(project_id: str, known: Known, found: Sequence[Idea]) -> str:
    """BM-AREA-NN: the next number in the project's area, counting the spec,
    scores, renders and every idea, dropped ones too."""
    area = area_of(project_id, known, found)
    taken = [*known.benchmarks, *(i.ref for i in found if i.kind == BENCHMARK)]
    numbers = [int(m.group(2)) for b in taken if (m := BENCHMARK_ID.match(b)) and m[1] == area]
    number = max(numbers, default=0) + 1
    if number > LAST_NUMBER:
        raise IdeaError(f"BM-{area} has no numbers left: type an id")
    return f"BM-{area}-{number:02d}"


def _taken_names(
    benchmark_id: str, known: Known, found: Iterable[Idea], *, dropped: bool
) -> set[str]:
    """Names a new variant idea can't have: takes, and variant ideas (dropped ones too
    when filling a name in, so a name is never handed out twice)."""
    names = set(STANDARD_TAKES | known.takes.get(benchmark_id, frozenset()))
    names |= {
        i.ref
        for i in found
        if i.kind == VARIANT and i.parent == benchmark_id and (dropped or not i.dropped_at)
    }
    return names


def variant_name(title: str, taken: set[str]) -> str:
    base = slug(title) or "variant"
    name, count = base, 1
    while name in taken:
        count += 1
        name = f"{base}-{count}"
    return name


def _filled(kind: str, title: str, parent: str, known: Known, found: Sequence[Idea]) -> str:
    if kind == BENCHMARK:
        return next_benchmark_id(parent, known, found)
    if kind == VARIANT:
        return variant_name(title, _taken_names(parent, known, found, dropped=True))
    ref = slug(title)
    if not ref:
        raise IdeaError("give the project a name with letters or numbers, or type an id")
    return _free_project(ref, known, found)


def _free_project(ref: str, known: Known, found: Iterable[Idea]) -> str:
    opened = {i.ref for i in found if i.kind == PROJECT and not i.dropped_at}
    if ref in known.projects or ref in opened:
        raise IdeaError(f"there is already a project {ref!r}: type another id")
    return ref


def _typed(kind: str, ref: str, parent: str, known: Known, found: Sequence[Idea]) -> str:
    """A typed id, if it has the right form and is free (REQ-FBK-08)."""
    if kind == PROJECT:
        _check_form(ref, SLUG, "lower-case words joined by hyphens, like tribal-percussion")
        return _free_project(ref, known, found)
    if kind == VARIANT:
        _check_form(ref, SLUG, "lower-case words joined by hyphens, like hats-up")
        if ref in _taken_names(parent, known, found, dropped=False):
            raise IdeaError(f"{parent} already has a take or idea called {ref!r}")
        return ref
    _check_form(ref, BENCHMARK_ID, "like BM-DRUM-01: BM, 2 to 5 capital letters, 2 digits")
    opened = {i.ref for i in found if i.kind == BENCHMARK and not i.dropped_at}
    if ref in known.benchmarks or ref in opened:
        raise IdeaError(f"{ref} is taken: leave the id empty for the next free one")
    return ref


def _check_form(ref: str, form: re.Pattern[str], example: str) -> None:
    if len(ref) > MAX_SLUG or not form.match(ref):
        raise IdeaError(f"an id here looks {example}")


def _parent(kind: str, parent: str, known: Known, found: Iterable[Idea]) -> str:
    """Where the idea goes: nowhere for a project; a project, or a project idea, for
    a benchmark; a benchmark, or a benchmark idea, for a variant."""
    if kind == PROJECT:
        return ""
    above = PROJECT if kind == BENCHMARK else BENCHMARK
    existing = known.projects if kind == BENCHMARK else known.benchmarks
    opened = {i.ref for i in found if i.kind == above and not i.dropped_at}
    if parent not in existing and parent not in opened:
        raise IdeaError(f"no {above} {parent!r} to add this {kind} idea to")
    return parent


# --- writing -------------------------------------------------------------------------


def add(
    conn: sqlite3.Connection, payload: Mapping[str, object], known: Known, *, when: str
) -> Idea:
    """Store a new idea from what the page sent: kind, title, notes, an optional
    id (``ref``) and, for benchmarks and variants, where it goes (``parent``)."""
    kind = str(payload.get("kind", ""))
    if kind not in KINDS:
        raise IdeaError("kind must be project, benchmark or variant")
    title = " ".join(str(payload.get("title", "")).split())
    notes = str(payload.get("notes", "")).strip()
    if not title or len(title) > MAX_TITLE:
        raise IdeaError(f"give it a title of up to {MAX_TITLE} characters")
    if len(notes) > MAX_NOTES:
        raise IdeaError(f"notes are limited to {MAX_NOTES} characters")
    found = every(conn)
    parent = _parent(kind, str(payload.get("parent", "")).strip(), known, found)
    typed = str(payload.get("ref", "")).strip()
    if typed:
        ref = _typed(kind, typed, parent, known, found)
    else:
        ref = _filled(kind, title, parent, known, found)
    with conn:
        cursor = conn.execute(
            "INSERT INTO idea (kind, ref, parent, title, notes, ref_given, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, ref, parent, title, notes, int(bool(typed)), when),
        )
    return Idea(int(cursor.lastrowid or 0), kind, ref, parent, title, notes, bool(typed), when)


def _family(idea: Idea, found: Sequence[Idea]) -> list[Idea]:
    """The idea and the ideas under it in the same state: all open, to drop, or
    all dropped by the same drop, to bring back."""
    family, below = [idea], [idea]
    while below:
        above = below.pop()
        children = [
            i
            for i in found
            if i.parent == above.ref
            and i.kind == BELOW.get(above.kind)
            and (i.dropped_with == idea.dropped_with if idea.dropped_at else not i.dropped_at)
        ]
        family += children
        below += children
    return family


def drop(conn: sqlite3.Connection, idea_id: int, *, when: str, undo: bool = False) -> list[Idea]:
    """Drop an open idea and the open ideas under it, or with ``undo`` bring back
    those dropped with it. Returns the ideas changed."""
    found = every(conn)
    idea = next((i for i in found if i.id == idea_id), None)
    if idea is None:
        raise IdeaError(f"no idea {idea_id}")
    if bool(idea.dropped_at) != undo:
        raise IdeaError("that idea is open already" if undo else "that idea is dropped already")
    above = [i for i in found if i.ref == idea.parent and BELOW.get(i.kind) == idea.kind]
    if undo and above and all(i.dropped_at for i in above):  # it would have nowhere to go
        raise IdeaError(f"{idea.parent} is dropped: bring it back first")
    family = _family(idea, found)
    changes = [(None, None, i.id) if undo else (when, idea.id, i.id) for i in family]
    try:
        with conn:
            conn.executemany(
                "UPDATE idea SET dropped_at = ?, dropped_with = ? WHERE id = ?", changes
            )
    except sqlite3.IntegrityError as exc:  # an open idea took its id meanwhile
        raise IdeaError(f"another open idea is called {idea.ref} now") from exc
    return family


# --- what the page and the query channel show ------------------------------------------


def projects(known: Known, found: Sequence[Idea], elsewhere: str) -> list[dict[str, Any]]:
    """Every project a benchmark idea can go in: those with scores, those only in
    the spec, and project ideas, with the id a new benchmark idea there would get
    and the spec's benchmarks that have no score yet."""
    rows: dict[str, dict[str, Any]] = {}
    for project_id, name in known.projects.items():
        if name != elsewhere:
            made = "scores" if project_id in known.built_projects else "spec"
            rows[project_id] = {"id": project_id, "name": name, "from": made}
    for idea in open_ideas(found, known):
        if idea.kind == PROJECT:
            rows[idea.ref] = {"id": idea.ref, "name": idea.title, "from": "idea", "idea": idea.id}
    for project_id, row in rows.items():
        row["next_benchmark"] = _suggest(project_id, known, found)
        row["specced"] = [
            {"id": b, "title": known.titles.get(b, "")}
            for b in sorted(known.benchmarks)
            if known.benchmarks[b] == project_id and b not in known.takes
        ]
    return sorted(rows.values(), key=lambda r: str(r["name"]).lower())


def _suggest(project_id: str, known: Known, found: Sequence[Idea]) -> str | None:
    try:
        return next_benchmark_id(project_id, known, found)
    except IdeaError:
        return None
