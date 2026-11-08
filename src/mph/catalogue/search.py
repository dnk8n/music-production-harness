"""Explainable sample search (REQ-CAT-05).

Filters are hard ("must be a kick"), fit is soft ("closer to 128 BPM ranks
higher"). Every result carries the reasons behind its score, so picking a
sample is never a black box. The catalogue is a few thousand rows, so the
candidates are scored in Python rather than in clever SQL.

Evidence rules, from the spec:
- A name hint and an analysis result are both evidence; each match is weighted
  by its confidence.
- Tempo matches allow half and double time. One-shots have no tempo and pass a
  BPM filter without gaining or losing score.
- Keys match when they're the same scale or one step round the circle of
  fifths (``agreement.compatible``). A pack's key label wins over the analysed
  key. Sounds with no key (most drums) pass a key filter neutrally.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from mph.catalogue import names
from mph.catalogue.agreement import compatible, relative_key

DEFAULT_BPM_TOLERANCE = 0.03  # 3%: 128 matches 124.2-131.8
NEIGHBOUR_KEY_SCORE = 0.6
TEMPO_FACTORS = (1.0, 2.0, 0.5)
UNKNOWN_TEMPO_SCORE = 0.0


class QueryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Query:
    roles: tuple[str, ...] = ()
    bpm: float | None = None
    bpm_tolerance: float = DEFAULT_BPM_TOLERANCE
    key: str | None = None  # canonical "D:minor"
    form: str | None = None  # oneshot | loop | phrase
    min_lufs: float | None = None
    max_lufs: float | None = None
    min_seconds: float | None = None
    max_seconds: float | None = None
    licence: str | None = None
    text: tuple[str, ...] = ()
    limit: int = 20


@dataclass(frozen=True, slots=True)
class Evidence:
    value: str
    origin: str
    confidence: float


@dataclass(slots=True)
class Candidate:
    digest: str
    paths: list[str] = field(default_factory=list)
    licences: set[str] = field(default_factory=set)
    duration_s: float | None = None
    tags: dict[str, list[Evidence]] = field(default_factory=lambda: defaultdict(list))
    features: dict[str, object] = field(default_factory=dict)

    def best(self, name: str) -> Evidence | None:
        found = self.tags.get(name) or []
        return max(found, key=lambda e: (e.confidence, e.origin != "analysis"), default=None)


@dataclass(frozen=True, slots=True)
class Hit:
    digest: str
    path: str
    score: float
    reasons: tuple[str, ...]
    duration_s: float | None
    lufs: float | None
    facts: dict[str, str] = field(default_factory=dict)  # best role, bpm, key, form

    def to_dict(self) -> dict[str, object]:
        return {
            "hash": self.digest,
            "path": self.path,
            "score": self.score,
            "reasons": list(self.reasons),
            "duration_s": self.duration_s,
            "lufs": self.lufs,
            "facts": self.facts,
        }


@dataclass(slots=True)
class Result:
    hits: list[Hit]
    matched: int
    unanalysed_skipped: int  # excluded only because a loudness filter needs analysis


def parse_key_query(text: str) -> str:
    """'Dm', 'D minor', 'D:minor', 'Ab', 'F#maj' -> canonical key."""
    cleaned = text.strip().replace(":", " ")
    tokens = [t for t in names.SPLIT.split(cleaned) if t]
    if tokens:
        tokens[0] = tokens[0][:1].upper() + tokens[0][1:]
    key = names.parse_key(tokens)
    if key is None and len(tokens) == 1 and tokens[0] in names.PITCH:
        key = f"{tokens[0]}:major"  # a bare letter in a query means the major key
    if key is None:
        raise QueryError(f"not a key: {text!r} (try Dm, F#, Ab minor)")
    return key


# --- loading ---------------------------------------------------------------------


def load(conn: sqlite3.Connection) -> dict[str, Candidate]:
    candidates: dict[str, Candidate] = {}
    for row in conn.execute(
        "SELECT a.hash, a.duration_s, f.rel_path, s.licence FROM asset a "
        "JOIN source_file f ON f.asset_hash = a.hash JOIN source s ON s.id = f.source_id "
        "WHERE a.probe_error IS NULL ORDER BY a.hash, f.rel_path"
    ):
        item = candidates.setdefault(row[0], Candidate(row[0], duration_s=row[1]))
        item.paths.append(row[2])
        item.licences.add(row[3])
    for digest, name, value, origin, confidence in conn.execute(
        "SELECT asset_hash, name, value, origin, confidence FROM tag"
    ):
        if digest in candidates:
            candidates[digest].tags[name].append(Evidence(value, origin, confidence))
    # Newest successful analysis wins. Not importing the analyser keeps search
    # fast to start (librosa takes seconds to load).
    for digest, result in conn.execute(
        "SELECT asset_hash, result_json FROM analysis WHERE error IS NULL "
        "ORDER BY asset_hash, version"
    ):
        if digest in candidates:
            candidates[digest].features = json.loads(result)
    return candidates


# --- matching --------------------------------------------------------------------

Verdict = tuple[float, str] | None  # (score, reason); None = filtered out


def match_role(item: Candidate, roles: tuple[str, ...]) -> Verdict:
    found = [e for e in item.tags.get("role", []) if e.value in roles]
    if not found:
        return None
    best = max(found, key=lambda e: e.confidence)
    return best.confidence, f"{best.value} ({best.origin} {best.confidence:g})"


def tempo_error(measured: float, target: float) -> float:
    return min(abs(measured * f - target) / target for f in TEMPO_FACTORS)


def match_bpm(item: Candidate, target: float, tolerance: float) -> Verdict:
    if _form(item) == "oneshot":
        return 0.0, "one-shot, fits any tempo"
    found = item.tags.get("bpm", [])
    if not found:
        return UNKNOWN_TEMPO_SCORE, "tempo unknown"
    scored = [
        (e.confidence * (1 - tempo_error(float(e.value), target) / tolerance), e)
        for e in found
        if tempo_error(float(e.value), target) <= tolerance
    ]
    if not scored:
        return None
    score, best = max(scored, key=lambda pair: pair[0])
    return max(score, 0.0), f"{best.value} BPM ({best.origin})"


def match_key(item: Candidate, target: str) -> Verdict:
    labels = [e for e in item.tags.get("key", []) if e.origin != "analysis"]
    evidence = max(labels, key=lambda e: e.confidence, default=None) or item.best("key")
    if evidence is None:
        return 0.0, "no key (fits any)"
    if evidence.value in (target, relative_key(target)):
        return evidence.confidence, f"key {evidence.value} ({evidence.origin})"
    if compatible(evidence.value, target):
        score = evidence.confidence * NEIGHBOUR_KEY_SCORE
        return score, f"key {evidence.value}, a neighbour of {target} ({evidence.origin})"
    return None


def _form(item: Candidate) -> str | None:
    measured = item.features.get("form")
    if isinstance(measured, str):
        return measured
    hint = item.best("form")
    return hint.value if hint else None


def _lufs(item: Candidate) -> float | None:
    value = item.features.get("lufs")
    return float(value) if isinstance(value, int | float) else None


def _within(value: float | None, low: float | None, high: float | None) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def passes_filters(item: Candidate, query: Query) -> bool:
    if query.licence and query.licence not in item.licences:
        return False
    if query.form and _form(item) != query.form:
        return False
    if not _within(item.duration_s, query.min_seconds, query.max_seconds):
        return False
    if not _within(_lufs(item), query.min_lufs, query.max_lufs):
        return False
    haystack = " ".join(item.paths).lower()
    tag_values = " ".join(e.value for found in item.tags.values() for e in found).lower()
    return all(term.lower() in haystack or term.lower() in tag_values for term in query.text)


def score(item: Candidate, query: Query) -> Hit | None:
    if not passes_filters(item, query):
        return None
    verdicts: list[Verdict] = []
    if query.roles:
        verdicts.append(match_role(item, query.roles))
    if query.bpm is not None:
        verdicts.append(match_bpm(item, query.bpm, query.bpm_tolerance))
    if query.key is not None:
        verdicts.append(match_key(item, query.key))
    if any(v is None for v in verdicts):
        return None
    kept = [v for v in verdicts if v is not None]
    return Hit(
        digest=item.digest,
        path=item.paths[0],
        score=round(sum(s for s, _ in kept), 3),
        reasons=tuple(reason for _, reason in kept),
        duration_s=item.duration_s,
        lufs=_lufs(item),
        facts=facts(item),
    )


def facts(item: Candidate) -> dict[str, str]:
    found = {name: best.value for name in ("role", "bpm", "key") if (best := item.best(name))}
    if form := _form(item):
        found["form"] = form
    return found


def search(conn: sqlite3.Connection, query: Query) -> Result:
    candidates = load(conn)
    hits = [hit for item in candidates.values() if (hit := score(item, query))]
    hits.sort(key=lambda h: (-h.score, h.path))
    loudness = query.min_lufs is not None or query.max_lufs is not None
    unanalysed = sum(1 for c in candidates.values() if loudness and _lufs(c) is None)
    return Result(hits[: query.limit], len(hits), unanalysed)
