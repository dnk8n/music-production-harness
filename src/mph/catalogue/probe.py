"""Read audio format facts and tags with ffprobe (never decodes or writes anything).

One call gives the first audio stream's format, the file's tags (REQ-CAT-12)
and whether it carries cover art: a picture stream marked as attached, whose
format and size are noted and whose bytes are never read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mph.proc import Runner

FFPROBE_ARGS = (
    "-v",
    "error",
    "-of",
    "json",
    "-show_entries",
    "stream=codec_type,codec_name,sample_rate,channels,bits_per_raw_sample,bits_per_sample,"
    "duration,width,height:stream_disposition=attached_pic:stream_tags"
    ":format=format_name,duration:format_tags",
)


@dataclass(frozen=True, slots=True)
class Picture:
    """Cover art: what it is, never the picture itself."""

    codec: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True, slots=True)
class Probe:
    container: str | None = None
    codec: str | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    duration_s: float | None = None
    error: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)  # as ffprobe names them
    picture: Picture | None = None


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None  # ffprobe reports 0 for "not applicable" (e.g. AAC bit depth)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tags(*found: Any) -> dict[str, str]:
    """Tags from the audio stream and the file; the file's win."""
    merged: dict[str, str] = {}
    for tags in found:
        if isinstance(tags, dict):
            merged.update({str(k): str(v) for k, v in tags.items()})
    return merged


def _picture(streams: list[dict[str, Any]]) -> Picture | None:
    for stream in streams:
        if (stream.get("disposition") or {}).get("attached_pic"):
            width, height = _int(stream.get("width")), _int(stream.get("height"))
            return Picture(stream.get("codec_name"), width, height)
    return None


def parse(output: str) -> Probe:
    data = json.loads(output or "{}")
    streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    audio = [s for s in streams if s.get("codec_type", "audio") == "audio"]
    if not audio:
        return Probe(error="no audio stream")
    stream, fmt = audio[0], data.get("format", {})
    # ffprobe lists aliases ("mov,mp4,m4a,3gp,3g2,mj2"); prefer the one people recognise.
    names = (fmt.get("format_name") or "").split(",")
    container = "m4a" if "m4a" in names else names[0] or None
    return Probe(
        container=container,
        codec=stream.get("codec_name"),
        sample_rate=_int(stream.get("sample_rate")),
        bit_depth=_int(stream.get("bits_per_raw_sample")) or _int(stream.get("bits_per_sample")),
        channels=_int(stream.get("channels")),
        duration_s=_float(stream.get("duration")) or _float(fmt.get("duration")),
        tags=_tags(stream.get("tags"), fmt.get("tags")),
        picture=_picture(streams),
    )


def probe(runner: Runner, path: Path) -> Probe:
    result = runner.run(["ffprobe", *FFPROBE_ARGS, str(path)])
    if not result.ok:
        return Probe(error=(result.stderr.strip() or f"ffprobe exit {result.code}")[:500])
    try:
        return parse(result.stdout)
    except json.JSONDecodeError as exc:
        return Probe(error=f"unreadable ffprobe output: {exc}")
