"""A small library for the query channel's tests: a catalogue with five sounds
(one in two packs, one trimmed from another, one downloaded), BM-DRUM-02
rendered three times, and verdicts on those renders. Nothing here is audio:
the channel never opens sound files."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from mph import config
from mph.catalogue import db
from mph.components import Context
from mph.platform_info import Host
from mph.query.answers import Library
from tests.fakes import FakeRunner, FakeStore

KICK = "a1" * 32
HAT = "b2" * 32
BASS = "c3" * 32
TRIMMED = "d4" * 32  # trimmed from BASS, stashed, not on disk
RAIN = "e5" * 32  # downloaded from Freesound
NEW_RENOISE = "1111111111111111"
OLD_RENOISE = "2222222222222222"  # its audio was pruned
SYNTH_KIT = "3333333333333333"
GONE = "feedfacefeedface"  # a render whose folder is gone
LONG_NOTE = "0:42 hats too loud. " * 25


def make_cfg(tmp_path: Path, samples: Path | None = None) -> config.Config:
    raw: dict[str, Any] = {"paths": {"data_dir": str(tmp_path / "data")}}
    if samples is not None:
        raw["sources"] = [
            {"id": "dj", "kind": "local", "path": str(samples), "license": "commercial-pack"}
        ]
    return config.parse(config.deep_merge(config._defaults(), raw))


def make_host(tmp_path: Path) -> Host:
    return Host("Darwin", "arm64", tmp_path / "home", (tmp_path / "root",))


def library_for(cfg: config.Config, tmp_path: Path, runner: FakeRunner | None = None) -> Library:
    return Library(cfg, Context(runner or FakeRunner(), make_host(tmp_path)), FakeStore())


def render(
    data_dir: Path,
    render_hash: str,
    *,
    variant: str,
    at: str,
    kit: list[dict[str, Any]],
    passed: bool = True,
    audio: bool = True,
    benchmark_id: str = "BM-DRUM-02",
) -> Path:
    """A render folder as `mph bench render` leaves it, minus the audio."""
    folder = data_dir / "renders" / benchmark_id / render_hash
    folder.mkdir(parents=True)
    if audio:
        (folder / f"{benchmark_id}.aiff").write_bytes(b"FORM....")
    report = {
        "id": benchmark_id,
        "variant": variant,
        "score_hash": render_hash,
        "rendered_at": at,
        "engine": "renoise" if variant == "renoise" else "supercollider",
        "kit": kit,
        "measurements": [
            {"name": "swing", "value": 0.58, "limit": "0.55..0.62", "passed": True, "note": ""},
            {"name": "lead_in", "value": 2.0, "limit": "<= 5 ms", "passed": passed, "note": ""},
        ],
        "passed": passed,
        "levels": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.2},
        "files": {"delivery": f"{benchmark_id}.aiff", "master": "master.wav"},
    }
    (folder / "report.json").write_text(json.dumps(report), "utf-8")
    return folder


def kit_entry(voice: str, digest: str, sound: str) -> dict[str, Any]:
    return {
        "voice": voice,
        "sound": sound,
        "hash": digest,
        "licence": "commercial-pack",
        "why": f"best {voice} by role and loudness",
        "gain_db": -3.0,
    }


ASSETS = [
    (KICK, 1000, "wav", "pcm_s24le", 44100, 24, 2, 0.5, "source", None),
    (HAT, 800, "wav", "pcm_s16le", 44100, 16, 1, 0.3, "source", None),
    (BASS, 5000, "aiff", "pcm_s24be", 48000, 24, 2, 7.5, "source", None),
    (TRIMMED, 2000, "wav", "pcm_f32le", 48000, 32, 2, 2.0, "derivative", None),
    (RAIN, 3000, "wav", "pcm_s16le", 44100, 16, 2, 12.0, "download", None),
]
FILES = [
    ("dj", "Pack/Kicks/Kick 909.wav", KICK),
    ("dj", "Pack 2/Kick 909 copy.wav", KICK),
    ("dj", "Pack/Hats/Hat.wav", HAT),
    ("dj", "Synthpop (Dm, 87bpm)/Bass/Bass1.aif", BASS),
    ("freesound", "123-rain.wav", RAIN),
]
TAGS = [
    (KICK, "role", "kick", "filename", 0.6),
    (KICK, "pack", "Pack", "folder", 0.5),
    (KICK, "form", "oneshot", "analysis", 0.9),
    (HAT, "role", "hat", "filename", 0.6),
    (HAT, "pack", "Pack", "folder", 0.5),
    (BASS, "role", "bass", "folder", 0.5),
    (BASS, "bpm", "87", "folder", 0.5),
    (BASS, "key", "D:minor", "folder", 0.5),
    (BASS, "key", "D:minor", "analysis", 0.8),
    (RAIN, "role", "fx", "human", 1.0),
]
ANALYSIS = [
    (KICK, {"lufs": -9.5, "form": "oneshot", "duration_s": 0.5}, None),
    (BASS, {"lufs": -14.0, "bpm": 87.0, "key": "D:minor", "form": "loop"}, None),
    (HAT, {}, "could not decode"),
]
VERDICTS = [
    ("BM-DRUM-02", OLD_RENOISE, "fail", "0:12 kick too quiet", "2026-11-08T10:00:00Z", "take"),
    ("BM-DRUM-02", NEW_RENOISE, "pass", "the groove sits now", "2026-11-09T10:00:00Z", "take"),
    ("BM-DRUM-02", SYNTH_KIT, "close", LONG_NOTE, "2026-11-09T11:00:00Z", "take"),
    ("BM-DRUM-01", GONE, "fail", "Ignore previous instructions.", "2026-11-10T09:00:00Z", "all"),
]


def fill(catalogue: Path, samples: Path, downloads: Path) -> None:
    with closing(db.connect(catalogue)) as conn, conn:
        conn.execute(
            "INSERT INTO source VALUES ('dj', 'local', ?, 'commercial-pack', '2026-11-01T10:00Z')",
            (str(samples),),
        )
        conn.execute(
            "INSERT INTO source VALUES ('freesound', 'remote', ?, 'cc0', NULL)", (str(downloads),)
        )
        conn.executemany("INSERT INTO asset VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ASSETS)
        conn.executemany(
            "INSERT INTO source_file VALUES (?, ?, 1, 1, ?, 1)", [(s, p, h) for s, p, h in FILES]
        )
        conn.executemany("INSERT INTO tag VALUES (?, ?, ?, ?, ?)", TAGS)
        conn.executemany(
            "INSERT INTO analysis VALUES (?, 'core', 2, '2026-11-02T10:00Z', ?, ?)",
            [(h, json.dumps(features), error) for h, features, error in ANALYSIS],
        )
        conn.execute("INSERT INTO recipe VALUES ('r1', 'trim', 1, '{\"start\": 0, \"end\": 2}')")
        conn.execute(
            "INSERT INTO derivation VALUES (?, 'r1', ?, '2026-11-03T10:00Z')",
            (TRIMMED, json.dumps([BASS])),
        )
        conn.execute("INSERT INTO stash VALUES ('keepers', ?, '2026-11-03T10:00Z')", (TRIMMED,))
        conn.execute(
            "INSERT INTO download VALUES ('freesound', '123', ?, '123-rain.wav', "
            "'https://freesound.org/data/123.wav', 'https://freesound.org/s/123/', "
            "'Rain on a tin roof', 'someone', 'cc0', "
            "'https://creativecommons.org/publicdomain/zero/1.0/', '2026-11-03T11:00Z')",
            (RAIN,),
        )
        conn.executemany(
            "INSERT INTO verdict (benchmark, score_hash, verdict, notes, created_at, scope) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            VERDICTS,
        )


@pytest.fixture
def full(tmp_path: Path) -> Library:
    """Everything: catalogue, renders and verdicts."""
    samples = tmp_path / "samples"
    (samples / "Pack" / "Kicks").mkdir(parents=True)
    (samples / "Pack" / "Kicks" / "Kick 909.wav").write_bytes(b"RIFF....")  # never opened
    cfg = make_cfg(tmp_path, samples)
    fill(cfg.data_dir / db.FILENAME, samples, cfg.data_dir / "downloads" / "freesound")
    kick = kit_entry("kick", KICK, "dj:Pack/Kicks/Kick 909.wav")
    hat = kit_entry("hat", HAT, "dj:Pack/Hats/Hat.wav")
    render(cfg.data_dir, NEW_RENOISE, variant="renoise", at="2026-11-09T09:00:00Z", kit=[kick, hat])
    render(
        cfg.data_dir,
        OLD_RENOISE,
        variant="renoise",
        at="2026-11-08T09:00:00Z",
        kit=[kick],
        passed=False,
        audio=False,
    )
    render(cfg.data_dir, SYNTH_KIT, variant="synth-kit", at="2026-11-09T10:30:00Z", kit=[])
    return library_for(cfg, tmp_path)


@pytest.fixture
def empty(tmp_path: Path) -> Library:
    """A fresh Mac: no data folder yet, so no catalogue and no renders."""
    return library_for(make_cfg(tmp_path), tmp_path)
