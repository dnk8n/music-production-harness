"""What the query channel answers (spec/components/query-channel.md, REQ-QRY-03).

Every answer is plain JSON-ready data built from the same functions as the
command or page that shows the same thing: ``search.search`` for sounds,
``feedback.listing`` for takes and the verdicts that stand, ``doctor.run`` for
health. The listing here also has what other branches rendered (REQ-QRY-09).
The catalogue is only opened through ``readonly`` (REQ-QRY-02), and nothing
here opens audio.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mph import __version__, doctor, retention
from mph.bench import feedback, score
from mph.catalogue import db, provenance, search, stats
from mph.components import Context, Stage
from mph.config import Config
from mph.query import Unanswerable, readonly
from mph.secrets import SecretStore

Answer = dict[str, Any]
SQL_TIMEOUT_S = 5.0
RECENT = 20  # renders and verdicts listed per benchmark; `verdicts` pages through the rest
NOTES_PREVIEW = 300  # characters of a verdict's notes in summaries; `verdicts` has them all
HEX = re.compile(r"^[0-9a-fA-F]{6,64}$")
ROLE_COUNTS = (
    "strongest counts each sound once, under its most confident role, as `mph catalogue stats` "
    "does. labelled counts every sound that has the role among its labels, as `labels` does, "
    "so a sound with two roles counts under both."
)
ROLE_VALUES = (
    "sounds counts every sound labelled with the role. strongest counts only the sounds whose "
    "most confident role it is, as overview and `mph catalogue stats` do."
)
DOWNLOAD_STORE = (
    "a download store: `mph catalogue fetch` fills it, so it needs no config; each sound has "
    "its own licence (`sound` shows it)"
)


@dataclass(frozen=True, slots=True)
class Library:
    """Where answers come from. ``ctx`` and ``store`` are for doctor only: it
    checks whether each credential is set, never what it is."""

    cfg: Config
    ctx: Context
    store: SecretStore | None = None

    @property
    def catalogue(self) -> Path:
        return self.cfg.data_dir / db.FILENAME

    @property
    def renders(self) -> Path:
        return self.cfg.data_dir / retention.RENDERS

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        with closing(readonly.connect(self.catalogue)) as conn:
            yield conn

    @contextmanager
    def reading_if_any(self) -> Iterator[sqlite3.Connection | None]:
        """The catalogue, or None before the first scan: benchmarks don't need it."""
        if not self.catalogue.is_file():
            yield None
            return
        with self.reading() as conn:
            yield conn


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


# --- overview ------------------------------------------------------------------------


def overview(lib: Library) -> Answer:
    cfg = lib.cfg
    with lib.reading_if_any() as conn:
        catalogue = _catalogue(conn) if conn else {"scanned": False, "next": "mph catalogue scan"}
        sources = _sources(cfg, conn)
        listing = _listing(lib, conn)
    tidy = retention.plan(cfg.data_dir, cfg.retention)
    return {
        "mph": __version__,
        "config_files": [str(p) for p in cfg.loaded_from],
        "data_dir": str(cfg.data_dir),
        "sources": sources,
        "audio": asdict(cfg.audio),
        "master": asdict(cfg.master),
        "catalogue": catalogue,
        "benchmarks": _projects(listing),
        "renders": {
            "disk_used": retention.human(tidy.renders_size),
            "prune_would_free": retention.human(tidy.size),
        },
        "llm_providers": list(cfg.llm_providers),
    }


def _catalogue(conn: sqlite3.Connection) -> Answer:
    summary = stats.summarise(conn)
    return {
        "scanned": True,
        "sounds": summary.assets,
        "files": summary.files,
        "documents": summary.documents,
        "gigabytes": summary.gigabytes,
        "hours": summary.hours,
        "unreadable": summary.probe_errors,
        "analysed": _count(
            conn, "SELECT COUNT(DISTINCT asset_hash) FROM analysis WHERE error IS NULL"
        ),
        "downloads": _count(conn, "SELECT COUNT(*) FROM download"),
        "derivatives": _count(conn, "SELECT COUNT(*) FROM derivation"),
        "stashed": _count(conn, "SELECT COUNT(*) FROM stash"),
        "verdicts": _count(conn, "SELECT COUNT(*) FROM verdict"),
        "top_packs": dict(summary.by_pack[:15]),
        "roles": _roles(conn, summary.by_role),
        "roles_counted": ROLE_COUNTS,
        "formats": dict(summary.by_format[:10]),
    }


