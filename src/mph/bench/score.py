"""Benchmark scores as data (spec/components/benchmarks.md, REQ-BEN-04).

A score is a TOML file: tempo, swing, bars, and one table per voice. Each
voice has a grid (the same notation as the spec) of 16 sixteenths or 12
eighth-note triplets (REQ-BEN-13), or a ``pulse`` that repeats every N
sixteenths across bar lines (polyrhythm), or a list of break ``slices``.
Velocity rules (alternation, ramps, seeded humanisation) are optional. A
melodic voice (BM-BASS-01) has 16 ``notes`` with a ``gate`` grid, plus
optional ``accent`` and ``slide`` rows and synth ``params``. ``swing`` moves
sixteenths, and ``feel`` places triplets within their beat. ``events`` turns
a score into hits at exact sample positions, so rendering and measuring both
start from the same list. A voice's sound is a catalogue search, a synth, or
one catalogue sound pinned by its hash with the reason it was picked
(REQ-BEN-16).

A track (spec/components/tracks.md) adds sections that say which voices play
in which bars (REQ-TRK-01), clip voices that play whole bars of a recording,
cued by the sections or by the voice's own ``cues`` (REQ-TRK-02, REQ-TRK-07),
excerpts: a block of a track's bars as a score of its own (REQ-TRK-03), and a
sample rate of its own (REQ-TRK-06).

Rows can hold several bars, split by ``|`` (REQ-BEN-17), and a melodic voice
can play a chord shape on every note (REQ-BEN-18). A score can mix its voices
through strips, sends and automation (spec/components/mix.md, ``mix.py``).
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
from typing import Any, TypeGuard

from mph.bench import mix
from mph.bench.mix import MixError, Send, Strip

STEPS = 16  # sixteenths in a bar: the usual grid
TRIPLETS = 12  # eighth-note triplets in a bar: a triplet grid (REQ-BEN-13)
BEATS = 4  # beats in a bar
PULSES = TRIPLETS // BEATS  # triplet pulses in a beat
VELOCITY = {"X": 1.0, "x": 0.8, "o": 0.8}
STRAIGHT = 0.5
EVEN: tuple[float, ...] = (1.0, 1.0, 1.0)  # a feel with equal thirds
NOTE_LENGTH = 0.5  # of a step, for notes that don't slide
PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DEFAULT_TAIL_S = 1.0
LEGACY_MAIN = "main"  # the variant of renders from before takes were equal
ENGINE_NAMES = ("renoise", "supercollider")
STANDARD_TAKES = ("renoise", "supercollider", "synth-kit")  # REQ-BEN-07
SOUND_KEYS = ("role", "words", "synth", "pick", "hash", "why")
HASH = re.compile(r"[0-9a-f]{64}")  # a catalogue sound's content hash (BLAKE2b-256)
CUE = ":"  # in a section's play list, "source:train" cues the clip "train" (REQ-TRK-02)
EXCERPT_KEYS = frozenset({"id", "title", "hear", "project", "excerpt"})  # REQ-TRK-03
BAR = "|"  # between the bars of a row that holds several (REQ-BEN-17)
MAX_CHORD = 8  # notes in one chord shape (REQ-BEN-18)
CHORD_RANGE = (-24, 36)  # semitones from the note
RATES = (44100, 48000, 88200, 96000, 176400, 192000)  # a track's own rate (REQ-TRK-06)


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
    """What a voice asks for: a catalogue search, a SuperCollider voice, or one
    catalogue sound by its hash (REQ-BEN-16)."""

    role: str = ""
    words: tuple[str, ...] = ()
    synth: str = ""  # forces a synth voice, e.g. "mph_kick"; "auto" picks one by role
    pick: int = 1  # take the Nth best catalogue match
    hash: str = ""  # exactly this catalogue sound; the search above is its fallback
    why: str = ""  # why the sound or synth was picked, for people


def recorded(sound: Sound) -> dict[str, Any]:
    """A sound as score hashes and reports record it. ``why`` is for people,
    and a sound with no hash is recorded as before hashes could be pinned, so
    renders and the requests passed takes keep (REQ-BEN-15) stay put."""
    found = asdict(sound)
    found.pop("why")
    if not sound.hash:
        found.pop("hash")
    return found


@dataclass(frozen=True, slots=True)
class Clip:
    """Bars ``first`` to ``last`` of a clip voice's recording (REQ-TRK-02)."""

    name: str
    first: int
    last: int

    @property
    def bars(self) -> int:
        return self.last - self.first + 1


@dataclass(frozen=True, slots=True)
class Section:
    """Bars of an arranged score and the voices that play in them (REQ-TRK-01)."""

    name: str
    first: int
    last: int
    play: tuple[str, ...] = ()  # voice names; "voice:clip" cues one of its clips

    def cue(self, voice: str) -> str | None:
        """None if the section leaves the voice out, "" if it lists the voice on
        its own, or the name of the clip it cues."""
        for entry in self.play:
            name, _, clip = entry.partition(CUE)
            if name == voice:
                return clip
        return None


@dataclass(frozen=True, slots=True)
class Excerpt:
    """Which bars of which track an excerpt plays (REQ-TRK-03)."""

    of: str
    first: int
    last: int


@dataclass(frozen=True, slots=True)
class Play:
    """One cue of a clip: the clip, the bar it starts on, and how many bars it
    plays before it ends or is stopped (REQ-TRK-02)."""

    voice: str
    clip: int  # into the voice's clips, from 0
    bar: int  # of the score, from 1
    bars: int


@dataclass(frozen=True, slots=True)
class Voice:
    name: str
    sound: Sound
    grid: str = ""  # 16 symbols, or 12 for eighth-note triplets
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
    clips: tuple[Clip, ...] = ()  # bars of the voice's recording, cued by sections (REQ-TRK-02)
    bar_one_s: float = 0.0  # where the recording's bar 1 starts
    chord: tuple[int, ...] = ()  # semitones above each note, one synth each (REQ-BEN-18)
    cues: tuple[tuple[int, str], ...] = ()  # (bar, clip) cued inside sections (REQ-TRK-07)
    strip: Strip | None = None  # its channel strip in a mix (REQ-MIX-01)

    @property
    def melodic(self) -> bool:
        return bool(self.notes)

    @property
    def steps(self) -> int:
        """Grid steps in a bar: 16 sixteenths, or 12 triplets (REQ-BEN-13)."""
        return len(bars_of(self.grid)[0]) if self.grid else STEPS

    @property
    def length(self) -> int:
        """Grid steps before the pattern repeats: a bar's, or several bars' (REQ-BEN-17)."""
        return sum(len(bar) for bar in bars_of(self.grid)) if self.grid else STEPS

    @property
    def triplets(self) -> bool:
        return self.steps == TRIPLETS


