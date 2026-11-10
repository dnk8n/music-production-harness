"""Benchmark scores as data (spec/components/benchmarks.md, REQ-BEN-04).

A score is a TOML file: tempo, swing, bars, and one table per voice. Each
voice has a 16-step grid (the same notation as the spec), or a ``pulse``
that repeats every N sixteenths across bar lines (polyrhythm), or a list of
break ``slices``. Velocity rules (alternation, ramps, seeded humanisation)
are optional. A melodic voice (BM-BASS-01) has 16 ``notes`` with a ``gate``
grid, plus optional ``accent`` and ``slide`` rows and synth ``params``.
``events`` turns a score into hits at exact sample positions, so rendering
and measuring both start from the same list.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import tomllib
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from importlib import resources
from typing import Any

STEPS = 16
VELOCITY = {"X": 1.0, "x": 0.8, "o": 0.8}
STRAIGHT = 0.5
NOTE_LENGTH = 0.5  # of a step, for notes that don't slide
PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DEFAULT_TAIL_S = 1.0
LEGACY_MAIN = "main"  # the variant of renders from before takes were equal
ENGINE_NAMES = ("renoise", "supercollider")


class ScoreError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Ramp:
    """Over the last ``steps`` steps of every ``every_bars``-th bar, velocity
    rises linearly from ``start`` to ``end``."""

    every_bars: int
    steps: int
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Sound:
    """What a voice asks for: a catalogue search, or a SuperCollider voice."""

    role: str = ""
    words: tuple[str, ...] = ()
    synth: str = ""  # forces a synth voice, e.g. "mph_kick"; "auto" picks one by role
    pick: int = 1  # take the Nth best catalogue match


@dataclass(frozen=True, slots=True)
class Voice:
    name: str
    sound: Sound
    grid: str = ""  # 16 symbols
    pulse: int = 0  # hit every N sixteenths, continuing across bars
    slices: tuple[int, ...] = ()  # 1-based slice numbers, one per 8th note
    velocities: tuple[float, ...] = ()  # cycled per hit, overriding the grid symbol
    ramp: Ramp | None = None
    humanise: float = 0.0  # +/- fraction of velocity, seeded
    chokes: tuple[str, ...] = ()  # voices this one cuts short
    seconds: float = 0.0  # length limit for one-shots (0 = whole sound)
    gain_db: float = 0.0  # on top of the role's level target (REQ-BEN-09)
    notes: tuple[str, ...] = ()  # 16 note names for a melodic voice; ``grid`` is its gate
    accents: str = ""  # 16 of "X" or "."
    slides: str = ""  # 16 of "S" or ".": hold into the next step and glide
    params: tuple[tuple[str, float], ...] = ()  # synth controls, e.g. ("cutoff", 420.0)

    @property
    def melodic(self) -> bool:
        return bool(self.notes)


Params = tuple[tuple[str, float], ...]
HOLD = "-"  # in a melodic gate: hold the note before it for one more step


@dataclass(frozen=True, slots=True)
class Sidechain:
    """Targets dip in level on every hit of the trigger voice (REQ-BEN-12)."""

    trigger: str
    targets: tuple[str, ...]
    depth_db: float = 6.0
    attack_ms: float = 2.0
    release_ms: float = 200.0

    @property
    def active(self) -> bool:
        return self.depth_db > 0


@dataclass(frozen=True, slots=True)
class Variant:
    """Another way of making the same benchmark (REQ-BEN-07)."""

    name: str
    title: str
    engine: str = ""  # "" = the engine REQ-BEN-10 picks
    sounds: tuple[tuple[str, Sound], ...] = ()  # voice -> its replacement sound
    gains: tuple[tuple[str, float], ...] = ()  # voice -> gain_db
    every_voice_synth: bool = False
    params: tuple[tuple[str, Params], ...] = ()  # voice -> synth controls changed
    sidechain_depth_db: float | None = None  # None keeps the score's own depth

    @property
    def as_scored(self) -> bool:
        """True when it plays the score's own sounds, levels and controls."""
        changed = self.sounds or self.gains or self.params or self.every_voice_synth
        return not changed and self.sidechain_depth_db is None