def _roles(conn: sqlite3.Connection, strongest: list[tuple[str, int]]) -> Answer:
    """Both ways of counting roles, side by side, most common first."""
    best = dict(strongest)
    labelled = dict(
        conn.execute(
            "SELECT value, COUNT(DISTINCT asset_hash) FROM tag WHERE name = 'role' GROUP BY value"
        ).fetchall()
    )
    order = sorted(labelled, key=lambda role: (-best.get(role, 0), -labelled[role], role))
    return {role: {"strongest": best.get(role, 0), "labelled": labelled[role]} for role in order}


SOURCE_COUNTS = """
    SELECT s.id, s.kind, s.location, s.licence, s.last_scan_at, COUNT(f.rel_path) AS files,
           COUNT(DISTINCT CASE WHEN NOT EXISTS (
               SELECT 1 FROM analysis a WHERE a.asset_hash = f.asset_hash AND a.error IS NULL
           ) THEN f.asset_hash END) AS unanalysed,
           (SELECT COUNT(*) FROM download d WHERE d.source_id = s.id) AS downloads,
           (SELECT MAX(fetched_at) FROM download d WHERE d.source_id = s.id) AS last_fetch_at
    FROM source s LEFT JOIN source_file f ON f.source_id = s.id GROUP BY s.id ORDER BY s.id
"""


def _sources(cfg: Config, conn: sqlite3.Connection | None) -> list[Answer]:
    """Configured sources, and the ones scans and downloads recorded, merged by id.
    A download store isn't configured: fetching fills it."""
    recorded: dict[str, Answer] = {}
    if conn is not None:
        recorded = {row["id"]: dict(row) for row in conn.execute(SOURCE_COUNTS)}
    configured = {
        s.id: {"id": s.id, "kind": s.kind, "location": s.location, "licence": s.license}
        for s in cfg.sources
    }
    found = []
    for source_id in sorted(configured.keys() | recorded.keys()):
        row = recorded.get(source_id, {"files": 0, "unanalysed": 0, "last_scan_at": None})
        if source_id not in configured and row.get("downloads"):
            found.append(_store(row))
            continue
        row.pop("downloads", None)
        row.pop("last_fetch_at", None)
        found.append(
            {**row, **configured.get(source_id, {}), "configured": source_id in configured}
        )
    return found


def _store(row: Answer) -> Answer:
    """A connector's download store, with what analysing it would take."""
    store = {
        "id": row["id"],
        "kind": "downloads",
        "location": row["location"],
        "files": row["files"],
        "downloads": row["downloads"],
        "unanalysed": row["unanalysed"],
        "last_fetch_at": row["last_fetch_at"],
        "about": DOWNLOAD_STORE,
    }
    if row["unanalysed"]:
        store["next"] = f"mph catalogue analyse --pack {row['id']}"
    return store


def _projects(listing: list[dict[str, Any]]) -> Answer:
    projects: dict[str, Answer] = {}
    for bench in listing:
        entry = projects.setdefault(
            bench["project"],
            {"benchmarks": [], "takes": 0, "rendered": 0, "measurements_pass": 0},
        )
        entry["benchmarks"].append(bench["id"])
        standing = Counter(t["verdict"]["verdict"] for t in bench["takes"] if t["verdict"])
        entry["verdicts"] = dict(Counter(entry.get("verdicts", {})) + standing)
        made = [t["report"] for t in bench["takes"] if t["report"]]
        entry["takes"] += len(bench["takes"])
        entry["rendered"] += len(made)
        entry["measurements_pass"] += sum(bool(r.get("passed")) for r in made)
    return {"count": len(listing), "projects": projects}


# --- sounds --------------------------------------------------------------------------