Params = tuple[tuple[str, float], ...]
HOLD = "-"  # in a melodic gate: hold the note before it for one more step
SIDECHAIN_CHANGES = ("depth_db", "attack_ms", "hold_ms", "release_ms")  # what a variant may change


@dataclass(frozen=True, slots=True)
class Sidechain:
    """Targets dip in level on every hit of the trigger voice (REQ-BEN-12)."""

    trigger: str
    targets: tuple[str, ...]
    depth_db: float = 6.0
    attack_ms: float = 2.0
    release_ms: float = 200.0
    hold_ms: float = 0.0  # at full depth, between the attack and the release

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
    sidechain: tuple[tuple[str, float], ...] = ()  # SIDECHAIN_CHANGES field -> its new value
    grids: tuple[tuple[str, str], ...] = ()  # voice -> its replacement grid (REQ-BEN-13)
    feel: tuple[float, ...] = ()  # the score's feel replaced; () keeps it

    @property
    def as_scored(self) -> bool:
        """True when every voice plays the score's own sounds, levels, controls
        and timing. A sidechain change doesn't count: it changes what happens
        to the voices afterwards, not what they play."""
        changed = self.sounds or self.gains or self.params or self.every_voice_synth
        return not (changed or self.grids or self.feel)


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
    feel: tuple[float, ...] = EVEN  # lengths of a beat's three triplet pulses (REQ-BEN-13)
    sections: tuple[Section, ...] = ()  # REQ-TRK-01; none: every voice plays every bar
    lead_bars: int = 0  # an excerpt's patterns run on from this many bars before it
    excerpt: Excerpt | None = None  # REQ-TRK-03
    rate: int = 0  # a track's own sample rate (REQ-TRK-06); 0 = the production rate
    sends: tuple[Send, ...] = ()  # shared effects (REQ-MIX-02)
    master: Strip | None = None  # the master strip (REQ-MIX-01)

    @property
    def step_s(self) -> float:
        return 60.0 / self.bpm / 4

    @property
    def beat_s(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar_s(self) -> float:
        return STEPS * self.step_s

    @property
    def triplets(self) -> bool:
        """True when a voice plays on a triplet grid (REQ-BEN-13)."""
        return any(v.triplets for v in self.voices)

    @property
    def length_s(self) -> float:
        return self.bars * STEPS * self.step_s + self.tail_s

    @property
    def melodic(self) -> bool:
        """Melodic voices glide and filter per note, so only SuperCollider plays them yet."""
        return any(v.melodic for v in self.voices)

    @property
    def mixed(self) -> bool:
        """True when the score mixes its voices (spec/components/mix.md)."""
        return bool(self.sends or self.master or any(v.strip for v in self.voices))

    @property
    def supercollider_only(self) -> str:
        """Why only SuperCollider can render this score yet, or "" if Renoise can too."""
        if self.melodic:
            return "a melodic part (glides and filter envelopes)"
        if self.sidechain:
            return "a sidechain"
        if self.mixed:
            return "a mix (strips, sends and automation)"
        return ""

    def voice(self, name: str) -> Voice:
        for voice in self.voices:
            if voice.name == name:
                return voice
        raise ScoreError(f"{self.id} has no voice {name!r}")

    def digest(self, extra: dict[str, Any] | None = None) -> str:
        """Hash of the score and anything else that shapes the render. Whatever
        a score doesn't use is left out, so scores from before it existed keep
        the hashes they had: a sidechain, a feel (REQ-BEN-13), sections, clips
        and excerpts (REQ-TRK-01 to 03), a rate (REQ-TRK-06), chords, cues
        and a mix."""
        shape = asdict(self)
        shape.pop("variants")  # other variants don't shape this render
        shape.pop("project")  # nor does the group it is listed in
        shape["voices"] = [_voice_shape(v) for v in self.voices]
        unused = {
            "sidechain": self.sidechain is None,
            "feel": self.feel == EVEN,
            "sections": not self.sections,
            "lead_bars": not self.lead_bars,
            "excerpt": self.excerpt is None,
            "rate": not self.rate,
            "sends": not self.sends,
            "master": self.master is None,
        }
        for key in (k for k, empty in unused.items() if empty):
            shape.pop(key)
        payload = json.dumps({"score": shape, "extra": extra or {}}, sort_keys=True)
        return hashlib.blake2b(payload.encode(), digest_size=8).hexdigest()


def _voice_shape(voice: Voice) -> dict[str, Any]:
    shape = {**asdict(voice), "sound": recorded(voice.sound)}
    unused = {
        "clips": not voice.clips,
        "bar_one_s": not voice.bar_one_s,
        "chord": not voice.chord,
        "cues": not voice.cues,
        "strip": voice.strip is None,
    }
    for key in (k for k, empty in unused.items() if empty):
        shape.pop(key)
    return shape


def clip_start_s(score: Score, voice: Voice, clip: Clip) -> float:
    """Where a clip starts in its recording, in seconds (REQ-TRK-02)."""
    return voice.bar_one_s + (clip.first - 1) * score.bar_s


@dataclass(frozen=True, slots=True)
class Event:
    voice: str
    step: int  # global index into its voice's grid: sixteenths, or triplets (REQ-BEN-13)
    time_s: float  # after swing
    velocity: float
    slice: int = 0  # 1-based break slice, 0 for ordinary hits
    pitch: int = 0  # MIDI note for melodic voices
    accent: bool = False
    tie: bool = False  # held into the next note, which glides instead of retriggering
    length_s: float = 0.0  # how long a melodic note or a clip plays (0 for hits)
    clip: int = 0  # a clip voice's cue: its clip, from 1 (REQ-TRK-02)

    @property
    def kind(self) -> tuple[int, int, bool]:
        """What makes two hits of a voice sound alike, for templates."""
        return self.slice, self.pitch, self.accent

    def sample(self, rate: int) -> int:
        return round(self.time_s * rate)


# --- loading -------------------------------------------------------------------------


def bars_of(row: str) -> list[list[str]]:
    """A row's bars, each a list of its symbols: split at every "|" (REQ-BEN-17)."""
    return [part.split() for part in row.split(BAR)]


def cells(grid: str, *, hold: bool = False) -> list[str]:
    """A grid's symbols, bar after bar: 16 sixteenths a bar, or 12 eighth-note
    triplets (REQ-BEN-13), over one bar or several split by "|" (REQ-BEN-17)."""
    allowed = (*VELOCITY, ".", *((HOLD,) if hold else ()))
    bars = bars_of(grid)
    sizes = {len(bar) for bar in bars}
    symbols = [s for bar in bars for s in bar]
    if len(sizes) != 1 or sizes - {STEPS, TRIPLETS} or any(s not in allowed for s in symbols):
        shown = " ".join(allowed)
        raise ScoreError(
            f"a grid needs {STEPS} symbols from {shown} in each bar ({TRIPLETS} for "
            f"triplets), with bars split by {BAR!r}: {grid!r}"
        )
    return symbols


def _bars_text(bars: list[list[str]]) -> str:
    """A row written back with single spaces, keeping its bar lines."""
    return f" {BAR} ".join(" ".join(bar) for bar in bars)


def midi(note: str) -> int:
    """'C2' -> 36, 'D#2' -> 39, 'Bb1' -> 34."""
    match = re.fullmatch(r"([A-G])([#b]?)(-?\d)", note)
    if not match:
        raise ScoreError(f"not a note: {note!r} (try C2, D#2, Bb1)")
    letter, sign, octave = match.groups()
    shift = {"#": 1, "b": -1}.get(sign, 0)
    return PITCH_CLASS[letter] + shift + 12 * (int(octave) + 1)


def _row(text: str, allowed: str, name: str) -> str:
    """An accent or slide row, all of its bars' symbols in one line."""
    bars = bars_of(text)
    symbols = [s for bar in bars for s in bar]
    wrong = any(len(bar) != STEPS for bar in bars) or any(s not in allowed for s in symbols)
    if text and wrong:
        raise ScoreError(
            f"voice {name}: a row needs {STEPS} symbols a bar from {allowed!r}: {text!r}"
        )
    return " ".join(symbols)


def _params(raw: dict[str, Any]) -> Params:
    return tuple(sorted((str(k), float(v)) for k, v in raw.items()))


def _feel(raw: object, where: str) -> tuple[float, ...]:
    """A beat's three triplet pulses, as lengths in any unit: [41, 31, 28]."""
    numbers = raw if isinstance(raw, list) else []
    if len(numbers) != PULSES or not all(
        isinstance(v, int | float) and not isinstance(v, bool) and v > 0 for v in numbers
    ):
        raise ScoreError(f"{where}: feel needs {PULSES} lengths above 0, one per pulse of a beat")
    return tuple(float(v) for v in numbers)


def _sound(raw: dict[str, Any], where: str) -> Sound:
    if unknown := set(raw) - set(SOUND_KEYS):
        raise ScoreError(
            f"{where}: a sound takes {', '.join(SOUND_KEYS)}, not {', '.join(sorted(unknown))}"
        )
    words = raw.get("words", ())
    found = Sound(
        str(raw.get("role", "")),
        tuple(str(w) for w in words),
        str(raw.get("synth", "")),
        int(raw.get("pick", 1)),
        str(raw.get("hash", "")),
        str(raw.get("why", "")).strip(),
    )
    return _pinned(found, where) if found.hash else found


def _pinned(found: Sound, where: str) -> Sound:
    """A sound pinned by its hash (REQ-BEN-16): exactly one catalogue sound."""
    if not HASH.fullmatch(found.hash):
        raise ScoreError(f"{where}: hash needs the sound's 64 hex digits: {found.hash!r}")
    if found.synth:
        raise ScoreError(
            f"{where}: a sound pins a hash or asks for a synth, not both "
            '(over a synth, set synth = "")'
        )
    if not found.role:
        raise ScoreError(f"{where}: a pinned sound needs its role, for its level and fallback")
    if not found.why:
        raise ScoreError(f"{where}: a pinned sound needs a why, saying why it was picked")
    return found


def _grids(
    name: str, changes: dict[str, Any], voices: dict[str, Voice]
) -> tuple[tuple[str, str], ...]:
    """A variant's new drum grids, one per voice it changes (REQ-BEN-13)."""
    found = []
    for voice, change in changes.items():
        if "grid" in change:
            if not voices[voice].grid or voices[voice].melodic:
                raise ScoreError(f"variant {name}: only a drum grid can change ({voice})")
            grid = str(change["grid"])
            cells(grid)
            found.append((voice, _bars_text(bars_of(grid))))
    return tuple(found)


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
            sounds.append((voice, _sound(merged, f"variant {name}, voice {voice}")))
    gains = [(v, float(c["gain_db"])) for v, c in changes.items() if "gain_db" in c]
    params = [(v, _params(c["params"])) for v, c in changes.items() if "params" in c]
    sidechain = raw.get("sidechain", {})
    if unknown := set(sidechain) - set(SIDECHAIN_CHANGES):
        raise ScoreError(
            f"variant {name} changes {sorted(unknown)} of the sidechain; "
            f"it can change {', '.join(SIDECHAIN_CHANGES)}"
        )
    feel = _feel(raw["feel"], f"variant {name}") if "feel" in raw else ()
    return Variant(
        name,
        str(raw.get("title", name)),
        engine,
        tuple(sounds),
        tuple(gains),
        params=tuple(params),
        sidechain=tuple((k, float(sidechain[k])) for k in SIDECHAIN_CHANGES if k in sidechain),
        grids=_grids(name, changes, voices),
        feel=feel,
    )


def _bar_pair(raw: object, where: str) -> tuple[int, int]:
    """A first and last bar, [9, 12], with the last no earlier than the first."""
    pair = raw if isinstance(raw, list) else []
    whole = all(isinstance(b, int) and not isinstance(b, bool) for b in pair)
    if len(pair) != 2 or not whole or not 1 <= pair[0] <= pair[1]:  # noqa: PLR2004 - a pair
        raise ScoreError(f"{where} needs a first and last bar from 1 up, like [9, 12]: {raw!r}")
    return int(pair[0]), int(pair[1])


def _clips(name: str, raw: object) -> tuple[Clip, ...]:
    """A clip voice's named bars of its recording (REQ-TRK-02)."""
    if not isinstance(raw, dict):
        raise ScoreError(f"voice {name}: clips names bars, like {{ train = [9, 12] }}")
    found = []
    for clip, bars in raw.items():
        if CUE in clip:
            raise ScoreError(f"voice {name}: a clip's name can't hold {CUE!r}: {clip!r}")
        found.append(Clip(str(clip), *_bar_pair(bars, f"voice {name}, clip {clip}")))
    return tuple(found)


def _clip_voice(voice: Voice) -> Voice:
    """A clip voice plays its recording as it is (REQ-TRK-02)."""
    if not voice.sound.hash:
        raise ScoreError(f"voice {voice.name}: a clip voice pins its recording by its hash")
    if voice.bar_one_s < 0:
        raise ScoreError(f"voice {voice.name}: bar_one_s can't be before the recording starts")
    rules = (voice.velocities, voice.ramp, voice.humanise, voice.chokes, voice.seconds)
    if any(rules) or voice.melodic:
        raise ScoreError(
            f"voice {voice.name}: a clip voice plays its clips as they are, so it takes "
            "no notes, velocities, ramp, humanise, chokes or seconds"
        )
    return voice


def _chord(name: str, raw: object) -> tuple[int, ...]:
    """A chord shape: semitones above each note (REQ-BEN-18)."""
    shape = raw if isinstance(raw, list) else []
    low, high = CHORD_RANGE
    whole = all(isinstance(n, int) and not isinstance(n, bool) for n in shape)
    if not 1 <= len(shape) <= MAX_CHORD or not whole or len(set(shape)) != len(shape):
        raise ScoreError(
            f"voice {name}: chord lists 1 to {MAX_CHORD} different semitones above each "
            "note, like [0, 3, 7, 10]"
        )
    if not all(low <= n <= high for n in shape):
        raise ScoreError(f"voice {name}: a chord's notes are {low} to {high} semitones away")
    return tuple(int(n) for n in shape)


def _cue_pair(pair: object) -> TypeGuard[list[Any]]:
    """True for [bar, clip], like [40, "verse"]."""
    if not isinstance(pair, list) or len(pair) != 2:  # noqa: PLR2004 - a bar and a clip
        return False
    bar, clip = pair
    return isinstance(bar, int) and not isinstance(bar, bool) and isinstance(clip, str)


def _voice_cues(name: str, raw: object) -> tuple[tuple[int, str], ...]:
    """A clip voice's own cues: [bar, clip] pairs, kept in bar order (REQ-TRK-07)."""
    found = []
    for pair in raw if isinstance(raw, list) else [None]:
        if not _cue_pair(pair):
            raise ScoreError(f'voice {name}: cues lists [bar, clip] pairs, like [[40, "verse"]]')
        found.append((int(pair[0]), str(pair[1])))
    return tuple(sorted(found))


def _strip(name: str, raw: dict[str, Any]) -> Strip | None:
    try:
        return mix.strip(raw.get("mix"), raw.get("automate"), f"voice {name}")
    except MixError as exc:
        raise ScoreError(str(exc)) from exc


def _voice(name: str, raw: dict[str, Any]) -> Voice:
    ramp = raw.get("ramp")
    voice = Voice(
        name=name,
        sound=_sound(raw.get("sound", {}), f"voice {name}"),
        grid=str(raw.get("grid", "")),
        pulse=int(raw.get("pulse", 0)),
        slices=tuple(int(n) for n in raw.get("slices", ())),
        velocities=tuple(float(v) for v in raw.get("velocities", ())),
        ramp=Ramp(**ramp) if ramp else None,
        humanise=float(raw.get("humanise", 0.0)),
        chokes=tuple(str(v) for v in raw.get("chokes", ())),
        seconds=float(raw.get("seconds", 0.0)),
        gain_db=float(raw.get("gain_db", 0.0)),
        notes=tuple(n for n in str(raw.get("notes", "")).split() if n != BAR),
        accents=_row(str(raw.get("accent", "")), "X.", name),
        slides=_row(str(raw.get("slide", "")), "S.", name),
        params=_params(raw.get("params", {})),
        clips=_clips(name, raw["clips"]) if "clips" in raw else (),
        bar_one_s=float(raw.get("bar_one_s", 0.0)),
        chord=_chord(name, raw["chord"]) if "chord" in raw else (),
        cues=_voice_cues(name, raw["cues"]) if "cues" in raw else (),
        strip=_strip(name, raw),
    )
    if voice.notes:
        voice = _melodic_voice(voice, str(raw.get("gate", "")), str(raw["notes"]))
    elif voice.accents or voice.slides or voice.chord:
        raise ScoreError(f"voice {name}: accent and slide rows need notes, as chords do")
    if voice.cues and not voice.clips:
        raise ScoreError(f"voice {name}: cues cue clips, so the voice needs clips")
    if sum(bool(x) for x in (voice.grid, voice.pulse, voice.slices, voice.clips)) != 1:
        raise ScoreError(f"voice {name}: give exactly one of grid, pulse, slices or clips")
    if voice.grid:
        cells(voice.grid, hold=voice.melodic)
    return _clip_voice(voice) if voice.clips else voice


def _melodic_voice(voice: Voice, gate: str, notes: str) -> Voice:
    """A melodic voice's rows: 16 steps a bar, every row as many bars long
    (REQ-BEN-17)."""
    if not gate:
        raise ScoreError(f"voice {voice.name}: a melodic voice needs a gate row")
    bars = bars_of(gate)
    if any(len(bar) != STEPS for bar in bars):
        raise ScoreError(f"voice {voice.name}: a melodic gate row needs {STEPS} steps a bar")
    if [len(bar) for bar in bars_of(notes)] != [STEPS] * len(bars):
        raise ScoreError(
            f"voice {voice.name}: notes needs {STEPS} entries a bar, over as many bars as the gate"
        )
    for row in (voice.accents, voice.slides):
        if row and len(row.split()) != STEPS * len(bars):
            raise ScoreError(f"voice {voice.name}: accent and slide rows span the gate's bars")
    _named(voice, cells(gate, hold=True))
    return replace(voice, grid=gate)


def _named(voice: Voice, symbols: list[str]) -> None:
    """The gate plays at least one note, and every note it plays is a real one."""
    if not any(symbol in VELOCITY for symbol in symbols):
        raise ScoreError(f"voice {voice.name}: a melodic gate row needs at least one note")
    for note, symbol in zip(voice.notes, symbols, strict=True):
        if note != HOLD or symbol in VELOCITY:  # a held or silent step needn't name a note
            midi(note)


def _sidechain(raw: dict[str, Any], voices: dict[str, Voice]) -> Sidechain:
    found = Sidechain(
        trigger=str(raw["trigger"]),
        targets=tuple(str(t) for t in raw["targets"]),
        depth_db=float(raw.get("depth_db", 6.0)),
        attack_ms=float(raw.get("attack_ms", 2.0)),
        release_ms=float(raw.get("release_ms", 200.0)),
        hold_ms=float(raw.get("hold_ms", 0.0)),
    )
    if unknown := {found.trigger, *found.targets} - set(voices):
        raise ScoreError(f"the sidechain names unknown voices {sorted(unknown)}")
    if not found.targets or found.trigger in found.targets:
        raise ScoreError("the sidechain needs targets other than its trigger")
    return _shaped(found, "the sidechain")


def _shaped(found: Sidechain, where: str) -> Sidechain:
    if found.depth_db < 0 or found.attack_ms <= 0 or found.release_ms <= 0 or found.hold_ms < 0:
        raise ScoreError(
            f"{where} needs a depth of 0 dB or more, a positive attack and release, "
            "and a hold of 0 ms or more"
        )
    return found


def _changed_sidechain(sidechain: Sidechain | None, variant: Variant) -> Sidechain | None:
    """The score's sidechain with a variant's changes (REQ-BEN-07, REQ-BEN-12)."""
    if not variant.sidechain:
        return sidechain
    if sidechain is None:
        raise ScoreError(f"variant {variant.name} changes a sidechain the score doesn't have")
    changes = dict(variant.sidechain)
    changed = replace(
        sidechain,
        depth_db=changes.get("depth_db", sidechain.depth_db),
        attack_ms=changes.get("attack_ms", sidechain.attack_ms),
        release_ms=changes.get("release_ms", sidechain.release_ms),
        hold_ms=changes.get("hold_ms", sidechain.hold_ms),
    )
    return _shaped(changed, f"variant {variant.name}'s sidechain")


def _section(raw: dict[str, Any], n: int) -> Section:
    where = f"section {n}"
    if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
        raise ScoreError(f"{where} needs a name, its bars and the voices that play in it")
    play = raw.get("play", [])
    if not isinstance(play, list) or not all(isinstance(p, str) for p in play):
        raise ScoreError(f'{where}: play lists voice names, like ["kick", "source:train"]')
    first, last = _bar_pair(raw.get("bars"), f"{where} ({raw['name']})")
    return Section(str(raw["name"]).strip(), first, last, tuple(play))


def _entry_problem(entry: str, voices: dict[str, Voice]) -> str:
    """What is wrong with one entry of a section's play list, or ""."""
    name, cued, clip = entry.partition(CUE)
    voice = voices.get(name)
    if voice is None:
        return f"names an unknown voice {name!r}"
    if cued and not voice.clips:
        return f"cues {clip!r} on {name}, which plays no clips"
    if cued and clip not in {c.name for c in voice.clips}:
        return f"cues {clip!r}, which isn't one of {name}'s clips"
    return ""


def _check_section(section: Section, voices: dict[str, Voice], score_id: str) -> None:
    where = f"{score_id}: section {section.name!r}"
    for entry in section.play:
        if problem := _entry_problem(entry, voices):
            raise ScoreError(f"{where} {problem}")
    names = [entry.partition(CUE)[0] for entry in section.play]
    if len(set(names)) != len(names):
        raise ScoreError(f"{where} lists a voice twice")


def _arranged(score: Score) -> Score:
    """Sections must run from bar 1 to the last bar, one after another, and
    only a score with sections can have clip voices (REQ-TRK-01, REQ-TRK-02)."""
    if not score.sections:
        if clipped := [v.name for v in score.voices if v.clips]:
            raise ScoreError(f"{score.id}: sections cue clips, so {clipped[0]} needs sections")
        return score
    voices = {v.name: v for v in score.voices}
    expected = 1
    for section in score.sections:
        if section.first != expected:
            raise ScoreError(
                f"{score.id}: section {section.name!r} starts at bar {section.first}, "
                f"but sections follow each other, so it should start at bar {expected}"
            )
        _check_section(section, voices, score.id)
        expected = section.last + 1
    if expected != score.bars + 1:
        raise ScoreError(f"{score.id}: the sections end at bar {expected - 1}, not {score.bars}")
    for voice in (v for v in score.voices if v.cues):
        _check_cues(score, voice)
    plays(score)  # a section that lets a clip carry on needs one playing
    return score


def _section_at(score: Score, bar: int) -> Section:
    return next(s for s in score.sections if s.first <= bar <= s.last)


def _check_cues(score: Score, voice: Voice) -> None:
    """A voice's own cues name its clips, fall in sections that list it, and
    don't clash with each other or with a section's cue (REQ-TRK-07)."""
    names = {clip.name for clip in voice.clips}
    bars = [bar for bar, _ in voice.cues]
    where = f"{score.id}: {voice.name}"
    if len(set(bars)) != len(bars):
        raise ScoreError(f"{where} cues two clips in one bar")
    for bar, clip in voice.cues:
        if clip not in names:
            raise ScoreError(f"{where} cues {clip!r}, which isn't one of its clips")
        if not 1 <= bar <= score.bars:
            raise ScoreError(f"{where} cues {clip!r} at bar {bar}, outside its {score.bars} bars")
        section = _section_at(score, bar)
        listed = section.cue(voice.name)
        if listed is None:
            raise ScoreError(
                f"{where} cues {clip!r} at bar {bar}, but section {section.name!r} leaves "
                f"{voice.name} out"
            )
        if listed and section.first == bar:
            raise ScoreError(f"{where}: section {section.name!r} already cues {listed!r} there")


def parse(raw: dict[str, Any]) -> Score:
    if "excerpt" in raw:
        return _excerpt_file(raw)
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
            feel=_feel(raw["feel"], str(raw["id"])) if "feel" in raw else EVEN,
        )
        by_name = {v.name: v for v in voices}
        declared = tuple(
            _variant(str(name), table, by_name) for name, table in raw.get("variants", {}).items()
        )
        sidechain = _sidechain(raw["sidechain"], by_name) if "sidechain" in raw else None
        for variant in declared:
            _changed_sidechain(sidechain, variant)  # a mistake is reported on load
        sections = tuple(_section(table, n) for n, table in enumerate(raw.get("sections", []), 1))
        score = replace(
            score,
            variants=declared,
            sidechain=sidechain,
            sections=sections,
            rate=_rate(raw.get("rate", 0), score.id),
        )
        score = _mixed(score, raw)
    except (KeyError, TypeError) as exc:
        raise ScoreError(f"score is missing or mistypes {exc}") from exc
    if not STRAIGHT <= score.swing < 1:
        raise ScoreError(f"{score.id}: swing must be from 0.5 (straight) to under 1")
    for voice in voices:
        _check_chokes(score, voice)
    return _arranged(score)


