"""What a file says about itself (REQ-CAT-12).

Two readers: ffprobe gives a file's tags and cover art (``probe``), and
``chunks`` gives the loop and sampler chunks ffprobe skips. All of it is kept
in the ``embedded`` table as JSON. The labels drawn from it are tags with
``origin='embedded'``, made again whenever the file is read again.

How sure each label is, from the spec:

- ``bpm`` 0.9 from an ACID tempo whose beats fill the file to within 2%, or
  an Apple Loop's beats over its length; 0.35 from a BPM tag, below a tempo
  in the file's name. A one-shot has no tempo, and only 60 to 200 BPM counts.
- ``key`` 0.8 from an Apple Loop's root and scale; 0.35 from a key tag in any
  common notation, below a key in the file's name. ``IKEY`` is RIFF INFO's
  keywords, so there only a key with a mode or a sharp or flat counts, never
  a lone letter.
- ``form`` 0.8: one-shot when a chunk says so; loop when it says loop and its
  beats at its tempo fill the file.
- ``root`` 0.8 from ACID (when it says the note is set) or an Apple Loop with
  a scale; 0.6 from a sampler chunk, unless it looks untouched (60, no
  tuning, no loops).
- An ACID chunk on a loop whose beats don't fill the file was stamped from a
  tool's template, not set for that file, so its tempo and root note give
  nothing: on Dean's library 1,027 of them all said 120 BPM.
- ``title``, ``artist``, ``album``, ``genre``, ``record_label``,
  ``catalogue_number``, ``year`` and ``track`` 1, as the tags write them.

Nothing here gives a role: text in a file names songs, artists, labels and
genres (REQ-CAT-03).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mph.catalogue import names
from mph.catalogue.chunks import Chunks, Instrument
from mph.catalogue.chunks import read as read_chunks
from mph.catalogue.probe import Picture, Probe

# Bump when what is read or how it labels changes: the next scan reads every file again.
VERSION = 1
ORIGIN = "embedded"
MAX_VALUE = 1000  # characters kept of any one tag
FILLS = 0.02  # beats at the tempo fill the file to within this share of its length
TEMPO_RANGE = names.BPM_RANGE
FITTED_TEMPO = 0.9
LOOP_KEY = 0.8
TAGGED_TEMPO = TAGGED_KEY = 0.35  # below names (0.6) and near folders, above bare numbers (0.3)
FORM = 0.8
ROOT, SAMPLER_ROOT = 0.8, 0.6
AS_WRITTEN = 1.0
UNTOUCHED_NOTE = 60  # middle C: what tools write when nobody set a note
METERS = (2, 4, 8, 16)

BPM_TAGS = ("tbpm", "bpm", "tempo", "tmpo")
KEY_TAGS = ("tkey", "initialkey", "key")
LOOSE_KEY_TAGS = ("ikey",)  # RIFF INFO keywords, which some packs use for keys
DESCRIBED: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "artist": ("artist", "author"),
    "album": ("album",),
    "genre": ("genre",),
    "record_label": ("label", "publisher", "organization", "organisation"),
    "catalogue_number": (
        "catalognumber",
        "cataloguenumber",
        "catalogno",
        "catalogueno",
        "catalog",
        "catalogue",
        "catno",
    ),
    "year": ("date", "year", "originaldate", "originalyear", "tdor"),
    "track": ("track", "tracknumber"),
}
TRACK_TOTALS = ("tracktotal", "totaltracks")
YEAR = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")
NUMBER = re.compile(r"^\s*(\d{2,3}(?:[.,]\d+)?)")
LYRICS = re.compile(r"lyric|^uslt$|^sylt$", re.IGNORECASE)
NOT_KEY_CHARS = re.compile(r"[^a-z0-9]")
CAMELOT = re.compile(r"^(1[0-2]|[1-9])\s*([ab])$", re.IGNORECASE)
OPEN_KEY = re.compile(r"^(1[0-2]|[1-9])\s*([dm])$", re.IGNORECASE)
KEY_PARTS = re.compile(r"\s*[/|;,]\s*|\s+-\s+")  # "8A - Am", "Am/8A"


@dataclass(frozen=True, slots=True)
class Found:
    """Everything one read of a file found, or why it couldn't be read."""

    tags: Mapping[str, str] = field(default_factory=dict)
    chunks: Chunks = field(default_factory=Chunks)
    picture: Picture | None = None
    duration_s: float | None = None  # from the probe, to check a tempo against
    error: str | None = None

    def to_json(self) -> dict[str, object]:
        found: dict[str, object] = {"tags": dict(self.tags)}
        for name in ("acid", "smpl", "inst", "basc"):
            if (chunk := getattr(self.chunks, name)) is not None:
                found[name] = asdict(chunk)
        if self.picture is not None:
            found["picture"] = asdict(self.picture)
        if self.chunks.skipped:
            found["skipped"] = list(self.chunks.skipped)
        return found


