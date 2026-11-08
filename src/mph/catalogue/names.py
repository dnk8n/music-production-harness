"""Priors parsed from folder and file names (REQ-CAT-03).

Pack naming is a cheap, mostly-right head start: "Acid Techno (Ab, 131bpm)",
"MXE_128_Vocals_19", "Kick Snare 0005". Everything here is a *prior* with a
confidence below 1.0; audio analysis later confirms or contradicts it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Bump when parsing rules change: the next scan rebuilds every name-derived tag.
VERSION = 2

FOLDER_CONFIDENCE = 0.5  # the folder directly containing the file
FOLDER_DECAY = 0.1  # per level further up the tree
FOLDER_FLOOR = 0.2  # pack-level names ("Stab Bass Sample Pack") still count a little
FILENAME_CONFIDENCE = 0.6
WEAK = 0.3  # bare numbers that might be a tempo, or might be an index

PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MINOR = {"m", "min", "minor"}
MAJOR = {"maj", "major"}
KEY_TOKEN = re.compile(r"^([A-Ga-g])([#b]?)(m|min|minor|maj|major)?$")
BPM_EXPLICIT = re.compile(r"(\d{2,3})\s*bpm", re.IGNORECASE)
BPM_BARE = re.compile(r"(?:^|[_\s-])(\d{2,3})(?=[_\s-]|$)")
TOKENS = re.compile(r"[a-z]+")
SPLIT = re.compile(r"[\s_()\[\],.-]+")
BPM_RANGE = range(60, 201)
BARE_BPM_RANGE = range(70, 181)  # stricter: a bare number is often an index

# word -> (tag name, value). Plurals are handled by stripping a trailing "s".
VOCAB: dict[str, tuple[str, str]] = {
    **dict.fromkeys(("kick", "kik", "bd"), ("role", "kick")),
    **dict.fromkeys(("snare", "snr", "sd"), ("role", "snare")),
    **dict.fromkeys(("clap", "clp"), ("role", "clap")),
    **dict.fromkeys(("hat", "hh", "hihat", "chh", "closedhat"), ("role", "hat")),
    **dict.fromkeys(("ohh", "openhat"), ("role", "hat_open")),
    **dict.fromkeys(("rim", "rimshot"), ("role", "rim")),
    **dict.fromkeys(("ride", "crash", "cymbal"), ("role", "cymbal")),
    "tom": ("role", "tom"),
    **dict.fromkeys(("perc", "percussion"), ("role", "perc")),
    **{w: ("role", w) for w in ("conga", "bongo", "djembe", "shaker", "clave", "cowbell")},
    **dict.fromkeys(("tamb", "tambourine"), ("role", "tambourine")),
    **dict.fromkeys(("drum", "top", "groove"), ("role", "drums")),
    **dict.fromkeys(("break", "breakbeat", "breaks"), ("role", "break")),
    **dict.fromkeys(("bass", "sub", "bassline"), ("role", "bass")),
    **dict.fromkeys(("pad", "atmos", "atmosphere", "drone"), ("role", "pad")),
    "chord": ("role", "chord"),
    "stab": ("role", "stab"),
    **dict.fromkeys(("lead", "synth", "melody", "melodic", "arp"), ("role", "melodic")),
    **dict.fromkeys(("fx", "sfx", "riser", "sweep", "impact", "noise"), ("role", "fx")),
    **dict.fromkeys(("vocal", "vox", "voice", "spoken", "acapella"), ("role", "vocal")),
    "loop": ("form", "loop"),
    **dict.fromkeys(("hit", "oneshot", "shot"), ("form", "oneshot")),
}


@dataclass(frozen=True, slots=True, order=True)
class Prior:
    name: str  # bpm | key | role | form | pack
    value: str
    origin: str  # folder | filename
    confidence: float


def parse_key(tokens: list[str]) -> str | None:
    """'Ab' + 'm' style tokens -> canonical 'G#:minor'; None if no key found."""
    for index, token in enumerate(tokens):
        match = KEY_TOKEN.match(token)
        if not match or match.group(1).islower():  # "am", "db" are words, not keys
            continue
        letter, accidental, mode = match.group(1).upper(), match.group(2), match.group(3)
        following = (
            tokens[index + 1].lower().rstrip("0123456789") if index + 1 < len(tokens) else ""
        )
        if mode is None and following in MINOR | MAJOR:  # "F Maj7", "C Minor"
            mode = following
        if mode is None and not accidental:
            continue  # a bare letter ("Pad A") is too ambiguous
        pitch = (PITCH[letter] + {"#": 1, "b": -1}.get(accidental, 0)) % 12
        quality = "minor" if mode and mode.lower() in MINOR else "major"
        return f"{SHARP_NAMES[pitch]}:{quality}"
    return None


def _tempo_priors(text: str, origin: str, confidence: float) -> set[Prior]:
    explicit = BPM_EXPLICIT.search(text)
    if explicit and int(explicit.group(1)) in BPM_RANGE:
        return {Prior("bpm", explicit.group(1), origin, confidence)}
    if origin == "filename":
        for bare in BPM_BARE.findall(text):
            if int(bare) in BARE_BPM_RANGE:
                return {Prior("bpm", bare, origin, WEAK)}
    return set()


def _word_priors(text: str, origin: str, confidence: float) -> set[Prior]:
    hits = set()
    for word in TOKENS.findall(text.lower()):
        hit = VOCAB.get(word) or (VOCAB.get(word[:-1]) if word.endswith("s") else None)
        if hit:
            hits.add(hit)
    # A name listing several roles ("DRUM_HITS_&_FX") says less about each of them.
    roles = sum(1 for name, _ in hits if name == "role") or 1
    return {
        Prior(name, value, origin, round(confidence / roles if name == "role" else confidence, 3))
        for name, value in hits
    }


def _component_priors(text: str, origin: str, confidence: float) -> set[Prior]:
    priors = _tempo_priors(text, origin, confidence) | _word_priors(text, origin, confidence)
    key = parse_key([t for t in SPLIT.split(text) if t])
    if key:
        priors.add(Prior("key", key, origin, confidence))
    return priors


def folder_confidence(depth_from_file: int) -> float:
    """The folder holding the file says most; each level further up says less."""
    return round(max(FOLDER_CONFIDENCE - FOLDER_DECAY * (depth_from_file - 1), FOLDER_FLOOR), 3)


def priors_for(rel_path: str) -> list[Prior]:
    """All priors for a path relative to its source root, strongest first."""
    path = PurePosixPath(rel_path)
    priors: set[Prior] = set()
    if len(path.parts) > 1:
        priors.add(Prior("pack", path.parts[0], "folder", 1.0))
    folders = path.parts[:-1]
    for index, folder in enumerate(folders):
        priors |= _component_priors(folder, "folder", folder_confidence(len(folders) - index))
    priors |= _component_priors(path.stem, "filename", FILENAME_CONFIDENCE)
    return sorted(priors, key=lambda p: (p.name, -p.confidence, p.value, p.origin))