def _rate(raw: object, score_id: str) -> int:
    """A track's own sample rate, or 0 for the production rate (REQ-TRK-06)."""
    if raw == 0:
        return 0
    if isinstance(raw, bool) or raw not in RATES:
        rates = ", ".join(str(r) for r in RATES)
        raise ScoreError(f"{score_id}: rate is one of {rates}, not {raw!r}")
    return int(str(raw))


def _mixed(score: Score, raw: dict[str, Any]) -> Score:
    """The score's sends and master strip, checked against its strips (REQ-MIX-01, 02)."""
    try:
        sends = mix.sends(raw["sends"]) if "sends" in raw else ()
        table = raw.get("master")
        master = None
        if table is not None:
            settings = {k: v for k, v in dict(table).items() if k != "automate"}
            master = mix.strip(settings, table.get("automate"), "the master") or mix.Strip()
        strips = {v.name: v.strip for v in score.voices if v.strip is not None}
        mix.check(strips, sends, master, score.id)
    except MixError as exc:
        raise ScoreError(str(exc)) from exc
    for send in (s for s in sends if s.kind == mix.DELAY):
        if send.setting("beats") * score.beat_s > mix.MAX_DELAY_S:
            raise ScoreError(
                f"{score.id}: send {send.name}'s echo comes after {send.setting('beats'):g} "
                f"beats, longer than the {mix.MAX_DELAY_S:g} seconds a delay holds"
            )
    return replace(score, sends=sends, master=master)


