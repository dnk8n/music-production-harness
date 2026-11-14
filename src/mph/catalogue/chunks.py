"""Loop and sampler chunks inside WAV and AIFF files (REQ-CAT-12).

ffprobe reads a file's tags but skips the chunks that loop software and
samplers use, so they are read here: a WAV's ``acid``, ``smpl`` and ``inst``
chunks, and an AIFF's Apple Loops ``basc`` chunk and its ``INST`` chunk, with
the ``MARK`` markers its loops point at. Only those chunks are read; the rest,
the audio included, is skipped with a seek. Files are opened read-only
(REQ-CAT-01) and recognised by their first bytes, not their names: Dean's
library has an M4A named .wav.

Layouts (WAV is little-endian, AIFF big-endian):

- ``acid``, 24 bytes: flags u32 (1 one-shot, 2 root note set, 4 stretch,
  8 disk-based), root note u16, two unknowns (u16, f32), beats u32, meter
  denominator u16, meter numerator u16, tempo f32.
- ``smpl``, 36 bytes and 24 per loop: manufacturer, product, sample period,
  MIDI unity note, pitch fraction, SMPTE format, SMPTE offset, loop count,
  sampler data size (u32 each); then each loop: cue id, type (0 forward,
  1 alternating, 2 backward), start, end (frames; the end is the loop's last
  frame), fraction, play count (0 plays forever) (u32 each).
- ``inst``, 7 bytes: base note, detune (cents), gain (dB) (s8 each), low and
  high note, low and high velocity (u8 each).
- ``basc``, 18 bytes or more: version u32, beats u32, root note u16, scale
  u16 (1 minor, 2 major, 3 neither, 4 both), meter numerator u16, meter
  denominator u16, loop type u16 (0 loop, 1 one-shot).
- ``INST``, 20 bytes: base note, detune (cents), low and high note, low and
  high velocity (s8 each), gain (dB, s16), then a sustain and a release loop:
  play mode s16 (0 none, 1 forward, 2 forward and back), begin and end marker
  ids s16. A marker sits just before the frame it names, so a loop ends just
  before its end marker.
- ``MARK``: marker count u16, then per marker: id s16, position u32 (frames),
  name (a count byte and that many bytes, padded to an even length).

Here a loop always runs from its first frame to the frame after its last,
whichever way the file counts.
"""

from __future__ import annotations

import math
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

WANTED = frozenset({b"acid", b"smpl", b"inst", b"basc", b"INST", b"MARK"})
FILE_HEAD = 12  # RIFF, size, WAVE or FORM, size, AIFF
CHUNK_HEAD = 8  # id, size
ID_BYTES = range(32, 127)  # a chunk id is four printable ASCII characters
MAX_CHUNK = 1 << 16  # far more than any of these needs; a bigger one is skipped
MAX_LOOPS = 16
UNKNOWN_SIZE = 0xFFFFFFFF  # RF64 and streamed files keep the real size elsewhere
ACID_ONE_SHOT, ACID_ROOT_SET = 0x01, 0x02
SMPL_LOOP_TYPES = {0: "forward", 1: "alternating", 2: "backward"}
AIFF_PLAY_MODES = {1: "forward", 2: "alternating"}
BASC_SCALES = {1: "minor", 2: "major", 3: "neither", 4: "both"}
BASC_ONE_SHOT = 1
MIDI_NOTES = range(128)


class ChunkError(ValueError):
    """A chunk too short or too odd to read."""


@dataclass(frozen=True, slots=True)
class Loop:
    start: int  # first frame
    end: int  # the frame after the last
    mode: str  # forward | alternating | backward | type N


@dataclass(frozen=True, slots=True)
class Acid:
    one_shot: bool
    root_note: int | None  # a MIDI note, only when the chunk says it is set
    beats: int
    meter: tuple[int, int]  # numerator, denominator
    tempo: float


@dataclass(frozen=True, slots=True)
class Sampler:
    """A WAV ``smpl`` chunk: the note the sound plays unshifted, and its loops."""

    unity_note: int
    cents: float  # how far above the unity note it plays, from the pitch fraction
    loops: tuple[Loop, ...]


