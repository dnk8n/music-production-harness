"""Choosing a sound for each benchmark voice (REQ-BEN-05, REQ-BEN-16).

A voice asks the catalogue for its role and words, and takes the best match
that SuperCollider can read directly (WAV, AIFF, FLAC, Ogg: no conversion,
REQ-AUD-01). For noise-like roles (hats, shakers, cymbals) it passes over
tonal sounds, ones that whistle rather than hiss, unless nothing else fits.
With no catalogue, no match, or a score that asks for a synth, the voice
falls back to a SuperCollider drum voice, and says so.
A voice can pin one catalogue sound by its hash, with the reason an agent
picked it (a fresh take, REQ-BEN-16). It plays exactly that sound; if the
sound isn't here, or whistles where a hiss is wanted, the voice searches as
above, and the reason says what happened to the pick.
A take Dean passed keeps the sounds he heard when it renders again (REQ-BEN-15).
A clip voice plays exactly the recording it pins, at the tempo the catalogue
measured, and nothing else (spec/components/tracks.md, REQ-TRK-02). A stem
plays at the tempo measured for the recording it was separated from
(REQ-TRK-05).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from mph.bench import audio
from mph.bench.score import Score, Voice, recorded
from mph.catalogue import provenance, search
from mph.catalogue.remote import CLEAR

READABLE = frozenset({"wav", "aiff", "flac", "ogg"})  # libsndfile, inside scsynth
SEARCH_DEPTH = 25
OWN = "own"  # licence of a sound synthesised here
AUTO = "auto"  # a score's way of saying "any synth voice for this role"
ORDINALS = {2: "second", 3: "third"}
NOISE_ROLES = frozenset({"hat", "hat_open", "shaker", "cymbal", "ride", "crash"})
TONAL_FLATNESS = 0.05  # below this, a noise-like sound whistles (spectral flatness)
SHORT_HASH = 12  # hex digits of a hash that name a sound in a reason
PICKED = "picked: "  # how a reason starts when a score says why it chose the sound
ASKED_FOR_SYNTH = "the score asks for a synth"
STEMS = "stems"  # the recipe whose sounds keep their recording's tempo (REQ-TRK-05)

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
    "bell": ("mph_bell", {}),
    "djembe": ("mph_djembe", {}),  # a voice's params pick the stroke: 0 bass, 1 tone, 2 slap
    "dunun": ("mph_dunun", {}),
}


class KitError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Kept:
    """A sound a passed render used, for the take to use again (REQ-BEN-15)."""

    digest: str
    label: str
    reason: str  # why it was chosen back then
    render: str  # the passed render's score hash
    request: object = None  # what the voice asked for then; None if not recorded


KEPT = "; kept from passed render "


def request_of(voice: Voice) -> dict[str, object]:
    """What a voice asks the catalogue for, as reports record it (REQ-BEN-15)."""
    plain: dict[str, object] = json.loads(json.dumps(recorded(voice.sound)))
    return plain


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


def tonal(path: Path) -> bool:
    """A sound that rings at a few pitches rather than hissing (REQ-BEN-05)."""
    try:
        data, rate = audio.read(path)
    except (OSError, RuntimeError, ValueError):
        return False  # unreadable here: let the render report it
    return audio.flatness(data, rate) < TONAL_FLATNESS


@dataclass(frozen=True, slots=True)
class _Found:
    digest: str
    path: Path
    label: str
    synthdef: str
    bpm: float


def _candidates(
    conn: sqlite3.Connection, data_dir: Path, voice: Voice, words: tuple[str, ...]
) -> list[_Found]:
    form = "loop" if voice.slices else "oneshot"
    query = search.Query(roles=(voice.sound.role,), form=form, text=words, limit=SEARCH_DEPTH)
    found = []
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
        bpm = float(hit.facts.get("bpm", 0) or 0)
        found.append(_Found(hit.digest, path, hit.path, f"mph_play{row[1]}", bpm))
    return found


def _sample(conn: sqlite3.Connection, data_dir: Path, voice: Voice) -> Choice | None:
    form = "loop" if voice.slices else "oneshot"
    attempts = [voice.sound.words, ()] if voice.sound.words else [()]
    for words in attempts:
        found = _candidates(conn, data_dir, voice, words)
        passed = 0
        if voice.sound.role in NOISE_ROLES:
            hissing = [f for f in found if not tonal(f.path)]
            if hissing:  # only pass over tonal sounds when something else fits
                passed, found = len(found) - len(hissing), hissing
        if len(found) < voice.sound.pick:
            continue
        chosen = found[voice.sound.pick - 1]
        matched = f" matching {' '.join(words)}" if words else ""
        rank = (
            ORDINALS.get(voice.sound.pick, f"#{voice.sound.pick}")
            if voice.sound.pick > 1
            else "best"
        )
        skipped = f", passing over {passed} that whistle rather than hiss" if passed else ""
        return Choice(
            voice=voice.name,
            synthdef=chosen.synthdef,
            reason=f"catalogue: {rank} {voice.sound.role} {form}{matched}{skipped}",
            path=str(chosen.path),
            digest=chosen.digest,
            label=chosen.label,
            licence=_licence(conn, chosen.digest),
            bpm=chosen.bpm,
        )
    return None


def _best_bpm(conn: sqlite3.Connection, digest: str) -> float:
    """A sound's tempo; for a stem with none of its own, its recording's
    (REQ-TRK-05): separation keeps every sample in place."""
    row = conn.execute(
        "SELECT value FROM tag WHERE asset_hash = ? AND name = 'bpm' "
        "ORDER BY confidence DESC, origin != 'analysis' DESC LIMIT 1",
        (digest,),
    ).fetchone()
    if row:
        return float(row[0])
    made = conn.execute(
        "SELECT d.inputs_json FROM derivation d JOIN recipe r ON r.id = d.recipe_id "
        "WHERE d.output_hash = ? AND r.kind = ?",
        (digest, STEMS),
    ).fetchone()
    inputs = json.loads(made[0]) if made else []
    return _best_bpm(conn, str(inputs[0])) if inputs else 0.0


def _label(conn: sqlite3.Connection, digest: str) -> str:
    """A sound's catalogue path, as search hits give it."""
    row = conn.execute(
        "SELECT rel_path FROM source_file WHERE asset_hash = ? ORDER BY rel_path LIMIT 1",
        (digest,),
    ).fetchone()
    return str(row[0]) if row else digest[:SHORT_HASH]


