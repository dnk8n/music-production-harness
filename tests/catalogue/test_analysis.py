from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph.catalogue import analysis, db
from mph.catalogue.analysis import (
    MINOR_PROFILE,
    DecodeError,
    Features,
    analyse_signal,
    decode,
    estimate_key,
    refine_loop_bpm,
    tags_from,
)

SR = 44100


def kick(seconds: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (np.sin(2 * np.pi * 55 * t) * np.exp(-t * 18)).astype(np.float32)


def click_loop(bpm: float, beats: int) -> np.ndarray:
    length = round(beats * 60 / bpm * SR)
    out = np.zeros(length, dtype=np.float32)
    hit = kick(0.12)
    for b in range(beats):
        start = round(b * 60 / bpm * SR)
        end = min(start + hit.size, length)
        out[start:end] += hit[: end - start]
    return out


def stereo(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1)


@pytest.mark.req("REQ-CAT-04")
def test_loop_tempo_from_length() -> None:
    features = analyse_signal(stereo(click_loop(124, 16)), SR)
    assert features.form == "loop"
    assert features.bpm == pytest.approx(124, abs=0.05)
    assert features.bpm_confidence >= 0.6


@pytest.mark.req("REQ-CAT-04")
def test_one_shot_kick() -> None:
    features = analyse_signal(stereo(kick()), SR)
    assert features.form == "oneshot"
    assert features.bpm is None
    assert features.lufs is None  # shorter than one 400 ms loudness block
    assert features.low_ratio > 0.8
    assert features.peak_dbfs <= 0


@pytest.mark.req("REQ-CAT-04")
def test_loudness_measured_for_long_sounds() -> None:
    t = np.arange(SR * 2) / SR
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    features = analyse_signal(stereo(tone), SR)
    assert features.lufs is not None
    # Two channels at -9 dBFS RMS sum to -6 dB, and K-weighting is ~0 dB at 1 kHz.
    assert features.lufs == pytest.approx(-6.0, abs=0.3)
    assert features.form == "oneshot"  # one onset, however long
    assert features.key is not None
    assert features.centroid_hz == pytest.approx(1000, rel=0.1)


def test_silence() -> None:
    features = analyse_signal(np.zeros((SR, 2), dtype=np.float32), SR)
    assert features.peak_dbfs == analysis.SILENCE_DBFS
    assert features.key is None


@pytest.mark.req("REQ-CAT-04")
def test_estimate_key_from_profile() -> None:
    a_minor = np.roll(MINOR_PROFILE, 9)  # tonic moved to A
    key, confidence = estimate_key(a_minor)
    assert key == "A:minor"
    assert confidence > 0
    assert estimate_key(np.zeros(12)) == (None, 0.0)


@pytest.mark.parametrize(
    ("estimate", "duration", "expected"),
    [
        (117.5, 7.742, 124.0),  # 16 beats at 124
        (62.0, 7.742, 124.0),  # half-time tracker error
        (250.0, 3.871, 124.0),  # double-time error, 8 beats
    ],
)
def test_refine_loop_bpm(estimate: float, duration: float, expected: float) -> None:
    bpm, _ = refine_loop_bpm(estimate, duration)
    assert bpm == pytest.approx(expected, abs=0.05)


def test_refine_without_candidates_keeps_estimate() -> None:
    assert refine_loop_bpm(120.0, 0.01) == (120.0, 0.3)


def test_tags_from_features() -> None:
    features = Features(
        8.0, -1.0, -12.0, -9.0, 16, 124.0, 0.9, "A:minor", 0.2, 900.0, 0.4, 0.8, "loop"
    )
    tags = {(n, v) for n, v, _ in tags_from(features)}
    assert tags == {
        ("form", "loop"),
        ("character", "percussive"),
        ("bpm", "124"),
        ("key", "A:minor"),
    }


def test_decode_native_rate_without_conversion(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    soundfile.write(path, stereo(kick()), 48000, subtype="PCM_24")
    samples, rate = decode(path)
    assert rate == 48000
    assert samples.shape[1] == 2


def test_decode_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "a.m4a"
    path.write_bytes(b"\x00\x00\x00 ftypM4A ")
    with pytest.raises(DecodeError):
        decode(path)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def seed(conn: sqlite3.Connection, root: Path, rel: str, digest: str) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO source VALUES ('dj', 'local', ?, 'x', NULL)", (str(root),)
        )
        conn.execute("INSERT INTO asset (hash, bytes, origin) VALUES (?, 1, 'source')", (digest,))
        conn.execute("INSERT INTO source_file VALUES ('dj', ?, 1, 1, ?, 1)", (rel, digest))


@pytest.mark.req("REQ-CAT-04")
def test_run_caches_by_version_and_tags(conn: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "samples"
    root.mkdir()
    soundfile.write(root / "loop.wav", stereo(click_loop(124, 16)), SR)
    (root / "bad.m4a").write_bytes(b"nope")
    seed(conn, root, "loop.wav", "h1")
    seed(conn, root, "bad.m4a", "h2")
    first = analysis.run(conn, ["h1", "h2"], workers=1, force=False, clock=lambda: "t")
    assert (first.analysed, first.failed, first.cached) == (1, 1, 0)
    assert len(first.errors) == 1
    assert first.errors[0].startswith(str(root / "bad.m4a"))
    again = analysis.run(conn, ["h1", "h2"], workers=1, force=False, clock=lambda: "t")
    assert (again.analysed, again.cached) == (0, 2)
    result = json.loads(
        conn.execute("SELECT result_json FROM analysis WHERE asset_hash = 'h1'").fetchone()[0]
    )
    assert result["bpm"] == pytest.approx(124, abs=0.05)
    tags = {
        (r[0], r[1])
        for r in conn.execute(
            "SELECT name, value FROM tag WHERE asset_hash = 'h1' AND origin = 'analysis'"
        )
    }
    assert ("bpm", "124") in tags
    error = conn.execute("SELECT error FROM analysis WHERE asset_hash = 'h2'").fetchone()[0]
    assert "cannot decode" in error


def test_locate_missing_asset(conn: sqlite3.Connection) -> None:
    assert analysis.locate(conn, "nope") is None