@dataclass(frozen=True, slots=True)
class Score:
    id: str
    bpm: float
    bars: int
    voices: tuple[Voice, ...]
    swing: float = STRAIGHT
    seed: int = 1
    tail_s: float = DEFAULT_TAIL_S
    title: str = ""
    hear: str = ""  # the spec's "Hear:" text, for the listening page
    variants: tuple[Variant, ...] = ()  # declared in the score, besides the standard ones
    variant: str = ""  # which one this is; "" = the score as written, before a variant
    project: str = ""  # the group it belongs to (REQ-BEN-11)
    sidechain: Sidechain | None = None

    @property
    def step_s(self) -> float:
        return 60.0 / self.bpm / 4

    @property
    def length_s(self) -> float:
        return self.bars * STEPS * self.step_s + self.tail_s

    @property
    def melodic(self) -> bool:
        """Melodic voices glide and filter per note, so only SuperCollider plays them yet."""
        return any(v.melodic for v in self.voices)

    @property
    def supercollider_only(self) -> str:
        """Why only SuperCollider can render this score yet, or "" if Renoise can too."""
        if self.melodic:
            return "a melodic part (glides and filter envelopes)"
        if self.sidechain:
            return "a sidechain"
        return ""

    def voice(self, name: str) -> Voice:
        for voice in self.voices:
            if voice.name == name:
                return voice
        raise ScoreError(f"{self.id} has no voice {name!r}")

    def digest(self, extra: dict[str, Any] | None = None) -> str:
        """Hash of the score and anything else that shapes the render."""
        shape = asdict(self)
        shape.pop("variants")  # other variants don't shape this render
        shape.pop("project")  # nor does the group it is listed in
        if shape["sidechain"] is None:
            shape.pop("sidechain")  # so scores without one keep the hashes they had
        payload = json.dumps({"score": shape, "extra": extra or {}}, sort_keys=True)
        return hashlib.blake2b(payload.encode(), digest_size=8).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    voice: str
    step: int  # global sixteenth index from the start
    time_s: float  # after swing
    velocity: float
    slice: int = 0  # 1-based break slice, 0 for ordinary hits
    pitch: int = 0  # MIDI note for melodic voices
    accent: bool = False
    tie: bool = False  # held into the next note, which glides instead of retriggering
    length_s: float = 0.0  # how long a melodic note is held (0 for hits)

    @property
    def kind(self) -> tuple[int, int, bool]:
        """What makes two hits of a voice sound alike, for templates."""
        return self.slice, self.pitch, self.accent

    def sample(self, rate: int) -> int:
        return round(self.time_s * rate)


# --- loading -------------------------------------------------------------------------


def cells(grid: str, *, hold: bool = False) -> list[str]:
    allowed = (*VELOCITY, ".", *((HOLD,) if hold else ()))
    symbols = grid.split()
    if len(symbols) != STEPS or any(s not in allowed for s in symbols):
        shown = " ".join(allowed)
        raise ScoreError(f"a grid needs {STEPS} symbols from {shown}: {grid!r}")
    return symbols


def midi(note: str) -> int:
    """'C2' -> 36, 'D#2' -> 39, 'Bb1' -> 34."""
    match = re.fullmatch(r"([A-G])([#b]?)(-?\d)", note)
    if not match:
        raise ScoreError(f"not a note: {note!r} (try C2, D#2, Bb1)")
    letter, sign, octave = match.groups()
    shift = {"#": 1, "b": -1}.get(sign, 0)
    return PITCH_CLASS[letter] + shift + 12 * (int(octave) + 1)


def _row(text: str, allowed: str, name: str) -> str:
    symbols = text.split()
    if text and (len(symbols) != STEPS or any(s not in allowed for s in symbols)):
        raise ScoreError(f"voice {name}: a row needs {STEPS} symbols from {allowed!r}: {text!r}")
    return " ".join(symbols)


def _params(raw: dict[str, Any]) -> Params:
    return tuple(sorted((str(k), float(v)) for k, v in raw.items()))


def _sound(raw: dict[str, Any]) -> Sound:
    words = raw.get("words", ())
    return Sound(
        str(raw.get("role", "")),
        tuple(str(w) for w in words),
        str(raw.get("synth", "")),
        int(raw.get("pick", 1)),
    )