def labels(lib: Library, name: str | None, origin: str | None, limit: int) -> Answer:
    with lib.reading() as conn:
        if name is None:
            return _label_names(conn, origin)
        return _label_values(conn, name, origin, limit)


def _label_names(conn: sqlite3.Connection, origin: str | None) -> Answer:
    found: dict[str, Answer] = {}
    for row in conn.execute(
        "SELECT name, COUNT(DISTINCT asset_hash), COUNT(DISTINCT value) FROM tag "
        "WHERE ?1 IS NULL OR origin = ?1 GROUP BY name ORDER BY name",
        (origin,),
    ):
        found[row[0]] = {"name": row[0], "sounds": row[1], "values": row[2], "by_origin": {}}
    for row in conn.execute(
        "SELECT name, origin, COUNT(DISTINCT asset_hash), COUNT(DISTINCT value) FROM tag "
        "WHERE ?1 IS NULL OR origin = ?1 GROUP BY name, origin ORDER BY name, origin",
        (origin,),
    ):
        found[row[0]]["by_origin"][row[1]] = {"sounds": row[2], "values": row[3]}
    return {"labels": list(found.values()), "next": "pass a name (such as role) for its values"}


def _label_values(conn: sqlite3.Connection, name: str, origin: str | None, limit: int) -> Answer:
    where = (name, origin)
    total = conn.execute(
        "SELECT COUNT(DISTINCT value) FROM tag WHERE name = ?1 AND (?2 IS NULL OR origin = ?2)",
        where,
    ).fetchone()[0]
    if not total:
        known = [row[0] for row in conn.execute("SELECT DISTINCT name FROM tag ORDER BY name")]
        source = f" from {origin}" if origin else ""
        raise Unanswerable(
            f"no sound has a {name!r} label{source}; labels: {', '.join(known) or 'none yet'}"
        )
    rows = conn.execute(
        "SELECT value, COUNT(DISTINCT asset_hash) AS sounds, group_concat(DISTINCT origin) "
        "FROM tag WHERE name = ?1 AND (?2 IS NULL OR origin = ?2) "
        "GROUP BY value ORDER BY sounds DESC, value LIMIT ?3",
        (*where, limit),
    ).fetchall()
    values = [{"value": r[0], "sounds": r[1], "origins": sorted(r[2].split(","))} for r in rows]
    answer: Answer = {"name": name, "distinct_values": total, "shown": len(values)}
    if name == "role" and origin is None:  # the overview's count, beside this one
        strongest = dict(conn.execute(stats.BEST_ROLE_COUNTS).fetchall())
        values = [{**v, "strongest": strongest.get(v["value"], 0)} for v in values]
        answer["counted"] = ROLE_VALUES
    return {**answer, "values": values}


def search_sounds(lib: Library, args: Mapping[str, Any]) -> Answer:
    query = _search_query(args)
    with lib.reading() as conn:
        result = search.search(conn, query)
    answer: Answer = {
        "matched": result.matched,
        "shown": len(result.hits),
        "hits": [hit.to_dict() for hit in result.hits],
    }
    if result.unanalysed_skipped:
        answer["note"] = (
            f"{result.unanalysed_skipped} sounds have no loudness yet and were left out; "
            "`mph catalogue analyse` measures them"
        )
    return answer


def _search_query(args: Mapping[str, Any]) -> search.Query:
    key = args.get("key")
    try:
        return search.Query(
            roles=tuple(args.get("roles", ())),
            bpm=args.get("bpm"),
            bpm_tolerance=args["bpm_tolerance"] / 100,
            key=search.parse_key_query(key) if key else None,
            form=args.get("form"),
            min_lufs=args.get("min_lufs"),
            max_lufs=args.get("max_lufs"),
            min_seconds=args.get("min_seconds"),
            max_seconds=args.get("max_seconds"),
            licence=args.get("licence"),
            text=tuple(args.get("words", ())),
            limit=args["limit"],
        )
    except search.QueryError as exc:
        raise Unanswerable(str(exc)) from exc