def unmixed(score: Score) -> Score:
    """The score with its voices straight to their stems: no strips, sends or
    master. Templates, one-shots and probes measure the voices this way."""
    if not score.mixed:
        return score
    voices = tuple(replace(v, strip=None) for v in score.voices)
    return replace(score, voices=voices, sends=(), master=None)


def _check_chokes(score: Score, voice: Voice) -> None:
    names = {v.name: v for v in score.voices}
    if unknown := set(voice.chokes) - set(names):
        raise ScoreError(f"{score.id}: {voice.name} chokes unknown voices {sorted(unknown)}")
    if clipped := [name for name in voice.chokes if names[name].clips]:
        raise ScoreError(
            f"{score.id}: {voice.name} chokes {clipped[0]}, but a clip plays as it is (REQ-TRK-02)"
        )


# --- excerpts (REQ-TRK-03) ---------------------------------------------------------------


def _excerpt_file(raw: dict[str, Any]) -> Score:
    """An excerpt's score file: its own id, title, "Hear:" and project, and the
    bars of the track it plays."""
    if unknown := set(raw) - EXCERPT_KEYS:
        shown = ", ".join(sorted(unknown))
        raise ScoreError(f"an excerpt takes its music from its track, so it can't set {shown}")
    found = raw["excerpt"]
    if not isinstance(found, dict) or "of" not in found or "id" not in raw:
        raise ScoreError('an excerpt needs an id, and excerpt = { of = "<id>", bars = [a, b] }')
    first, last = _bar_pair(found.get("bars"), f"{raw['id']}: the excerpt")
    return excerpt(
        load(str(found["of"])),
        first,
        last,
        id=str(raw["id"]),
        title=str(raw.get("title", "")),
        hear=str(raw.get("hear", "")),
        project=str(raw.get("project", "")),
    )