def _variant(name: str, raw: dict[str, Any], voices: dict[str, Voice]) -> Variant:
    engine = str(raw.get("engine", ""))
    if engine and engine not in ENGINE_NAMES:
        raise ScoreError(f"variant {name}: engine must be one of {', '.join(ENGINE_NAMES)}")
    changes = raw.get("voices", {})
    if unknown := set(changes) - set(voices):
        raise ScoreError(f"variant {name} changes unknown voices {sorted(unknown)}")
    sounds = []
    for voice, change in changes.items():
        if "sound" in change:
            merged = {**asdict(voices[voice].sound), **change["sound"]}
            sounds.append((voice, _sound(merged)))
    gains = [(v, float(c["gain_db"])) for v, c in changes.items() if "gain_db" in c]
    params = [(v, _params(c["params"])) for v, c in changes.items() if "params" in c]
    depth = raw.get("sidechain", {}).get("depth_db")
    return Variant(
        name,
        str(raw.get("title", name)),
        engine,
        tuple(sounds),
        tuple(gains),
        params=tuple(params),
        sidechain_depth_db=None if depth is None else float(depth),
    )


def _voice(name: str, raw: dict[str, Any]) -> Voice:
    ramp = raw.get("ramp")
    voice = Voice(
        name=name,
        sound=_sound(raw.get("sound", {})),
        grid=str(raw.get("grid", "")),
        pulse=int(raw.get("pulse", 0)),
        slices=tuple(int(n) for n in raw.get("slices", ())),
        velocities=tuple(float(v) for v in raw.get("velocities", ())),
        ramp=Ramp(**ramp) if ramp else None,
        humanise=float(raw.get("humanise", 0.0)),
        chokes=tuple(str(v) for v in raw.get("chokes", ())),
        seconds=float(raw.get("seconds", 0.0)),
        gain_db=float(raw.get("gain_db", 0.0)),
        notes=tuple(str(raw.get("notes", "")).split()),
        accents=_row(str(raw.get("accent", "")), "X.", name),
        slides=_row(str(raw.get("slide", "")), "S.", name),
        params=_params(raw.get("params", {})),
    )
    if voice.notes:
        voice = _melodic_voice(voice, str(raw.get("gate", "")))
    elif voice.accents or voice.slides:
        raise ScoreError(f"voice {name}: accent and slide rows need notes")
    if sum(bool(x) for x in (voice.grid, voice.pulse, voice.slices)) != 1:
        raise ScoreError(f"voice {name}: give exactly one of grid, pulse or slices")
    if voice.grid:
        cells(voice.grid, hold=voice.melodic)
    return voice


def _melodic_voice(voice: Voice, gate: str) -> Voice:
    if len(voice.notes) != STEPS:
        raise ScoreError(f"voice {voice.name}: notes needs {STEPS} entries")
    if not gate:
        raise ScoreError(f"voice {voice.name}: a melodic voice needs a gate row")
    for note, symbol in zip(voice.notes, cells(gate, hold=True), strict=True):
        if note != HOLD or symbol in VELOCITY:  # a held or silent step needn't name a note
            midi(note)
    return replace(voice, grid=gate)


def _sidechain(raw: dict[str, Any], voices: dict[str, Voice]) -> Sidechain:
    found = Sidechain(
        trigger=str(raw["trigger"]),
        targets=tuple(str(t) for t in raw["targets"]),
        depth_db=float(raw.get("depth_db", 6.0)),
        attack_ms=float(raw.get("attack_ms", 2.0)),
        release_ms=float(raw.get("release_ms", 200.0)),
    )
    if unknown := {found.trigger, *found.targets} - set(voices):
        raise ScoreError(f"the sidechain names unknown voices {sorted(unknown)}")
    if not found.targets or found.trigger in found.targets:
        raise ScoreError("the sidechain needs targets other than its trigger")
    if found.depth_db < 0 or found.attack_ms <= 0 or found.release_ms <= 0:
        raise ScoreError(
            "the sidechain needs a depth of 0 dB or more, and a positive attack and release"
        )
    return found