def sound(lib: Library, ref: str) -> Answer:
    with lib.reading() as conn:
        digest = _resolve(conn, ref)
        row = conn.execute(
            "SELECT hash, origin, bytes, container, codec, sample_rate, bit_depth, channels, "
            "duration_s, probe_error FROM asset WHERE hash = ?",
            (digest,),
        ).fetchone()
        answer: Answer = {
            **dict(row),
            "files": _files(conn, digest),
            "tags": [
                dict(r)
                for r in conn.execute(
                    "SELECT name, value, origin, confidence FROM tag WHERE asset_hash = ? "
                    "ORDER BY name, confidence DESC, value",
                    (digest,),
                )
            ],
            "analysis": _analysis(conn, digest),
            "download": _download(conn, digest),
            "made_from": _made_from(conn, digest),
            "stashes": [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM stash WHERE asset_hash = ? ORDER BY name", (digest,)
                )
            ],
        }
        answer["on_disk"] = _on_disk(conn, lib.cfg.data_dir, digest, answer["files"])
    answer["used_in_renders"] = _renders_using(lib.renders, digest)
    if not answer["analysis"]:
        answer["note"] = "not analysed yet: `mph catalogue analyse` measures BPM, key, loudness"
    return answer


def _resolve(conn: sqlite3.Connection, ref: str) -> str:
    """A hash, a unique hash prefix, a path inside its source, or a full path."""
    ref = ref.strip()
    for (location,) in conn.execute("SELECT location FROM source"):
        ref = ref.removeprefix(str(Path(location).expanduser()) + "/")
    try:
        return provenance.resolve(conn, ref.lower() if HEX.match(ref) else ref)
    except provenance.ProvenanceError as exc:
        raise Unanswerable(f"{exc}; `search_sounds` finds sounds and gives their hashes") from exc


def _files(conn: sqlite3.Connection, digest: str) -> list[Answer]:
    rows = conn.execute(
        "SELECT f.source_id, f.rel_path, s.location, s.licence FROM source_file f "
        "JOIN source s ON s.id = f.source_id WHERE f.asset_hash = ? "
        "ORDER BY f.source_id, f.rel_path",
        (digest,),
    )
    return [
        {
            "source": r["source_id"],
            "path": r["rel_path"],
            "full_path": str(Path(r["location"]).expanduser() / r["rel_path"]),
            "source_licence": r["licence"],
        }
        for r in rows
    ]


def _analysis(conn: sqlite3.Connection, digest: str) -> list[Answer]:
    rows = conn.execute(
        "SELECT analyser, version, analysed_at, result_json, error FROM analysis "
        "WHERE asset_hash = ? ORDER BY analyser, version DESC",
        (digest,),
    )
    return [
        {
            "analyser": r["analyser"],
            "version": r["version"],
            "analysed_at": r["analysed_at"],
            "error": r["error"],
            "features": json.loads(r["result_json"] or "{}"),
        }
        for r in rows
    ]


def _download(conn: sqlite3.Connection, digest: str) -> Answer | None:
    row = conn.execute(
        "SELECT source_id, remote_id, title, creator, licence, licence_url, page_url, url, "
        "fetched_at FROM download WHERE asset_hash = ? ORDER BY fetched_at LIMIT 1",
        (digest,),
    ).fetchone()
    return dict(row) if row else None


def _made_from(conn: sqlite3.Connection, digest: str) -> Answer | None:
    tree = provenance.lineage_tree(conn, digest)
    return tree.to_dict() if tree.kind == "derivative" else None


def _on_disk(conn: sqlite3.Connection, data_dir: Path, digest: str, files: list[Answer]) -> Answer:
    """Where the bytes are now: the first of its files still there, else a stash or
    the cache. Only looks for the file, never opens it."""
    for entry in files:
        if Path(entry["full_path"]).is_file():
            return {"path": entry["full_path"], "present": True}
    path = provenance.locate(conn, data_dir, digest)
    return {"path": str(path) if path else None, "present": bool(path and path.is_file())}