def excerpt(
    track: Score,
    first: int,
    last: int,
    *,
    id: str,
    title: str = "",
    hear: str = "",
    project: str = "",
) -> Score:
    """Bars ``first`` to ``last`` of a track as a score of their own. The track's
    patterns run on through the bars before, so each plays what the track
    plays there, and a clip already playing at ``first`` is cued again from
    the bar it has reached. Cues and automation inside the bars come along,
    counted from the excerpt's first bar (REQ-TRK-07, REQ-MIX-03)."""
    if track.excerpt is not None:
        raise ScoreError(f"{id}: an excerpt is of a whole track, and {track.id} is an excerpt")
    if not 1 <= first <= last <= track.bars:
        raise ScoreError(f"{id}: bars {first} to {last} aren't all in {track.id}'s {track.bars}")
    lead = first - 1
    sections = tuple(
        replace(s, first=max(s.first, first) - lead, last=min(s.last, last) - lead)
        for s in track.sections
        if s.last >= first and s.first <= last
    )
    voices = {v.name: _within(v, first, last) for v in track.voices}
    for play in (p for p in plays(track) if p.bar < first < p.bar + p.bars):
        voice = voices[play.voice]
        clip = voice.clips[play.clip]
        reached = clip.first + first - play.bar
        carried = Clip(f"{clip.name} from bar {reached}", reached, clip.last)
        voices[play.voice] = replace(voice, clips=(*voice.clips, carried))
        sections = (_recue(sections[0], play.voice, carried.name), *sections[1:])
    return replace(
        track,
        id=id,
        title=title,
        hear=hear,
        project=project or track.project,
        bars=last - first + 1,
        voices=tuple(_cued_only(voices[v.name], sections) for v in track.voices),
        sections=sections,
        lead_bars=lead,
        excerpt=Excerpt(track.id, first, last),
        sends=tuple(_send_within(s, first, last) for s in track.sends),
        master=mix.cut(track.master, first, last),
    )