def parse(raw: dict[str, Any]) -> Score:
    try:
        voices = tuple(_voice(name, table) for name, table in raw["voices"].items())
        score = Score(
            id=str(raw["id"]),
            bpm=float(raw["bpm"]),
            bars=int(raw["bars"]),
            voices=voices,
            swing=float(raw.get("swing", STRAIGHT)),
            seed=int(raw.get("seed", 1)),
            tail_s=float(raw.get("tail_s", DEFAULT_TAIL_S)),
            title=str(raw.get("title", "")),
            hear=str(raw.get("hear", "")),
            project=str(raw.get("project", "")),
        )
        by_name = {v.name: v for v in voices}
        declared = tuple(
            _variant(str(name), table, by_name) for name, table in raw.get("variants", {}).items()
        )
        sidechain = _sidechain(raw["sidechain"], by_name) if "sidechain" in raw else None
        score = replace(score, variants=declared, sidechain=sidechain)
    except (KeyError, TypeError) as exc:
        raise ScoreError(f"score is missing or mistypes {exc}") from exc
    if not STRAIGHT <= score.swing < 1:
        raise ScoreError(f"{score.id}: swing must be from 0.5 (straight) to under 1")
    names = {v.name for v in voices}
    for voice in voices:
        if unknown := set(voice.chokes) - names:
            raise ScoreError(f"{score.id}: {voice.name} chokes unknown voices {sorted(unknown)}")
    return score