def _renders_using(renders: Path, digest: str) -> Answer:
    found = [
        {
            "benchmark": benchmark_id,
            "take": feedback.variant_of_report(report),
            "score_hash": report.get("score_hash"),
            "rendered_at": report.get("rendered_at"),
            "voice": entry.get("voice"),
        }
        for benchmark_id in feedback.rendered(renders)
        for report in feedback.reports(renders, benchmark_id)
        for entry in report.get("kit", [])
        if entry.get("hash") == digest
    ]
    found.sort(key=lambda r: str(r["rendered_at"]), reverse=True)
    return {"count": len(found), "newest": found[:RECENT]}


# --- benchmarks ----------------------------------------------------------------------


GIVEN = """
    SELECT benchmark, verdict, scope, created_at, notes, group_concat(score_hash) AS hashes,
           MAX(id) AS last
    FROM verdict
    WHERE (?1 IS NULL OR benchmark = ?1) AND (?2 IS NULL OR verdict = ?2)
      AND (?3 IS NULL OR created_at >= ?3)
    GROUP BY benchmark, scope, created_at, verdict, notes, CASE scope WHEN 'all' THEN 0 ELSE id END
"""  # a whole-benchmark verdict is stored once per take, and given once (REQ-QRY-10)


def _listing(lib: Library, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
    """The listening page's benchmarks, plus what other branches rendered (REQ-QRY-09)."""
    return feedback.listing(lib.renders, conn, elsewhere=True)


def _entry(listing: list[dict[str, Any]], benchmark_id: str) -> dict[str, Any]:
    found = next((b for b in listing if b["id"] == benchmark_id), None)
    if found is None:
        known = ", ".join(sorted(b["id"] for b in listing))
        raise Unanswerable(f"no benchmark {benchmark_id!r}; there are {known}")
    return found


def benchmarks(lib: Library, project: str | None) -> Answer:
    with lib.reading_if_any() as conn:
        listing = _listing(lib, conn)
    projects = sorted({b["project"] for b in listing})
    if project:
        listing = [b for b in listing if b["project"].lower() == project.lower()]
        if not listing:
            raise Unanswerable(f"no project {project!r}; projects: {', '.join(projects)}")
    return {"projects": projects, "benchmarks": [_brief(b) for b in listing]}


def _brief(bench: dict[str, Any]) -> Answer:
    return {
        "id": bench["id"],
        "title": bench["title"],
        "project": bench["project"],
        "bpm": bench["bpm"],
        "in_checkout": bench["in_checkout"],
        "verdicts_for_every_take": _wholes(bench["takes"]),
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
        "measurements_pass": made.get("passed"),
        "failed_measurements": _failed(made) if made else None,
        "audio_on_disk": made.get("on_disk"),
        "verdict": _standing(take),
    }


def _failed(made: dict[str, Any]) -> list[Answer]:
    return [
        {key: m.get(key) for key in ("name", "value", "limit", "note")}
        for m in made.get("measurements", [])
        if not m.get("passed")
    ]


def _for_every_take(verdict: dict[str, str], made: dict[str, Any]) -> bool:
    """A whole-benchmark verdict, or one given on a `main` render, which counts the
    same (REQ-FBK-02), rather than the take's own."""
    return verdict["scope"] == "all" or verdict["score_hash"] != made["score_hash"]


def _standing(take: dict[str, Any]) -> Answer | None:
    """The verdict that stands for a take. A whole-benchmark one leaves its notes to
    the benchmark, so they're shown once (REQ-QRY-10)."""
    verdict = take["verdict"]
    if not verdict:
        return None
    if _for_every_take(verdict, take["report"]):
        return {"verdict": verdict["verdict"], "scope": "all", "at": verdict["created_at"]}
    return _clip_notes(
        {
            "verdict": verdict["verdict"],
            "scope": "take",
            "at": verdict["created_at"],
            "notes": verdict["notes"],
        }
    )


def _wholes(takes: list[dict[str, Any]]) -> list[Answer]:
    """Each whole-benchmark verdict that stands for some take, once, with those takes."""
    found: dict[tuple[str, str, str], Answer] = {}
    for take in takes:
        verdict = take["verdict"]
        if not verdict or not _for_every_take(verdict, take["report"]):
            continue
        key = (verdict["created_at"], verdict["verdict"], verdict["notes"])
        if key not in found:
            found[key] = _clip_notes(
                {
                    "verdict": verdict["verdict"],
                    "at": verdict["created_at"],
                    "notes": verdict["notes"],
                    "takes": [],
                }
            )
        found[key]["takes"].append(take["name"])
    return list(found.values())


def benchmark(lib: Library, benchmark_id: str) -> Answer:
    with lib.reading_if_any() as conn:
        entry = _entry(_listing(lib, conn), benchmark_id)
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
        "verdicts": {"count": total, "newest": [_clip_notes(v) for v in given]},
        "next": "report has a render's whole report.json; verdicts has notes in full",
    }
    if not entry["in_checkout"]:
        answer["note"] = (
            f"another branch defines {benchmark_id}: this checkout has no score for it, so its "
            "takes come from its renders, and there is no score file or hear text"
        )
    return answer