def _exact(conn: sqlite3.Connection, data_dir: Path, voice: Voice, digest: str) -> _Found | str:
    """One catalogue sound, ready to play for this voice, or why it can't be."""
    row = conn.execute("SELECT container, channels FROM asset WHERE hash = ?", (digest,)).fetchone()
    if not row:
        return "isn't in the catalogue"
    if row[0] not in READABLE:
        return f"is {row[0]}, which SuperCollider can't play as it is"
    if row[1] not in (1, 2):
        return f"has {row[1]} channels, and a voice plays mono or stereo"
    path = provenance.locate(conn, data_dir, digest)
    if path is None or not path.is_file():
        return "isn't on disk"
    timed = bool(voice.slices or voice.clips)  # a break is sliced by its tempo, clips placed by it
    bpm = _best_bpm(conn, digest) if timed else 0.0
    if timed and not bpm:
        use = "slice it by" if voice.slices else "place its clips by"
        return f"has no known tempo to {use}"
    return _Found(digest, path, _label(conn, digest), f"mph_play{row[1]}", bpm)


def _kept(
    conn: sqlite3.Connection, data_dir: Path, voice: Voice, kept: Kept | None
) -> Choice | None:
    """The sound a passed take used, if the voice still asks for the same thing
    and the sound is still here (REQ-BEN-15)."""
    if kept is None or (kept.request is not None and kept.request != request_of(voice)):
        return None
    found = _exact(conn, data_dir, voice, kept.digest)
    if isinstance(found, str):
        return None
    first = kept.reason.split(KEPT)[0]
    return Choice(
        voice=voice.name,
        synthdef=found.synthdef,
        reason=f"{first}{KEPT}{kept.render}",
        path=str(found.path),
        digest=kept.digest,
        label=kept.label,
        licence=_licence(conn, kept.digest),
        bpm=found.bpm,
    )