@dataclass(frozen=True, slots=True)
class Instrument:
    """A WAV ``inst`` or AIFF ``INST`` chunk; only AIFF's has loops, through markers."""

    base_note: int
    detune: int  # cents
    notes: tuple[int, int]  # the lowest and highest note it should play
    velocities: tuple[int, int]
    gain_db: int
    sustain: Loop | None = None
    release: Loop | None = None


@dataclass(frozen=True, slots=True)
class AppleLoop:
    one_shot: bool
    beats: int
    root_note: int
    scale: str  # minor | major | neither | both | unknown
    meter: tuple[int, int]


@dataclass(frozen=True, slots=True)
class Chunks:
    acid: Acid | None = None
    smpl: Sampler | None = None
    inst: Instrument | None = None
    basc: AppleLoop | None = None
    skipped: tuple[str, ...] = ()  # chunks too short or too odd to read, and why


def read(path: Path) -> Chunks:
    """The loop and sampler chunks of a WAV or AIFF file; none for anything else."""
    with path.open("rb") as fh:  # read-only by construction (REQ-CAT-01)
        return parse(fh)


def parse(fh: BinaryIO) -> Chunks:
    head = fh.read(FILE_HEAD)
    kind = (head[:4], head[8:12]) if len(head) == FILE_HEAD else None
    if kind == (b"RIFF", b"WAVE"):
        return _wav(_walk(fh, "<"))
    if kind in ((b"FORM", b"AIFF"), (b"FORM", b"AIFC")):
        return _aiff(_walk(fh, ">"))
    return Chunks()


Found = dict[bytes, bytes | str]  # chunk id -> its body, or why it wasn't read


def _walk(fh: BinaryIO, order: str) -> Found:
    """The wanted chunks; the first of each id counts.

    Chunks are padded to an even length. Some writers forget, so when what
    follows a pad byte isn't a chunk id, the walk looks one byte earlier."""
    found: Found = {}
    padded = False
    while len(head := fh.read(CHUNK_HEAD)) == CHUNK_HEAD:
        chunk_id, size = head[:4], struct.unpack(f"{order}I", head[4:])[0]
        if not _is_id(chunk_id):
            if not padded:
                break
            fh.seek(-CHUNK_HEAD - 1, os.SEEK_CUR)
            padded = False
            continue
        if size == UNKNOWN_SIZE:
            break
        if chunk_id in WANTED and chunk_id not in found:
            found[chunk_id] = _body(fh, size)
        else:
            fh.seek(size, os.SEEK_CUR)
        padded = size % 2 == 1
        fh.seek(size % 2, os.SEEK_CUR)
    return found


def _is_id(chunk_id: bytes) -> bool:
    return all(byte in ID_BYTES for byte in chunk_id)


def _body(fh: BinaryIO, size: int) -> bytes | str:
    if size > MAX_CHUNK:
        fh.seek(size, os.SEEK_CUR)
        return f"{size} bytes, more than {MAX_CHUNK}"
    body = fh.read(size)
    return body if len(body) == size else f"cut short: {len(body)} of {size} bytes"


def _wav(found: Found) -> Chunks:
    skipped: list[str] = []
    return Chunks(
        acid=_each(found, b"acid", _acid, skipped),
        smpl=_each(found, b"smpl", _smpl, skipped),
        inst=_each(found, b"inst", _wav_inst, skipped),
        skipped=tuple(skipped),
    )


def _aiff(found: Found) -> Chunks:
    skipped: list[str] = []
    markers = _each(found, b"MARK", _markers, skipped) or {}
    return Chunks(
        basc=_each(found, b"basc", _basc, skipped),
        inst=_each(found, b"INST", lambda body: _aiff_inst(body, markers), skipped),
        skipped=tuple(skipped),
    )