def load_text(text: str) -> Score:
    try:
        return parse(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise ScoreError(str(exc)) from exc


def available() -> list[str]:
    folder = resources.files("mph.bench").joinpath("scores")
    return sorted(
        p.name.removesuffix(".toml") for p in folder.iterdir() if p.name.endswith(".toml")
    )


def load(benchmark_id: str) -> Score:
    if benchmark_id not in available():
        raise ScoreError(f"no score for {benchmark_id} (have: {', '.join(available())})")
    path = resources.files("mph.bench").joinpath("scores", f"{benchmark_id}.toml")
    return load_text(path.read_text("utf-8"))


# --- events ---------------------------------------------------------------------------


def swung_time(step: int, step_s: float, swing: float) -> float:
    """Every odd sixteenth lands ``swing`` of the way through its 8th note."""
    pair, offbeat = divmod(step, 2)
    return pair * 2 * step_s + (2 * step_s * swing if offbeat else 0.0)


def _hits(voice: Voice, bars: int) -> Iterator[tuple[int, float, int]]:
    """(global step, velocity from the symbol, slice) before velocity rules."""
    total = bars * STEPS
    if voice.pulse:
        yield from ((step, VELOCITY["x"], 0) for step in range(0, total, voice.pulse))
    elif voice.slices:
        repeats = total // 2 // len(voice.slices) + 1  # one slice per 8th note
        for n, number in enumerate(voice.slices * repeats):
            if n * 2 < total:
                yield n * 2, VELOCITY["x"], number
    else:
        symbols = cells(voice.grid)
        for step in range(total):
            symbol = symbols[step % STEPS]
            if symbol != ".":
                yield step, VELOCITY[symbol], 0


def _velocity(voice: Voice, n: int, step: int, base: float, rng: random.Random) -> float:
    velocity = voice.velocities[n % len(voice.velocities)] if voice.velocities else base
    ramp = voice.ramp
    if ramp:
        bar, position = divmod(step, STEPS)
        first = STEPS - ramp.steps
        if (bar + 1) % ramp.every_bars == 0 and position >= first:
            fraction = (position - first) / max(ramp.steps - 1, 1)
            velocity = ramp.start + (ramp.end - ramp.start) * fraction
    if voice.humanise:
        velocity *= 1 + rng.uniform(-voice.humanise, voice.humanise)
    return round(min(max(velocity, 0.0), 1.0), 4)


def _melodic(score: Score, voice: Voice) -> list[Event]:
    """303 rules: a slid note holds for the whole step and, if the next step
    plays, ties into it so that note glides instead of retriggering. A note
    followed by "-" steps is held through them (a pad, BM-SC-01)."""
    gate = cells(voice.grid, hold=True)
    accents = voice.accents.split() or ["."] * STEPS
    slides = voice.slides.split() or ["."] * STEPS
    total = score.bars * STEPS
    found = []
    for step in range(total):
        at = step % STEPS
        if gate[at] in (".", HOLD):
            continue
        held = 0  # steps the note is held for after its own, by "-"
        while step + held + 1 < total and gate[(step + held + 1) % STEPS] == HOLD:
            held += 1
        slide = slides[at] == "S" and not held
        following = step + 1 < total and gate[(at + 1) % STEPS] not in (".", HOLD)
        steps = held + 1 if held else (1.0 if slide else NOTE_LENGTH)
        found.append(
            Event(
                voice.name,
                step,
                swung_time(step, score.step_s, score.swing),
                1.0,
                pitch=midi(voice.notes[at]),
                accent=accents[at] == "X",
                tie=slide and following,
                length_s=steps * score.step_s,
            )
        )
    return found


def events(score: Score) -> list[Event]:
    found: list[Event] = []
    for index, voice in enumerate(score.voices):
        if voice.melodic:
            found += _melodic(score, voice)
            continue
        rng = random.Random(score.seed * 1000 + index)  # noqa: S311 - musical, not crypto
        for n, (step, base, number) in enumerate(_hits(voice, score.bars)):
            time_s = swung_time(step, score.step_s, score.swing)
            velocity = _velocity(voice, n, step, base, rng)
            found.append(Event(voice.name, step, time_s, velocity, number))
    found.sort(key=lambda e: (e.time_s, e.voice))
    return found


def bar_one(score: Score, voice: Voice) -> str:
    """Bar 1 of a voice in grid notation, for checking against the spec."""
    hits = {e.step: e for e in events(score) if e.voice == voice.name and e.step < STEPS}
    symbols = []
    for step in range(STEPS):
        hit = hits.get(step)
        if hit is None:
            symbols.append(".")
        elif voice.grid:
            symbols.append(cells(voice.grid)[step])
        else:
            symbols.append("x")
    return " ".join(symbols)


# --- variants (REQ-BEN-07) -------------------------------------------------------------


def standard_variants(score: Score) -> list[Variant]:
    """Equal takes, none of them "main" (REQ-BEN-07)."""
    supercollider = Variant("supercollider", "in SuperCollider", engine="supercollider")
    if score.supercollider_only:  # it always renders there (REQ-BEN-10, REQ-BEN-12)
        return [supercollider]
    found = [Variant("renoise", "in Renoise", engine="renoise"), supercollider]
    if not any(v.slices for v in score.voices):
        found.append(
            Variant("synth-kit", "every part a SuperCollider synth", every_voice_synth=True)
        )
    return found


def all_variants(score: Score) -> list[Variant]:
    standard = standard_variants(score)
    names = {v.name for v in standard}
    for declared in score.variants:
        if declared.name in names:
            raise ScoreError(f"{score.id}: variant {declared.name} is a standard one")
    return [*standard, *score.variants]


def variant_of(score: Score, name: str) -> tuple[Score, Variant]:
    """The score as this variant makes it, and the variant (for its engine)."""
    found = next((v for v in all_variants(score) if v.name == name), None)
    if found is None:
        names = ", ".join(v.name for v in all_variants(score))
        raise ScoreError(f"{score.id} has no variant {name!r} (have: {names})")
    sounds, gains, params = dict(found.sounds), dict(found.gains), dict(found.params)
    voices = []
    for voice in score.voices:
        sound = sounds.get(voice.name, voice.sound)
        if found.every_voice_synth:
            sound = replace(sound, synth=sound.synth or "auto")
        changed = _params({**dict(voice.params), **dict(params.get(voice.name, ()))})
        voices.append(
            replace(
                voice,
                sound=sound,
                gain_db=gains.get(voice.name, voice.gain_db),
                params=changed,
            )
        )
    sidechain = score.sidechain
    if sidechain and found.sidechain_depth_db is not None:
        sidechain = replace(sidechain, depth_db=found.sidechain_depth_db)
    return replace(score, voices=tuple(voices), variant=found.name, sidechain=sidechain), found