def _picked(conn: sqlite3.Connection, data_dir: Path, voice: Voice) -> Choice | str:
    """The sound a score pins by its hash (REQ-BEN-16), or what is wrong with it."""
    found = _exact(conn, data_dir, voice, voice.sound.hash)
    if isinstance(found, str):
        return found
    if voice.sound.role in NOISE_ROLES and tonal(found.path):
        return "whistles rather than hisses"  # REQ-BEN-05 holds for picks too
    return Choice(
        voice=voice.name,
        synthdef=found.synthdef,
        reason=f"{PICKED}{voice.sound.why}",
        path=str(found.path),
        digest=found.digest,
        label=found.label,
        licence=_licence(conn, found.digest),
        bpm=found.bpm,
    )


def _catalogued(
    conn: sqlite3.Connection, data_dir: Path, voice: Voice, kept: Kept | None
) -> tuple[Choice | None, str]:
    """A catalogue sound for the voice, and what became of its pinned one."""
    found = _kept(conn, data_dir, voice, kept)
    if found is not None or not voice.sound.hash:
        return found or _sample(conn, data_dir, voice), ""
    picked = _picked(conn, data_dir, voice)
    if isinstance(picked, Choice):
        return picked, ""
    return _sample(conn, data_dir, voice), picked


def _recording(
    score: Score, voice: Voice, conn: sqlite3.Connection | None, data_dir: Path
) -> Choice:
    """A clip voice's recording: the sound it pins and no other (REQ-TRK-02)."""
    problem = "needs the catalogue" if conn is None else ""
    if conn is not None:
        picked = _picked(conn, data_dir, voice)
        if isinstance(picked, Choice):
            return picked
        problem = picked
    raise KitError(
        f"{score.id}: {voice.name} plays clips of {voice.sound.hash[:SHORT_HASH]}, which "
        f"{problem}: scan and analyse the folder it is in (mph catalogue scan, analyse)"
    )


def _fallback(score: Score, voice: Voice, conn: sqlite3.Connection | None) -> Choice:
    """A synth voice, when the catalogue has nothing for this voice."""
    if voice.slices:
        raise KitError(
            f"{score.id} needs a {voice.sound.role} loop in the catalogue, with a "
            "known tempo: scan and analyse a folder of breaks first"
        )
    why = "no catalogue" if conn is None else f"no {voice.sound.role} in the catalogue"
    return synth_for(voice, f"synth fallback: {why}")


def choose(
    score: Score,
    conn: sqlite3.Connection | None,
    data_dir: Path,
    keep: Mapping[str, Kept] | None = None,
) -> list[Choice]:
    """A sound for every voice. ``keep`` has the sounds a passed take used."""
    keep = keep or {}
    choices = []
    for voice in score.voices:
        if voice.clips:
            choices.append(_recording(score, voice, conn, data_dir))
            continue
        if voice.sound.synth:
            why = voice.sound.why
            choices.append(synth_for(voice, f"{PICKED}{why}" if why else ASKED_FOR_SYNTH))
            continue
        found, problem = None, "needs the catalogue"
        if conn is not None:
            found, problem = _catalogued(conn, data_dir, voice, keep.get(voice.name))
        found = found or _fallback(score, voice, conn)
        if voice.sound.hash and problem:  # say what became of the pick (REQ-BEN-16)
            pin = voice.sound.hash[:SHORT_HASH]
            found = replace(found, reason=f"{found.reason}; the picked sound {pin} {problem}")
        choices.append(found)
    return choices


def licence_marker(choices: list[Choice]) -> str:
    """``licence:clear`` only if every ingredient may be published."""
    ok = all(c.licence == OWN or c.licence in CLEAR for c in choices)
    return "licence:clear" if ok else "licence:private"
