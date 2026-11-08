from __future__ import annotations

import json
import shutil
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
from mph.proc import Result, SubprocessRunner
from tests.fakes import FakeRunner

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
    assert (again.analysed, again.failed, again.cached) == (0, 1, 1)  # failures retried
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


PROBE_JSON = json.dumps(
    {
        "streams": [{"codec_name": "aac", "sample_rate": "8000", "channels": 2}],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
)


@pytest.mark.req("REQ-CAT-04")
def test_undecodable_by_libsndfile_falls_back_to_ffmpeg_pipe(tmp_path: Path) -> None:
    path = tmp_path / "demo.wav"  # really an .m4a with the wrong extension
    path.write_bytes(b"ftypM4A not riff")
    pcm = np.array([[0.5, -0.5], [0.25, -0.25], [0.0, 0.0]], dtype="<f4")
    runner = FakeRunner(
        on_path={"ffmpeg": "/usr/bin/ffmpeg"},
        responses={
            "ffprobe": Result(0, PROBE_JSON),
            "ffmpeg": Result(0, data=pcm.tobytes() + b"\x00"),  # stray byte is dropped
        },
    )
    samples, rate = analysis.decode(path, runner)
    assert rate == 8000
    assert samples.shape == (3, 2)
    assert samples[0].tolist() == [0.5, -0.5]
    ffmpeg_argv = next(argv for argv, _ in runner.calls if argv[0] == "ffmpeg")
    assert "-ar" not in ffmpeg_argv  # native rate: no resampling
    assert ffmpeg_argv[-1] == "-"  # decoded into a pipe, never a file


@pytest.mark.parametrize(
    ("on_path", "responses", "reason"),
    [
        ({}, {}, "Format not recognised"),
        ({"ffmpeg": "x"}, {"ffprobe": Result(1, stderr="Invalid data")}, "Invalid data"),
        (
            {"ffmpeg": "x"},
            {"ffprobe": Result(0, PROBE_JSON), "ffmpeg": Result(1, stderr="boom")},
            "boom",
        ),
    ],
)
def test_decode_failures_explain_themselves(
    tmp_path: Path, on_path: dict[str, str], responses: dict[str, Result], reason: str
) -> None:
    path = tmp_path / "x.m4a"
    path.write_bytes(b"nope")
    with pytest.raises(analysis.DecodeError, match=reason):
        analysis.decode(path, FakeRunner(on_path=on_path, responses=dict(responses)))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_m4a_decodes_through_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    soundfile.write(source, stereo(click_loop(120, 4)), SR)
    m4a = tmp_path / "tone.m4a"
    runner = SubprocessRunner(timeout_s=60)
    made = runner.run(["ffmpeg", "-v", "error", "-i", str(source), "-c:a", "aac", str(m4a)])
    if not made.ok:
        pytest.skip("this ffmpeg has no AAC encoder")
    samples, rate = analysis.decode(m4a, runner)
    assert rate == SR
    assert samples.shape[1] == 2
    assert abs(samples.shape[0] - SR * 2) < SR * 0.1