def _take(take: dict[str, Any]) -> Answer:
    """A take and its newest render in brief: what it used and what failed."""
    made = take["report"]
    newest = made and {
        **_history(made),
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
        "verdict": _standing(take),
        "newest": newest,
    }


def _clip_notes(verdict: Answer) -> Answer:
    notes = str(verdict.get("notes") or "")
    if len(notes) <= NOTES_PREVIEW:
        return verdict
    return {**verdict, "notes": notes[:NOTES_PREVIEW] + "…", "notes_clipped": True}


def _history(made: dict[str, Any]) -> Answer:
    return {
        "score_hash": made.get("score_hash"),
        "take": feedback.variant_of_report(made),
        "rendered_at": made.get("rendered_at"),
        "engine": made.get("engine"),
        "measurements_pass": made.get("passed"),
        "audio_on_disk": made.get("on_disk"),
    }


def report(lib: Library, benchmark_id: str, take: str | None, score_hash: str | None) -> Answer:
    with lib.reading_if_any() as conn:
        entry = _entry(_listing(lib, conn), benchmark_id)
        given = feedback.verdicts(conn, benchmark_id)
    chosen = _pick(lib.renders, entry, take, score_hash)
    return {
        "report": chosen,
        "verdicts_on_this_render": [v for v in given if v["score_hash"] == chosen["score_hash"]],
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


def verdicts(
    lib: Library, benchmark_id: str | None, verdict: str | None, since: str | None, limit: int
) -> Answer:
    with lib.reading_if_any() as conn:
        if benchmark_id:
            _known_for_verdicts(lib, conn, benchmark_id)
        if conn is None:
            return {"total": 0, "shown": 0, "verdicts": [], "note": "no catalogue, no verdicts"}
        total, found = _given(
            conn, lib.renders, benchmark_id, limit=limit, verdict=verdict, since=since
        )
    return {"total": total, "shown": len(found), "verdicts": found}


def _known_for_verdicts(lib: Library, conn: sqlite3.Connection | None, benchmark_id: str) -> None:
    """Any benchmark with a score, a render or a verdict here."""
    judged = [r[0] for r in conn.execute("SELECT DISTINCT benchmark FROM verdict")] if conn else []
    known = {b["id"] for b in _listing(lib, conn)} | set(judged)
    if benchmark_id not in known:
        raise Unanswerable(f"no benchmark {benchmark_id!r}; there are {', '.join(sorted(known))}")


def _given(
    conn: sqlite3.Connection | None,
    renders: Path,
    benchmark_id: str | None,
    *,
    limit: int,
    verdict: str | None = None,
    since: str | None = None,
) -> tuple[int, list[Answer]]:
    """How many verdicts Dean gave, and the newest ``limit``, each as given: one for
    a take, or one for the whole benchmark with every take it covered."""
    if conn is None:
        return 0, []
    where = (benchmark_id, verdict, since)
    total = conn.execute(f"SELECT COUNT(*) FROM ({GIVEN})", where).fetchone()[0]  # noqa: S608
    newest = f"{GIVEN} ORDER BY created_at DESC, last DESC LIMIT ?4"
    rows = conn.execute(newest, (*where, limit)).fetchall()
    takes = _takes_by_render(renders, {r["benchmark"] for r in rows})
    return total, [_as_given(row, takes) for row in rows]


def _as_given(row: sqlite3.Row, takes: Mapping[tuple[str, str], str]) -> Answer:
    benchmark_id = row["benchmark"]
    judged = sorted(
        (takes.get((benchmark_id, h)), h) for h in str(row["hashes"]).split(",")
    )  # a take is None when its render is gone
    found: Answer = {
        "benchmark": benchmark_id,
        "verdict": row["verdict"],
        "scope": row["scope"],
        "created_at": row["created_at"],
        "notes": row["notes"],
    }
    if row["scope"] == "all":
        return {**found, "takes": [t for t, _ in judged], "score_hashes": [h for _, h in judged]}
    (take, render_hash), *_ = judged
    return {**found, "take": take, "score_hash": render_hash}


def _takes_by_render(renders: Path, benchmark_ids: set[str]) -> dict[tuple[str, str], str]:
    return {
        (benchmark_id, str(made.get("score_hash"))): feedback.variant_of_report(made)
        for benchmark_id in sorted(benchmark_ids)
        for made in feedback.reports(renders, benchmark_id)
    }


# --- the database and health ---------------------------------------------------------


SCHEMA_NOTES = (
    "Sounds are assets, keyed by content hash (BLAKE2b-256); source_file says where each "
    "lives (one asset can be in several packs).",
    "tag holds labels with origin (folder, filename, analysis, human) and confidence.",
    "analysis.result_json holds measured features as JSON, read with json_extract(result_json, "
    "'$.lufs'): duration_s, peak_dbfs, rms_dbfs, lufs, onsets, bpm, bpm_confidence, key, "
    "key_confidence, centroid_hz, low_ratio (energy share below 150 Hz), percussive_ratio, form.",
    "Renders are folders, not rows: verdict.score_hash names renders/<benchmark>/<score_hash>/"
    "report.json. Use the benchmark and report tools for them.",
)


def schema(lib: Library) -> Answer:
    with lib.reading() as conn:
        names = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables = [_table(conn, row["name"], row["sql"]) for row in names]
        indexes = [
            dict(r)
            for r in conn.execute(
                "SELECT name, tbl_name AS table_name, sql FROM sqlite_master "
                "WHERE type = 'index' AND sql IS NOT NULL ORDER BY name"
            )
        ]
        version = db.schema_version(conn)
    return {"schema_version": version, "notes": SCHEMA_NOTES, "tables": tables, "indexes": indexes}


def _table(conn: sqlite3.Connection, name: str, sql: str) -> Answer:
    columns = [
        {"name": c[0], "type": c[1], "not_null": bool(c[2]), "primary_key": bool(c[3])}
        for c in conn.execute('SELECT name, type, "notnull", pk FROM pragma_table_info(?)', (name,))
    ]
    quoted = '"' + name.replace('"', '""') + '"'  # a name from sqlite_master, quoted
    rows = _count(conn, f"SELECT COUNT(*) FROM {quoted}")  # noqa: S608
    return {"name": name, "rows": rows, "columns": columns, "sql": sql}


def sql(lib: Library, query: str, max_rows: int) -> Answer:
    found = readonly.select(lib.catalogue, query, max_rows=max_rows, timeout_s=SQL_TIMEOUT_S)
    answer: Answer = {
        "columns": found.columns,
        "rows": found.rows,
        "shown": len(found.rows),
        "truncated": found.truncated,
    }
    if found.truncated:
        answer["note"] = (
            f"there are more than {max_rows} rows: aggregate, page with LIMIT and OFFSET, "
            "or raise max_rows (up to 1000)"
        )
    return answer


def health(lib: Library, stages: Sequence[str]) -> Answer:
    found = doctor.run(lib.cfg, lib.ctx, lib.store, [Stage(s) for s in stages])
    return {
        "healthy": found.healthy,
        "ok": found.count(doctor.Status.OK),
        "warnings": found.count(doctor.Status.WARN),
        "failures": found.count(doctor.Status.FAIL),
        "findings": [f.to_dict() for f in found.findings],
    }