def _each[T](
    found: Found, chunk_id: bytes, parse_body: Callable[[bytes], T], skipped: list[str]
) -> T | None:
    body = found.get(chunk_id)
    name = chunk_id.decode("ascii")
    if body is None:
        return None
    if isinstance(body, str):
        skipped.append(f"{name}: {body}")
        return None
    try:
        return parse_body(body)
    except (ChunkError, struct.error) as exc:
        skipped.append(f"{name}: {exc}")
        return None


def _need(body: bytes, size: int) -> None:
    if len(body) < size:
        raise ChunkError(f"{len(body)} bytes, wanted at least {size}")


def _note(value: int, what: str) -> int:
    if value not in MIDI_NOTES:
        raise ChunkError(f"{what} {value} isn't a MIDI note")
    return value


def _acid(body: bytes) -> Acid:
    _need(body, 24)
    flags, root, _, _, beats, denominator, numerator, tempo = struct.unpack("<IHHfIHHf", body[:24])
    return Acid(
        one_shot=bool(flags & ACID_ONE_SHOT),
        root_note=root if flags & ACID_ROOT_SET and root in MIDI_NOTES else None,
        beats=beats,
        meter=(numerator, denominator),
        tempo=round(tempo, 3) if math.isfinite(tempo) else 0.0,
    )


def _smpl(body: bytes) -> Sampler:
    _need(body, 36)
    unity, fraction, count = struct.unpack("<12xII8xI4x", body[:36])
    loops = []
    for index in range(min(count, MAX_LOOPS, (len(body) - 36) // 24)):
        _, kind, start, end, _, _ = struct.unpack_from("<6I", body, 36 + 24 * index)
        if end >= start:
            loops.append(Loop(start, end + 1, SMPL_LOOP_TYPES.get(kind, f"type {kind}")))
    return Sampler(_note(unity, "unity note"), round(fraction / 2**32 * 100, 2), tuple(loops))


def _wav_inst(body: bytes) -> Instrument:
    _need(body, 7)
    base, detune, gain, low, high, soft, hard = struct.unpack("<bbbBBBB", body[:7])
    return Instrument(_note(base, "base note"), detune, (low, high), (soft, hard), gain)


def _basc(body: bytes) -> AppleLoop:
    _need(body, 18)
    _, beats, root, scale, numerator, denominator, kind = struct.unpack(">IIHHHHH", body[:18])
    return AppleLoop(
        one_shot=kind == BASC_ONE_SHOT,
        beats=beats,
        root_note=_note(root, "root note"),
        scale=BASC_SCALES.get(scale, "unknown"),
        meter=(numerator, denominator),
    )


def _markers(body: bytes) -> dict[int, int]:
    """Marker id -> position in frames."""
    _need(body, 2)
    (count,) = struct.unpack(">H", body[:2])
    found: dict[int, int] = {}
    offset = 2
    for _ in range(count):
        if offset + 7 > len(body):
            raise ChunkError(f"cut short after {len(found)} of {count} markers")
        marker, position, length = struct.unpack_from(">hIB", body, offset)
        found[marker] = position
        offset += 7 + length + (length + 1) % 2  # the name, padded to an even length
    return found


def _aiff_inst(body: bytes, markers: dict[int, int]) -> Instrument:
    _need(body, 20)
    base, detune, low, high, soft, hard, gain = struct.unpack(">bbbbbbh", body[:8])
    return Instrument(
        base_note=_note(base, "base note"),
        detune=detune,
        notes=(low, high),
        velocities=(soft, hard),
        gain_db=gain,
        sustain=_aiff_loop(struct.unpack(">hhh", body[8:14]), markers),
        release=_aiff_loop(struct.unpack(">hhh", body[14:20]), markers),
    )


def _aiff_loop(loop: tuple[int, int, int], markers: dict[int, int]) -> Loop | None:
    mode, begin, end = loop
    if mode not in AIFF_PLAY_MODES or begin not in markers or end not in markers:
        return None
    start, stop = markers[begin], markers[end]
    return Loop(start, stop, AIFF_PLAY_MODES[mode]) if stop > start else None
