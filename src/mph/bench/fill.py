"""Placeholder sounds from free sources (spec/components/projects.md, REQ-PRJ-05).

``plan`` walks every take of the scores it is given and asks, voice by voice,
whether the catalogue has a sound the kit would play (``kit.covered``). What
is missing is grouped by role and form, such as "kick one-shots" or "break
loops": each group is one search of a free source. A voice that plays a
recording (a clip, REQ-TRK-02) can't take a stand-in, so the plan names the
recording instead.

``fetch`` downloads up to ``limit`` sounds for each group, only CC0, public
domain and CC-BY (REQ-CAT-07), of a length that fits a one-shot or a loop.
It labels each with the role it was found for, because a source's own words
may say "ride cymbal" where a score asks for a "ride", and leaves analysing
them to the caller, after which the kit's search can find them (REQ-BEN-05).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mph.bench import kit
from mph.bench.score import Score, Voice, all_variants, variant_of
from mph.catalogue import remote

ONESHOT, LOOP = "oneshot", "loop"
# What to search a free source for, by the role a score asks for.
TERMS = {
    "kick": "kick drum",
    "snare": "snare drum",
    "clap": "hand clap",
    "hat": "closed hihat",
    "hat_open": "open hihat",
    "rim": "rimshot",
    "tom": "tom drum",
    "ride": "ride cymbal",
    "crash": "crash cymbal",
    "clave": "claves",
    "bell": "agogo bell",
    "break": "breakbeat",
    "perc": "percussion",
}
SECONDS = {ONESHOT: (0, 5), LOOP: (1, 30)}  # how long a sound of each form may be
# The role a sound was found for: below a word in its own name (0.6, names.py).
ROLE_CONFIDENCE = 0.5
SHORT_HASH = 12


@dataclass(frozen=True, slots=True)
class Need:
    """Sounds of one role and form that voices ask for and the catalogue lacks."""

    role: str
    form: str
    voices: tuple[str, ...]  # "BM-DRUM-01 kick"
    meanwhile: str  # the synth the voices play until then, or "" when they can't render

    @property
    def query(self) -> str:
        words = TERMS.get(self.role, self.role.replace("_", " "))
        return f"{words} loop" if self.form == LOOP else words

    @property
    def filters(self) -> tuple[str, ...]:
        low, high = SECONDS[self.form]
        return (f"duration:[{low} TO {high}]",)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "form": self.form,
            "voices": list(self.voices),
            "meanwhile": self.meanwhile,
            "query": self.query,
        }


@dataclass(frozen=True, slots=True)
class Recording:
    """A recording clip voices play, which no stand-in can replace."""

    hash: str
    why: str
    voices: tuple[str, ...]  # "BM-LTDJ-05 vox"


@dataclass(frozen=True, slots=True)
class Plan:
    """Voices are counted once per benchmark, whichever takes they play in."""

    needs: tuple[Need, ...] = ()
    recordings: tuple[Recording, ...] = ()
    voices: int = 0  # every voice looked at
    synths: int = 0  # synths on purpose in every take
    covered: int = 0  # with a sound in the catalogue wherever they play one

    @property
    def missing(self) -> int:
        return self.voices - self.synths - self.covered

    def to_dict(self) -> dict[str, object]:
        return {
            "voices": self.voices,
            "synths": self.synths,
            "covered": self.covered,
            "needs": [n.to_dict() for n in self.needs],
            "recordings": [
                {"hash": r.hash, "why": r.why, "voices": list(r.voices)} for r in self.recordings
            ],
        }


def _form(voice: Voice) -> str:
    return LOOP if voice.slices else ONESHOT


def _meanwhile(voice: Voice) -> str:
    """The synth a voice falls back to without a sound (REQ-BEN-05), or ""."""
    if voice.slices or voice.sound.role not in kit.SYNTHS:
        return ""
    return kit.SYNTHS[voice.sound.role][0]


def _request(voice: Voice) -> tuple[object, ...]:
    """What decides whether the catalogue covers a voice, for asking once."""
    sound = voice.sound
    return (sound.role, _form(voice), bool(voice.clips), sound.words, sound.pick, sound.hash)


def _add(found: dict[tuple[str, str], list[str]], key: tuple[str, str], label: str) -> None:
    names = found.setdefault(key, [])
    if label not in names:
        names.append(label)


@dataclass(slots=True)
class _Walk:
    conn: sqlite3.Connection | None
    data_dir: Path
    asked: dict[tuple[object, ...], bool] = field(default_factory=dict)
    seen: set[tuple[str, str, tuple[object, ...]]] = field(default_factory=set)
    named: set[str] = field(default_factory=set)
    sampled: set[str] = field(default_factory=set)  # asked the catalogue in some take
    missing: set[str] = field(default_factory=set)
    meanwhile: dict[tuple[str, str], str] = field(default_factory=dict)
    groups: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (role, form)
    recorded: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (hash, why)

    def covered(self, voice: Voice) -> bool:
        if self.conn is None:
            return False
        key = _request(voice)
        if key not in self.asked:
            self.asked[key] = kit.covered(self.conn, self.data_dir, voice)
        return self.asked[key]

    def visit(self, benchmark_id: str, voice: Voice) -> None:
        label = f"{benchmark_id} {voice.name}"
        key = (benchmark_id, voice.name, _request(voice))
        if key in self.seen:
            return
        self.seen.add(key)
        self.named.add(label)
        if voice.sound.synth:
            return
        self.sampled.add(label)
        if self.covered(voice):
            return
        self.missing.add(label)
        if voice.clips:
            _add(self.recorded, (voice.sound.hash, voice.sound.why), label)
            return
        group = (voice.sound.role, _form(voice))
        self.meanwhile.setdefault(group, _meanwhile(voice))
        _add(self.groups, group, label)


def plan(scores: Iterable[Score], conn: sqlite3.Connection | None, data_dir: Path) -> Plan:
    """What the catalogue lacks for every take of ``scores``."""
    walk = _Walk(conn, data_dir)
    for score in scores:
        for take in all_variants(score):
            made, _ = variant_of(score, take.name)
            for voice in made.voices:
                walk.visit(score.id, voice)
    needs = tuple(
        Need(role, form, tuple(labels), walk.meanwhile[(role, form)])
        for (role, form), labels in sorted(walk.groups.items())
    )
    recordings = tuple(
        Recording(digest, why, tuple(labels))
        for (digest, why), labels in sorted(walk.recorded.items())
    )
    synths = len(walk.named - walk.sampled)
    covered = len(walk.sampled - walk.missing)
    return Plan(needs, recordings, len(walk.named), synths, covered)


@dataclass(frozen=True, slots=True)
class Filled:
    need: Need
    report: remote.FetchReport


def label(conn: sqlite3.Connection, hashes: Sequence[str], role: str) -> None:
    """Say each sound is the role it was found for, as a name would (REQ-PRJ-05)."""
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO tag (asset_hash, name, value, origin, confidence) "
            "VALUES (?, 'role', ?, 'filename', ?)",
            [(digest, role, ROLE_CONFIDENCE) for digest in hashes],
        )


def fetch(
    needs: Iterable[Need],
    connector: remote.Connector,
    fetcher: remote.Fetcher,
    conn: sqlite3.Connection,
    *,
    limit: int,
    max_bytes: int = remote.DEFAULT_MAX_BYTES,
    filters: Sequence[str] = (),
) -> list[Filled]:
    """Up to ``limit`` free, publishable sounds for each need, labelled.
    ``filters`` narrow every search further, in the source's own terms."""
    filled = []
    for need in needs:
        report = fetcher.fetch(
            connector,
            need.query,
            limit,
            clear_only=True,
            max_bytes=max_bytes,
            filters=(*need.filters, *filters),
        )
        label(conn, report.hashes, need.role)
        filled.append(Filled(need, report))
    return filled
