"""Read audio format facts with ffprobe (never decodes or writes anything)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mph.proc import Runner

FFPROBE_ARGS = (
    "-v",
    "error",
    "-select_streams",
    "a:0",
    "-of",
    "json",
    "-show_entries",
    "stream=codec_name,sample_rate,channels,bits_per_raw_sample,bits_per_sample,duration"
    ":format=format_name,duration",
)


@dataclass(frozen=True, slots=True)
class Probe:
    container: str | None = None
    codec: str | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    duration_s: float | None = None
    error: str | None = None


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


def parse(output: str) -> Probe:
    data = json.loads(output or "{}")
    streams = data.get("streams") or []
    if not streams:
        return Probe(error="no audio stream")
    stream, fmt = streams[0], data.get("format", {})
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
    )


def probe(runner: Runner, path: Path) -> Probe:
    result = runner.run(["ffprobe", *FFPROBE_ARGS, str(path)])
    if not result.ok:
        return Probe(error=(result.stderr.strip() or f"ffprobe exit {result.code}")[:500])
    try:
        return parse(result.stdout)
    except json.JSONDecodeError as exc:
        return Probe(error=f"unreadable ffprobe output: {exc}")