def _send_within(send: Send, first: int, last: int) -> Send:
    return replace(send, strip=mix.cut(send.strip, first, last) or send.strip)


def _within(voice: Voice, first: int, last: int) -> Voice:
    """A voice's cues and automation in bars ``first`` to ``last``, counted from
    the first of them."""
    lead = first - 1
    cues = tuple((bar - lead, clip) for bar, clip in voice.cues if first <= bar <= last)
    return replace(voice, cues=cues, strip=mix.cut(voice.strip, first, last))


def _recue(section: Section, voice: str, clip: str) -> Section:
    """The section with ``voice`` cueing ``clip``."""
    play = tuple(f"{voice}{CUE}{clip}" if e.partition(CUE)[0] == voice else e for e in section.play)
    return replace(section, play=play)


def _cued_only(voice: Voice, sections: tuple[Section, ...]) -> Voice:
    """A clip voice with only the clips its sections cue, in the order they are
    first cued, so clips the excerpt doesn't play are neither cut nor hashed.
    One that cues none keeps its first clip: it stays a clip voice, and rests."""
    if not voice.clips:
        return voice
    by_name = {clip.name: clip for clip in voice.clips}
    marks = [(s.first, name) for s in sections if (name := s.cue(voice.name))]
    cued = dict.fromkeys(name for _, name in sorted([*marks, *voice.cues]))
    return replace(voice, clips=tuple(by_name[name] for name in cued) or voice.clips[:1])


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