@dataclass(frozen=True, slots=True, order=True)
class Label:
    name: str
    value: str
    confidence: float


def kept_tags(tags: Mapping[str, str]) -> dict[str, str]:
    """Every tag but lyrics, each kept to MAX_VALUE characters."""
    return {
        key: value[:MAX_VALUE]
        for key, value in sorted(tags.items())
        if value.strip() and not LYRICS.search(key)
    }


def read(path: Path, facts: Probe) -> Found:
    """What the file at ``path`` says, given what ffprobe found in it."""
    if facts.error:
        return Found(error=facts.error)
    try:
        found = read_chunks(path)
    except OSError as exc:
        return Found(error=f"cannot read {path.name}: {exc.strerror or exc}")
    return Found(kept_tags(facts.tags), found, facts.picture, facts.duration_s)


# --- labels ----------------------------------------------------------------------------


def labels(found: Found) -> list[Label]:
    """The labels a read gives, each (name, value) once at its highest confidence."""
    if found.error:
        return []
    made = [
        *_tempos(found),
        *_forms(found),
        *_keys(found),
        *_roots(found),
        *_described(found.tags),
    ]
    best: dict[tuple[str, str], float] = {}
    for label in made:
        key = (label.name, label.value)
        best[key] = max(best.get(key, 0.0), label.confidence)
    return sorted(Label(name, value, confidence) for (name, value), confidence in best.items())


def _one_shot(chunks: Chunks) -> bool:
    return bool((chunks.acid and chunks.acid.one_shot) or (chunks.basc and chunks.basc.one_shot))


def _tempo_text(bpm: float) -> str:
    return f"{round(bpm, 2):g}"


def _in_range(bpm: float) -> bool:
    return TEMPO_RANGE.start <= bpm < TEMPO_RANGE.stop


def _fills(beats: int, bpm: float, duration_s: float | None) -> bool:
    """Whether ``beats`` at ``bpm`` last as long as the file, to within 2%."""
    if beats < 1 or bpm <= 0 or not duration_s:
        return False
    return abs(beats * 60 / bpm - duration_s) <= FILLS * duration_s


def _acid_fits(found: Found) -> bool:
    """Whether the ACID chunk was set for this file: a one-shot, or a loop whose
    beats at its tempo fill it. Any other was stamped from a template."""
    acid = found.chunks.acid
    if acid is None:
        return False
    return acid.one_shot or _fills(acid.beats, acid.tempo, found.duration_s)


def _apple_tempo(found: Found) -> float | None:
    """An Apple Loop's tempo: its beats over its length, as Logic works it out."""
    basc = found.chunks.basc
    if basc is None or basc.one_shot or basc.beats < 1 or not found.duration_s:
        return None
    denominator = basc.meter[1] if basc.meter[1] in METERS else 4
    return basc.beats * (4 / denominator) * 60 / found.duration_s


def _tempos(found: Found) -> list[Label]:
    if _one_shot(found.chunks):
        return []
    made = []
    acid = found.chunks.acid
    if acid is not None and _acid_fits(found) and _in_range(acid.tempo):
        made.append(Label("bpm", _tempo_text(acid.tempo), FITTED_TEMPO))
    apple = _apple_tempo(found)
    if apple is not None and _in_range(apple):
        made.append(Label("bpm", _tempo_text(apple), FITTED_TEMPO))
    tagged = _tag_number(found.tags, BPM_TAGS)
    if tagged is not None and _in_range(tagged):
        made.append(Label("bpm", _tempo_text(tagged), TAGGED_TEMPO))
    return made


def _forms(found: Found) -> list[Label]:
    made = []
    acid, basc = found.chunks.acid, found.chunks.basc
    if acid is not None:
        if acid.one_shot:
            made.append(Label("form", "oneshot", FORM))
        elif _acid_fits(found):
            made.append(Label("form", "loop", FORM))
    if basc is not None:
        if basc.one_shot:
            made.append(Label("form", "oneshot", FORM))
        elif basc.beats >= 1:
            made.append(Label("form", "loop", FORM))  # its tempo is its beats over its length
    return made


def _keys(found: Found) -> list[Label]:
    made = []
    basc = found.chunks.basc
    if basc is not None and basc.scale in ("major", "minor"):
        made.append(Label("key", f"{_pitch(basc.root_note)}:{basc.scale}", LOOP_KEY))
    for tags, strict in ((KEY_TAGS, False), (LOOSE_KEY_TAGS, True)):
        text = _tag(found.tags, tags)
        if text and (key := parse_key(text, strict=strict)):
            made.append(Label("key", key, TAGGED_KEY))
    return made


