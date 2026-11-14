"""WAV and AIFF files built byte by byte, with the chunks REQ-CAT-12 reads.

The repo keeps no audio (a pre-commit hook refuses it), so tests build what
they need: silent PCM plus the loop and sampler chunks sample packs write.
"""

from __future__ import annotations

import math
import struct

RATE = 44100
ACID_ONE_SHOT, ACID_ROOT_SET = 0x01, 0x02


def riff_chunk(chunk_id: bytes, body: bytes, *, pad: bool = True) -> bytes:
    """A RIFF chunk, padded to an even length unless ``pad`` is False (a writer's bug)."""
    filler = b"\x00" if pad and len(body) % 2 else b""
    return chunk_id + struct.pack("<I", len(body)) + body + filler


def aiff_chunk(chunk_id: bytes, body: bytes) -> bytes:
    return chunk_id + struct.pack(">I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def acid(
    *,
    one_shot: bool = False,
    root: int | None = None,
    beats: int = 8,
    tempo: float = 120.0,
    meter: tuple[int, int] = (4, 4),
) -> bytes:
    flags = (ACID_ONE_SHOT if one_shot else 0) | (ACID_ROOT_SET if root is not None else 0)
    numerator, denominator = meter
    return struct.pack(
        "<IHHfIHHf", flags, root or 0, 0x8000, 0.0, beats, denominator, numerator, tempo
    )


def smpl(unity: int = 60, *, cents: float = 0.0, loops: tuple[tuple[int, int], ...] = ()) -> bytes:
    """``loops`` are (first frame, last frame), as a smpl chunk counts them."""
    fraction = round(cents / 100 * 2**32)
    head = struct.pack("<9I", 0, 0, 22675, unity, fraction, 0, 0, len(loops), 0)
    return head + b"".join(struct.pack("<6I", 0, 0, start, end, 0, 0) for start, end in loops)


def wav_inst(base: int = 60, *, detune: int = 0, notes: tuple[int, int] = (0, 127)) -> bytes:
    return struct.pack("<bbbBBBB", base, detune, 0, notes[0], notes[1], 1, 127)


def info(**fields: str) -> bytes:
    """A LIST INFO chunk's body: INAM="Kick", IART="Someone", ..."""
    return b"INFO" + b"".join(
        riff_chunk(key.encode("ascii"), value.encode("utf-8") + b"\x00")
        for key, value in fields.items()
    )


def wav(seconds: float = 4.0, *chunks: bytes, channels: int = 1) -> bytes:
    """A silent 16-bit WAV with ``chunks`` (built with ``riff_chunk``) before its audio."""
    frames = int(RATE * seconds)
    fmt = struct.pack("<HHIIHH", 1, channels, RATE, RATE * 2 * channels, 2 * channels, 16)
    body = (
        b"WAVE"
        + riff_chunk(b"fmt ", fmt)
        + b"".join(chunks)
        + riff_chunk(b"data", b"\x00\x00" * channels * frames)
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def basc(
    *,
    beats: int = 8,
    root: int = 48,
    scale: int = 1,
    meter: tuple[int, int] = (4, 4),
    one_shot: bool = False,
) -> bytes:
    """Scale 1 minor, 2 major, 3 neither, 4 both."""
    head = struct.pack(">IIHHHHH", 1, beats, root, scale, *meter, 1 if one_shot else 0)
    return head + b"\x00" * 66  # Apple pads the chunk to 84 bytes


def mark(*markers: tuple[int, int, str]) -> bytes:
    """(id, position, name) per marker."""
    body = struct.pack(">H", len(markers))
    for marker, position, name in markers:
        text = name.encode("ascii")
        body += struct.pack(">hIB", marker, position, len(text)) + text
        body += b"\x00" if (len(text) + 1) % 2 else b""
    return body


def aiff_inst(
    base: int = 60, *, detune: int = 0, sustain: tuple[int, int, int] = (0, 0, 0)
) -> bytes:
    """``sustain`` is (play mode, begin marker, end marker)."""
    return struct.pack(">bbbbbbh", base, detune, 0, 127, 1, 127, 0) + struct.pack(
        ">hhhhhh", *sustain, 0, 0, 0
    )


def _extended(value: float) -> bytes:
    """An 80-bit IEEE extended number, as AIFF stores its sample rate."""
    exponent = math.floor(math.log2(value))
    return struct.pack(">HQ", exponent + 16383, int(value / 2**exponent * 2**63))


def aiff(seconds: float = 4.0, *chunks: bytes, kind: bytes = b"AIFF") -> bytes:
    """A silent 16-bit mono AIFF with ``chunks`` (built with ``aiff_chunk``)."""
    frames = int(RATE * seconds)
    comm = struct.pack(">hIh", 1, frames, 16) + _extended(RATE)
    ssnd = struct.pack(">II", 0, 0) + b"\x00\x00" * frames
    body = kind + aiff_chunk(b"COMM", comm) + b"".join(chunks) + aiff_chunk(b"SSND", ssnd)
    return b"FORM" + struct.pack(">I", len(body)) + body
