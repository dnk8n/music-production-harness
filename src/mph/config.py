"""Layered configuration. Spec: spec/components/config.md.

Precedence (lowest to highest): packaged defaults, the user config file, then
an explicit ``--config`` path. Config is validated once at load time and is
immutable afterwards, so every component sees the same answer.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

USER_CONFIG = Path("~/.config/mph/config.toml")
ENV_CONFIG = "MPH_CONFIG"
SOURCE_KINDS = frozenset({"local", "remote"})
DELIVERY_FORMATS = frozenset({"aiff", "wav"})
STANDARD_RATES = frozenset({44100, 48000, 88200, 96000, 176400, 192000})


class ConfigError(ValueError):
    """Raised with every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid configuration:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    kind: str
    location: str
    license: str

    @property
    def local_path(self) -> Path | None:
        return Path(self.location).expanduser() if self.kind == "local" else None


@dataclass(frozen=True, slots=True)
class AudioPolicy:
    production_sample_rate: int
    delivery_sample_rate: int
    delivery_bit_depth: int
    delivery_format: str


@dataclass(frozen=True, slots=True)
class MasterTargets:
    target_lufs_integrated: float
    max_true_peak_dbtp: float


@dataclass(frozen=True, slots=True)
class Config:
    data_dir: Path
    audio: AudioPolicy
    master: MasterTargets
    keychain_service: str
    llm_providers: tuple[str, ...]
    ollama_model: str
    sources: tuple[Source, ...]
    loaded_from: tuple[Path, ...]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge tables recursively; arrays (like ``sources``) are replaced whole."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _defaults() -> dict[str, Any]:
    text = resources.files("mph").joinpath("default_config.toml").read_text("utf-8")
    return tomllib.loads(text)


def candidate_files(explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [explicit]
    env = os.environ.get(ENV_CONFIG)
    return [Path(env)] if env else [USER_CONFIG.expanduser()]


def load(explicit: Path | None = None) -> Config:
    raw = _defaults()
    loaded: list[Path] = []
    for path in candidate_files(explicit):
        if path.is_file():
            raw = deep_merge(raw, tomllib.loads(path.read_text("utf-8")))
            loaded.append(path)
        elif explicit is not None:
            raise ConfigError([f"config file not found: {path}"])
    return parse(raw, tuple(loaded))


def parse(raw: dict[str, Any], loaded_from: tuple[Path, ...] = ()) -> Config:
    problems: list[str] = []
    sources = tuple(_parse_sources(raw.get("sources", []), problems))
    audio = _table(AudioPolicy, raw, "audio", problems)
    master = _table(MasterTargets, raw, "master", problems)
    if audio is None or master is None:
        raise ConfigError(problems)
    config = Config(
        data_dir=Path(raw["paths"]["data_dir"]).expanduser(),
        audio=audio,
        master=master,
        keychain_service=raw["secrets"]["keychain_service"],
        llm_providers=tuple(raw["llm"]["providers"]),
        ollama_model=raw["llm"]["ollama_model"],
        sources=sources,
        loaded_from=loaded_from,
    )
    problems.extend(_validate(config))
    if problems:
        raise ConfigError(problems)
    return config


def _table[T](cls: type[T], raw: dict[str, Any], name: str, problems: list[str]) -> T | None:
    """Build a dataclass from a TOML table, reporting unknown or missing keys."""
    try:
        return cls(**raw.get(name, {}))
    except TypeError as exc:
        problems.append(f"[{name}]: {exc}")
        return None


def _parse_sources(items: list[dict[str, Any]], problems: list[str]) -> list[Source]:
    sources: list[Source] = []
    for index, item in enumerate(items):
        missing = {"id", "kind"} - item.keys()
        if missing:
            problems.append(f"sources[{index}] missing {sorted(missing)}")
            continue
        location = item.get("path") or item.get("url") or ""
        sources.append(Source(item["id"], item["kind"], location, item.get("license", "unknown")))
    return sources


def _validate(config: Config) -> list[str]:
    problems = _validate_audio(config.audio)
    seen: set[str] = set()
    for source in config.sources:
        if source.id in seen:
            problems.append(f"duplicate source id: {source.id}")
        seen.add(source.id)
        if source.kind not in SOURCE_KINDS:
            problems.append(f"source {source.id}: kind must be one of {sorted(SOURCE_KINDS)}")
        if not source.location:
            problems.append(f"source {source.id}: needs a path (local) or url (remote)")
        problems.extend(_validate_isolation(config.data_dir, source))
    return problems


def _validate_audio(audio: AudioPolicy) -> list[str]:
    problems: list[str] = []
    for name in ("production_sample_rate", "delivery_sample_rate"):
        if getattr(audio, name) not in STANDARD_RATES:
            problems.append(f"audio.{name} must be one of {sorted(STANDARD_RATES)}")
    if audio.production_sample_rate < audio.delivery_sample_rate:
        problems.append("audio.production_sample_rate must be >= delivery_sample_rate")
    if audio.delivery_bit_depth not in (16, 24):
        problems.append("audio.delivery_bit_depth must be 16 or 24")
    if audio.delivery_format not in DELIVERY_FORMATS:
        problems.append(f"audio.delivery_format must be one of {sorted(DELIVERY_FORMATS)}")
    return problems


def _validate_isolation(data_dir: Path, source: Source) -> list[str]:
    """Sources are read-only: the writable data_dir may never overlap one (REQ-CAT-01)."""
    root = source.local_path
    if root is None:
        return []
    data, src = data_dir.resolve(), root.resolve()
    if data == src or data.is_relative_to(src) or src.is_relative_to(data):
        return [f"source {source.id}: data_dir {data} overlaps read-only source {src}"]
    return []
