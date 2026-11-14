"""How often audio analysis agrees with labels (REQ-CAT-03, REQ-CAT-12).

This is a quality signal for both sides: low agreement on a pack means either
its labels are unreliable or the analyser needs tuning, and the per-sound
disagreements are where to listen first. Names and what files say about
themselves are measured apart: a pack can name its loops well and still
leave a tool's default tempo in every file.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

TEMPO_TOLERANCE = 0.02
NAMES = ("folder", "filename")  # priors parsed from folder and file names
OWN = ("embedded",)  # what the files say about themselves


@dataclass(frozen=True, slots=True)
class Agreement:
    field: str
    compared: int
    agreed: int

    @property
    def rate(self) -> float:
        return self.agreed / self.compared if self.compared else 0.0


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
NEIGHBOUR_SHARED_NOTES = 6  # one step round the circle of fifths changes one note


def relative_key(key: str) -> str:
    """'A:minor' <-> 'C:major': same notes, so a common and forgivable confusion."""
    tonic, quality = key.split(":")
    shift, other = (3, "major") if quality == "minor" else (-3, "minor")
    return f"{NOTE_NAMES[(NOTE_NAMES.index(tonic) + shift) % 12]}:{other}"


def scale_notes(key: str) -> frozenset[int]:
    """Pitch classes of the key's natural scale ('A:minor' and 'C:major' share them)."""
    tonic, quality = key.split(":")
    root = (NOTE_NAMES.index(tonic) + (3 if quality == "minor" else 0)) % 12
    return frozenset((root + step) % 12 for step in MAJOR_STEPS)


def compatible(a: str, b: str) -> bool:
    """Same scale, or one step apart on the circle of fifths: safe to layer."""
    return len(scale_notes(a) & scale_notes(b)) >= NEIGHBOUR_SHARED_NOTES


def _pairs(
    conn: sqlite3.Connection, name: str, hashes: Sequence[str], origins: Sequence[str]
) -> list[tuple[str, str]]:
    """(strongest label of these origins, analysis value) per asset; hashes and
    origins travel as JSON parameters.

    A file named "..._Cm" inside a folder labelled "(Fm, ...)" is compared on Cm:
    the most confident prior is the one search would trust.
    """
    rows = conn.execute(
        "SELECT prior, measured FROM ("
        "  SELECT p.value AS prior, a.value AS measured, ROW_NUMBER() OVER ("
        "    PARTITION BY p.asset_hash ORDER BY p.confidence DESC, p.origin = 'filename' DESC"
        "  ) AS rank FROM tag p JOIN tag a ON a.asset_hash = p.asset_hash "
        "  AND a.name = p.name AND a.origin = 'analysis' "
        "  WHERE p.name = ? AND p.origin IN (SELECT value FROM json_each(?)) "
        "  AND p.asset_hash IN (SELECT value FROM json_each(?))"
        ") WHERE rank = 1",
        (name, json.dumps(list(origins)), json.dumps(list(hashes))),
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def measure(
    conn: sqlite3.Connection, hashes: Sequence[str], origins: Sequence[str] = NAMES
) -> list[Agreement]:
    """Agreement between analysis and the strongest label of ``origins``."""
    if not hashes:
        return []
    tempo = _pairs(conn, "bpm", hashes, origins)
    tempo_ok = sum(abs(float(a) - float(p)) / float(p) <= TEMPO_TOLERANCE for p, a in tempo)
    keys = _pairs(conn, "key", hashes, origins)
    keys_ok = sum(a in (p, relative_key(p)) for p, a in keys)
    keys_near = sum(compatible(p, a) for p, a in keys)
    return [
        Agreement("bpm", len(tempo), tempo_ok),
        Agreement("key", len(keys), keys_ok),
        Agreement("key or a neighbouring key", len(keys), keys_near),
    ]
