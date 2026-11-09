"""Choosing a sound for each benchmark voice (REQ-BEN-05).

A voice asks the catalogue for its role and words, and takes the best match
that SuperCollider can read directly (WAV, AIFF, FLAC, Ogg: no conversion,
REQ-AUD-01). With no catalogue, no match, or a score that asks for a synth,
the voice falls back to a SuperCollider drum voice, and says so.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from mph.bench.score import Score, Voice
from mph.catalogue import provenance, search
from mph.catalogue.remote import CLEAR

READABLE = frozenset({"wav", "aiff", "flac", "ogg"})  # libsndfile, inside scsynth
SEARCH_DEPTH = 25
OWN = "own"  # licence of a sound synthesised here
AUTO = "auto"  # a score's way of saying "any synth voice for this role"
ORDINALS = {2: "second", 3: "third"}

# role -> (synthdef, parameters)
SYNTHS: dict[str, tuple[str, dict[str, float]]] = {
    "kick": ("mph_kick", {}),
    "snare": ("mph_snare", {}),
    "clap": ("mph_clap", {}),
    "hat": ("mph_hat", {"decay": 0.06}),
    "hat_open": ("mph_hat", {"decay": 0.45}),
    "rim": ("mph_rim", {}),
    "tom": ("mph_tom", {}),
    "clave": ("mph_clave", {}),
    "conga": ("mph_conga", {}),
    "shaker": ("mph_shaker", {}),
}


class KitError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Choice:
    voice: str
    synthdef: str
    reason: str
    path: str = ""  # sample file, read in place
    digest: str = ""
    label: str = ""  # catalogue path or synth name, for people
    licence: str = OWN
    bpm: float = 0.0  # a break's own tempo, for slicing
    params: dict[str, float] = field(default_factory=dict)

    @property
    def is_sample(self) -> bool:
        return bool(self.path)

    def fingerprint(self) -> dict[str, object]:
        """What the score hash covers: which sound, not where it lives."""
        return {"synthdef": self.synthdef, "digest": self.digest, "params": self.params}


def synth_for(voice: Voice, reason: str) -> Choice:
    role = voice.sound.role or voice.name
    name = "" if voice.sound.synth == AUTO else voice.sound.synth
    params: dict[str, float] = {}
    if not name:
        if role not in SYNTHS:
            raise KitError(f"{voice.name}: no synth voice for role {role!r}, {reason}")
        name, params = SYNTHS[role]
    return Choice(voice.name, name, reason, label=name, params=dict(params))


def _licence(conn: sqlite3.Connection, digest: str) -> str:
    row = conn.execute("SELECT licence FROM download WHERE asset_hash = ?", (digest,)).fetchone()
    if row:
        return str(row[0])
    row = conn.execute(
        "SELECT s.licence FROM source_file f JOIN source s ON s.id = f.source_id "
        "WHERE f.asset_hash = ? ORDER BY s.licence LIMIT 1",
        (digest,),
    ).fetchone()
    return str(row[0]) if row else "unknown"


def _sample(conn: sqlite3.Connection, data_dir: Path, voice: Voice) -> Choice | None:
    form = "loop" if voice.slices else "oneshot"
    attempts = [voice.sound.words, ()] if voice.sound.words else [()]
    for words in attempts:
        query = search.Query(roles=(voice.sound.role,), form=form, text=words, limit=SEARCH_DEPTH)
        skip = voice.sound.pick - 1
        for hit in search.search(conn, query).hits:
            row = conn.execute(
                "SELECT container, channels FROM asset WHERE hash = ?", (hit.digest,)
            ).fetchone()
            if not row or row[0] not in READABLE or row[1] not in (1, 2):
                continue
            if voice.slices and not hit.facts.get("bpm"):
                continue  # slicing a break needs its tempo
            path = provenance.locate(conn, data_dir, hit.digest)
            if path is None or not path.is_file():
                continue
            if skip:
                skip -= 1
                continue
            matched = f" matching {' '.join(words)}" if words else ""
            rank = (
                ORDINALS.get(voice.sound.pick, f"#{voice.sound.pick}")
                if voice.sound.pick > 1
                else "best"
            )
            return Choice(
                voice=voice.name,
                synthdef=f"mph_play{row[1]}",
                reason=f"catalogue: {rank} {voice.sound.role} {form}{matched}",
                path=str(path),
                digest=hit.digest,
                label=hit.path,
                licence=_licence(conn, hit.digest),
                bpm=float(hit.facts.get("bpm", 0) or 0),
            )
    return None


def choose(score: Score, conn: sqlite3.Connection | None, data_dir: Path) -> list[Choice]:
    choices = []
    for voice in score.voices:
        if voice.sound.synth:
            choices.append(synth_for(voice, "the score asks for a synth"))
            continue
        found = _sample(conn, data_dir, voice) if conn is not None else None
        if found is None:
            if voice.slices:
                raise KitError(
                    f"{score.id} needs a {voice.sound.role} loop in the catalogue, with a "
                    "known tempo: scan and analyse a folder of breaks first"
                )
            why = "no catalogue" if conn is None else f"no {voice.sound.role} in the catalogue"
            found = synth_for(voice, f"synth fallback: {why}")
        choices.append(found)
    return choices


def licence_marker(choices: list[Choice]) -> str:
    """``licence:clear`` only if every ingredient may be published."""
    ok = all(c.licence == OWN or c.licence in CLEAR for c in choices)
    return "licence:clear" if ok else "licence:private"