def _roots(found: Found) -> list[Label]:
    made = []
    chunks = found.chunks
    if chunks.acid is not None and chunks.acid.root_note is not None and _acid_fits(found):
        made.append(Label("root", _pitch(chunks.acid.root_note), ROOT))
    if chunks.basc is not None and chunks.basc.scale not in ("neither", "unknown"):
        made.append(Label("root", _pitch(chunks.basc.root_note), ROOT))
    smpl = chunks.smpl
    if smpl is not None and not _untouched(smpl.unity_note, smpl.cents, bool(smpl.loops)):
        made.append(Label("root", _pitch(smpl.unity_note), SAMPLER_ROOT))
    if chunks.inst is not None and not _untouched_instrument(chunks.inst):
        made.append(Label("root", _pitch(chunks.inst.base_note), SAMPLER_ROOT))
    return made


def _untouched(note: int, tuning: float, looped: bool) -> bool:
    return note == UNTOUCHED_NOTE and not tuning and not looped


def _untouched_instrument(inst: Instrument) -> bool:
    looped = inst.sustain is not None or inst.release is not None
    return _untouched(inst.base_note, inst.detune, looped)


def _pitch(note: int) -> str:
    return names.SHARP_NAMES[note % 12]


def _described(tags: Mapping[str, str]) -> list[Label]:
    made = []
    for name, keys in DESCRIBED.items():
        text = _tag(tags, keys)
        value = _year(text) if name == "year" else _track(text, tags) if name == "track" else text
        if value:
            made.append(Label(name, " ".join(value.split()), AS_WRITTEN))
    return made


def _year(text: str | None) -> str | None:
    found = YEAR.search(text or "")
    return found.group(1) if found else None


def _track(text: str | None, tags: Mapping[str, str]) -> str | None:
    """'7/7' as written, or '7' and a separate total joined the same way."""
    if not text or text.strip().lstrip("0") in ("", "/") or text.strip().startswith("0/"):
        return None
    total = _tag(tags, TRACK_TOTALS)
    return f"{text.strip()}/{total.strip()}" if total and "/" not in text else text


# --- reading tags ----------------------------------------------------------------------


def _tag(tags: Mapping[str, str], keys: Iterable[str]) -> str | None:
    """The first of ``keys`` a file has, matched without case or punctuation:
    CATALOGNUMBER, catalog_number and 'Catalog #' are one key."""
    found: dict[str, str] = {}
    for key, value in sorted(tags.items()):
        found.setdefault(NOT_KEY_CHARS.sub("", key.lower()), value)
    for key in keys:
        text = found.get(key, "").strip()
        if text:
            return text
    return None


def _tag_number(tags: Mapping[str, str], keys: Iterable[str]) -> float | None:
    match = NUMBER.match(_tag(tags, keys) or "")
    return float(match.group(1).replace(",", ".")) if match else None


def parse_key(text: str, *, strict: bool = False) -> str | None:
    """A key tag ('Am', 'A minor', 'F#m', '8A', '1m', '8A - Am') as 'A:minor'.

    A lone letter is a major key, as ID3 writes them, unless ``strict``."""
    for part in KEY_PARTS.split(text.strip()):
        if key := _one_key(part, strict=strict):
            return key
    return None


def _one_key(part: str, *, strict: bool) -> str | None:
    if found := CAMELOT.match(part):  # 8B is C major, each step a fifth up; A is its relative
        return _wheel(int(found.group(1)) - 8, found.group(2).lower() == "a")
    if found := OPEN_KEY.match(part):  # 1d is C major, 1m A minor
        return _wheel(int(found.group(1)) - 1, found.group(2).lower() == "m")
    tokens = [t for t in names.SPLIT.split(part) if t]
    if not tokens:
        return None
    if not strict:
        tokens[0] = tokens[0][:1].upper() + tokens[0][1:]
    key = names.parse_key(tokens)
    if key is None and not strict and len(tokens) == 1 and tokens[0] in names.PITCH:
        key = f"{tokens[0]}:major"
    return key


def _wheel(steps: int, minor: bool) -> str:
    """The key ``steps`` fifths above C major, or its relative minor."""
    tonic = 7 * steps % 12
    if minor:
        return f"{names.SHARP_NAMES[(tonic + 9) % 12]}:minor"
    return f"{names.SHARP_NAMES[tonic]}:major"


# --- storing ---------------------------------------------------------------------------


def store(conn: sqlite3.Connection, digest: str, found: Found, now: str) -> list[Label]:
    """Keep a read of the file and replace its embedded labels. The caller commits."""
    conn.execute(
        "INSERT OR REPLACE INTO embedded (asset_hash, version, read_at, found_json, error) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            digest,
            VERSION,
            now,
            json.dumps(found.to_json(), sort_keys=True, ensure_ascii=False),
            found.error,
        ),
    )
    conn.execute("DELETE FROM tag WHERE asset_hash = ? AND origin = ?", (digest, ORIGIN))
    made = labels(found)
    conn.executemany(
        "INSERT OR IGNORE INTO tag (asset_hash, name, value, origin, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        [(digest, label.name, label.value, ORIGIN, label.confidence) for label in made],
    )
    return made