def source(benchmark_id: str) -> str:
    """The score file's text, as written."""
    if benchmark_id not in available():
        raise ScoreError(f"no score for {benchmark_id} (have: {', '.join(available())})")
    path = resources.files("mph.bench").joinpath("scores", f"{benchmark_id}.toml")
    return path.read_text("utf-8")


def load(benchmark_id: str) -> Score:
    return load_text(source(benchmark_id))


# --- events ---------------------------------------------------------------------------


def swung_time(step: int, step_s: float, swing: float) -> float:
    """Every odd sixteenth lands ``swing`` of the way through its 8th note."""
    pair, offbeat = divmod(step, 2)
    return pair * 2 * step_s + (2 * step_s * swing if offbeat else 0.0)


def feel_shares(feel: tuple[float, ...]) -> list[float]:
    """Where each pulse of a beat starts, as a share of the beat: [0, 0.41, 0.72]."""
    total = sum(feel)
    return [sum(feel[:pulse]) / total for pulse in range(PULSES)]


def triplet_time(step: int, beat_s: float, feel: tuple[float, ...]) -> float:
    """A triplet step's time: its beat, plus its pulse's place in the feel (REQ-BEN-13)."""
    beat, pulse = divmod(step, PULSES)
    return (beat + feel_shares(feel)[pulse]) * beat_s


def step_time(score: Score, voice: Voice, step: int) -> float:
    if voice.triplets:
        return triplet_time(step, score.beat_s, score.feel)
    return swung_time(step, score.step_s, score.swing)


def _hits(voice: Voice, bars: int) -> Iterator[tuple[int, float, int]]:
    """(global step, velocity from the symbol, slice) before velocity rules."""
    total = bars * voice.steps
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
            symbol = symbols[step % len(symbols)]  # a pattern of one bar or several (REQ-BEN-17)
            if symbol != ".":
                yield step, VELOCITY[symbol], 0


def _velocity(voice: Voice, n: int, step: int, base: float, rng: random.Random) -> float:
    velocity = voice.velocities[n % len(voice.velocities)] if voice.velocities else base
    ramp = voice.ramp
    if ramp:
        bar, position = divmod(step, voice.steps)
        first = voice.steps - ramp.steps
        if (bar + 1) % ramp.every_bars == 0 and position >= first:
            fraction = (position - first) / max(ramp.steps - 1, 1)
            velocity = ramp.start + (ramp.end - ramp.start) * fraction
    if voice.humanise:
        velocity *= 1 + rng.uniform(-voice.humanise, voice.humanise)
    return round(min(max(velocity, 0.0), 1.0), 4)


def _melodic(score: Score, voice: Voice) -> list[Event]:
    """303 rules: a slid note holds for the whole step and, if the next step
    plays, ties into it so that note glides instead of retriggering. A note
    followed by "-" steps is held through them (a pad, BM-SC-01). Steps count
    from the start of an excerpt's lead (REQ-TRK-03)."""
    gate = cells(voice.grid, hold=True)
    length = len(gate)  # one bar or several (REQ-BEN-17)
    accents = voice.accents.split() or ["."] * length
    slides = voice.slides.split() or ["."] * length
    total = (score.lead_bars + score.bars) * STEPS
    found = []
    for step in range(total):
        at = step % length
        if gate[at] in (".", HOLD):
            continue
        held = 0  # steps the note is held for after its own, by "-"
        while step + held + 1 < total and gate[(step + held + 1) % length] == HOLD:
            held += 1
        slide = slides[at] == "S" and not held
        following = step + 1 < total and gate[(at + 1) % length] not in (".", HOLD)
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


