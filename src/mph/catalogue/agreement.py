"""How often audio analysis agrees with name-derived priors (REQ-CAT-03).

This is a quality signal for both sides: low agreement on a pack means either
its labels are unreliable or the analyser needs tuning, and the per-sound
disagreements are where to listen first.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

TEMPO_TOLERANCE = 0.02


@dataclass(frozen=True, slots=True)
class Agreement:
    field: str
    compared: int
    agreed: int

    @property
    def rate(self) -> float:
        return self.agreed / self.compared if self.compared else 0.0


def relative_key(key: str) -> str:
    """'A:minor' <-> 'C:major': same notes, so a common and forgivable confusion."""
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    tonic, quality = key.split(":")
    shift, other = (3, "major") if quality == "minor" else (-3, "minor")
    return f"{names[(names.index(tonic) + shift) % 12]}:{other}"


def _pairs(conn: sqlite3.Connection, name: str, hashes: Sequence[str]) -> list[tuple[str, str]]:
    """(name prior, analysis value) per asset; hashes travel as one JSON parameter."""
    rows = conn.execute(
        "SELECT p.value, a.value FROM tag p JOIN tag a ON a.asset_hash = p.asset_hash "
        "AND a.name = p.name AND a.origin = 'analysis' "
        "WHERE p.name = ? AND p.origin IN ('folder', 'filename') "
        "AND p.asset_hash IN (SELECT value FROM json_each(?)) GROUP BY p.asset_hash",
        (name, json.dumps(list(hashes))),
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def measure(conn: sqlite3.Connection, hashes: Sequence[str]) -> list[Agreement]:
    if not hashes:
        return []
    tempo = _pairs(conn, "bpm", hashes)
    tempo_ok = sum(abs(float(a) - float(p)) / float(p) <= TEMPO_TOLERANCE for p, a in tempo)
    keys = _pairs(conn, "key", hashes)
    keys_ok = sum(a in (p, relative_key(p)) for p, a in keys)
    return [Agreement("bpm", len(tempo), tempo_ok), Agreement("key", len(keys), keys_ok)]