def first_note(score: Score, voice: Voice) -> Event:
    """The first note of a melodic voice's pattern, as the pattern plays it from
    bar 1: its level is measured on it (REQ-BEN-09), so a track and its
    excerpts match their voices' levels on the same note."""
    pattern = replace(score, bars=voice.length // STEPS, lead_bars=0, sections=())
    return _melodic(pattern, voice)[0]


def _beats(score: Score, index: int, voice: Voice) -> list[Event]:
    """A drum voice's hits from the start of an excerpt's lead. Every hit takes
    its velocity, so velocity rules and humanising keep their place."""
    rng = random.Random(score.seed * 1000 + index)  # noqa: S311 - musical, not crypto
    found = []
    for n, (step, base, number) in enumerate(_hits(voice, score.lead_bars + score.bars)):
        velocity = _velocity(voice, n, step, base, rng)
        found.append(Event(voice.name, step, step_time(score, voice, step), velocity, number))
    return found


def _in_place(score: Score, voice: Voice, found: list[Event]) -> list[Event]:
    """A voice's events from the score's own bar 1 on (REQ-TRK-03), in the bars
    of the sections that list it (REQ-TRK-01)."""
    if not score.lead_bars and not score.sections:
        return found
    lead = score.lead_bars * voice.steps
    out = []
    for event in found:
        step = event.step - lead
        if step >= 0 and _listed(score, voice, step // voice.steps + 1):
            out.append(replace(event, step=step, time_s=step_time(score, voice, step)))
    return _untied(out)


def _untied(found: list[Event]) -> list[Event]:
    """A note that would slide into one a section leaves out ends instead."""
    steps = {e.step for e in found}
    return [replace(e, tie=False) if e.tie and e.step + 1 not in steps else e for e in found]


def _listed(score: Score, voice: Voice, bar: int) -> bool:
    if not score.sections:
        return True
    section = next(s for s in score.sections if s.first <= bar <= s.last)
    return section.cue(voice.name) is not None


def plays(score: Score) -> list[Play]:
    """Every cue of every clip voice, in order (REQ-TRK-02)."""
    return [play for voice in score.voices if voice.clips for play in _cues(score, voice)]


def _starts(score: Score, voice: Voice) -> dict[int, int]:
    """Bar -> the clip cued there: by a section, or by the voice itself (REQ-TRK-07)."""
    index = {clip.name: n for n, clip in enumerate(voice.clips)}
    found = {s.first: index[cue] for s in score.sections if (cue := s.cue(voice.name))}
    found.update((bar, index[clip]) for bar, clip in voice.cues)
    return found


def _cues(score: Score, voice: Voice) -> list[Play]:
    """A clip plays from its cue until its last bar, another cue, a section
    that leaves the voice out, or the end of the score."""
    starts = _starts(score, voice)
    found: list[Play] = []
    playing: Play | None = None
    for bar in sorted({s.first for s in score.sections} | set(starts)):
        section = _section_at(score, bar)
        listed = section.cue(voice.name)
        if playing and (bar in starts or listed is None or playing.bar + playing.bars <= bar):
            found.append(replace(playing, bars=min(playing.bars, bar - playing.bar)))
            playing = None
        if bar in starts:
            playing = Play(voice.name, starts[bar], bar, voice.clips[starts[bar]].bars)
        elif listed == "" and playing is None and not _cued_in(voice, section):
            example = f"{voice.name}{CUE}{voice.clips[0].name}"
            raise ScoreError(
                f"{score.id}: section {section.name!r} lets {voice.name} carry on, but none "
                f"of its clips is playing there; cue one, like {example}"
            )
    if playing:
        found.append(replace(playing, bars=min(playing.bars, score.bars + 1 - playing.bar)))
    return found


def _cued_in(voice: Voice, section: Section) -> bool:
    """True when one of the voice's own cues falls inside the section."""
    return any(section.first <= bar <= section.last for bar, _ in voice.cues)


def _clip_events(score: Score) -> list[Event]:
    return [
        Event(
            p.voice,
            (p.bar - 1) * STEPS,
            (p.bar - 1) * score.bar_s,
            1.0,
            length_s=p.bars * score.bar_s,
            clip=p.clip + 1,
        )
        for p in plays(score)
    ]


def events(score: Score) -> list[Event]:
    found: list[Event] = []
    for index, voice in enumerate(score.voices):
        if voice.clips:
            continue
        own = _melodic(score, voice) if voice.melodic else _beats(score, index, voice)
        found += _in_place(score, voice, own)
    found += _clip_events(score)
    found.sort(key=lambda e: (e.time_s, e.voice))
    return found


def bar_one(score: Score, voice: Voice) -> str:
    """Bar 1 of a voice in grid notation, for checking against the spec."""
    hits = {e.step: e for e in events(score) if e.voice == voice.name and e.step < voice.steps}
    symbols = []
    for step in range(voice.steps):
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
    """Equal takes, none of them "main" (REQ-BEN-07). No take repeats another:
    a score whose every voice is a synth already has no synth-kit take, and
    nor does one with a break or clips, which no synth can play (REQ-TRK-02)."""
    supercollider = Variant("supercollider", "in SuperCollider", engine="supercollider")
    if score.supercollider_only:  # it always renders there (REQ-BEN-10, REQ-BEN-12)
        return [supercollider]
    found = [Variant("renoise", "in Renoise", engine="renoise"), supercollider]
    sampled = any(not v.sound.synth for v in score.voices)
    if sampled and not any(v.slices or v.clips for v in score.voices):
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
    grids = dict(found.grids)
    voices = []
    for voice in score.voices:
        sound = sounds.get(voice.name, voice.sound)
        if found.every_voice_synth and not sound.synth:  # a pinned sound gives way too
            sound = replace(sound, synth="auto", hash="", why="")
        changed = _params({**dict(voice.params), **dict(params.get(voice.name, ()))})
        voices.append(
            replace(
                voice,
                sound=sound,
                grid=grids.get(voice.name, voice.grid),
                gain_db=gains.get(voice.name, voice.gain_db),
                params=changed,
            )
        )
    sidechain = _changed_sidechain(score.sidechain, found)
    feel = found.feel or score.feel
    changed_score = replace(
        score, voices=tuple(voices), variant=found.name, sidechain=sidechain, feel=feel
    )
    return changed_score, found
