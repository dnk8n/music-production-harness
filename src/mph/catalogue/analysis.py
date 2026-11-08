"""Just-in-time audio analysis, cached per analyser version (REQ-CAT-04, ADR-0008).

Everything here is classic, deterministic DSP. Audio is decoded into memory
and resampled there for analysis only; nothing is written next to sources.
Results land in the ``analysis`` table plus ``origin='analysis'`` tags, which
sit beside the name-derived priors rather than replacing them (REQ-CAT-03).
"""

from __future__ import annotations

import json
import math
import sqlite3
import warnings
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from multiprocessing import get_context
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm
import soundfile
from numpy.typing import NDArray

from mph.catalogue.names import SHARP_NAMES

ANALYSER = "core"
VERSION = 1
ANALYSIS_SR = 22050
MIN_LUFS_SECONDS = 0.4  # BS.1770 gating needs at least one 400 ms block
LOOP_BEATS = (1, 2, 4, 8, 16, 32, 64, 128)
SILENCE_DBFS = -120.0
CLUB_TEMPO = 125.0  # tie-breaker between equally plausible tempos
TEMPO_RANGE = (60.0, 200.0)
TIGHT, LOOSE = 0.02, 0.04  # relative tempo error for high / medium confidence
LOW_BAND_HZ = 150
ONESHOT_MAX_ONSETS = 2
MIN_ONSETS_FOR_TEMPO = 4
PHRASE_MIN_SECONDS = 60.0
MIN_KEY_SECONDS = 0.5
TONAL_MAX_PERCUSSIVE = 0.7  # above this the pitch content is too thin for a key
PERCUSSIVE_SPLIT = 0.5
BPM_TAG_MIN_CONFIDENCE = 0.6
KEY_TAG_MIN_MARGIN = 0.05

# Krumhansl-Kessler key profiles, index 0 = tonic.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

Signal = NDArray[np.float32]


class DecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Features:
    duration_s: float
    peak_dbfs: float
    rms_dbfs: float
    lufs: float | None
    onsets: int
    bpm: float | None
    bpm_confidence: float
    key: str | None
    key_confidence: float
    centroid_hz: float
    low_ratio: float  # share of energy below 150 Hz
    percussive_ratio: float  # share of energy in the percussive HPSS component
    form: str  # oneshot | loop | phrase


def decode(path: Path) -> tuple[Signal, int]:
    """Float32 samples shaped (frames, channels) and the native sample rate."""
    try:
        data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    except (soundfile.LibsndfileError, RuntimeError, TypeError) as exc:
        raise DecodeError(f"cannot decode {path.suffix or 'file'}: {exc}") from exc
    return data, int(rate)


def _db(value: float) -> float:
    return round(20 * math.log10(value), 2) if value > 0 else SILENCE_DBFS


def estimate_key(chroma_mean: NDArray[np.float64]) -> tuple[str | None, float]:
    """Best-correlating key and its margin over the runner-up (0 = ambiguous)."""
    if not np.any(chroma_mean > 0):
        return None, 0.0
    scores: list[tuple[float, str]] = []
    for tonic in range(12):
        rotated = np.roll(chroma_mean, -tonic)
        for quality, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            scores.append(
                (float(np.corrcoef(rotated, profile)[0, 1]), f"{SHARP_NAMES[tonic]}:{quality}")
            )
    scores.sort(reverse=True)
    return scores[0][1], round(max(scores[0][0] - scores[1][0], 0.0), 3)


def refine_loop_bpm(estimate: float, duration_s: float) -> tuple[float, float]:
    """Snap a tempo estimate to a whole number of beats in the loop's length.

    Sample-pack loops are cut to exact bar lengths, so the duration pins the
    tempo far more precisely than beat tracking does. Half/double-time tracker
    errors are forgiven. Returns (bpm, confidence).
    """
    low, high = TEMPO_RANGE
    candidates = [n * 60 / duration_s for n in LOOP_BEATS if low <= n * 60 / duration_s <= high]
    if not candidates:
        return round(estimate, 2), 0.3

    def error(bpm: float) -> float:
        return min(abs(bpm - estimate * f) / (estimate * f) for f in (1.0, 2.0, 0.5))

    best = min(candidates, key=lambda bpm: (error(bpm), abs(bpm - CLUB_TEMPO)))
    confidence = 0.9 if error(best) < TIGHT else 0.6 if error(best) < LOOSE else 0.3
    return round(best, 2), confidence


def analyse_signal(samples: Signal, rate: int) -> Features:
    duration = samples.shape[0] / rate
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
    lufs = None
    if duration >= MIN_LUFS_SECONDS and peak > 0:
        value = pyloudnorm.Meter(rate).integrated_loudness(samples.astype(np.float64))
        lufs = round(float(value), 2) if math.isfinite(value) else None
    if peak == 0:
        return Features(
            round(duration, 3),
            SILENCE_DBFS,
            SILENCE_DBFS,
            None,
            0,
            None,
            0.0,
            None,
            0.0,
            0.0,
            0.0,
            0.0,
            "oneshot",
        )
    mono = librosa.resample(np.mean(samples, axis=1), orig_sr=rate, target_sr=ANALYSIS_SR)
    with warnings.catch_warnings():
        # librosa warns about very short inputs; the features are still usable.
        warnings.simplefilter("ignore", UserWarning)
        return _spectral_features(mono, duration, peak, rms, lufs)


def _spectral_features(
    mono: Signal, duration: float, peak: float, rms: float, lufs: float | None
) -> Features:
    sr = ANALYSIS_SR
    onsets = librosa.onset.onset_detect(y=mono, sr=sr, units="time")
    harmonic, percussive = librosa.effects.hpss(mono)
    total = float(np.sum(harmonic**2) + np.sum(percussive**2)) or 1.0
    percussive_ratio = round(float(np.sum(percussive**2)) / total, 3)
    spectrum = np.abs(librosa.stft(mono)) ** 2
    freqs = librosa.fft_frequencies(sr=sr)
    low_ratio = round(float(spectrum[freqs < LOW_BAND_HZ].sum() / (spectrum.sum() or 1.0)), 3)
    centroid = float(np.median(librosa.feature.spectral_centroid(S=np.sqrt(spectrum), sr=sr)))
    if len(onsets) <= ONESHOT_MAX_ONSETS:
        form = "oneshot"
    else:
        form = "loop" if duration < PHRASE_MIN_SECONDS else "phrase"
    bpm, bpm_conf = None, 0.0
    if form == "loop" and len(onsets) >= MIN_ONSETS_FOR_TEMPO:
        estimate = float(np.atleast_1d(librosa.beat.beat_track(y=mono, sr=sr)[0])[0])
        if estimate > 0:
            bpm, bpm_conf = refine_loop_bpm(estimate, duration)
    key, key_conf = None, 0.0
    if percussive_ratio < TONAL_MAX_PERCUSSIVE and duration >= MIN_KEY_SECONDS:
        chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr)
        key, key_conf = estimate_key(np.mean(chroma, axis=1))
    return Features(
        round(duration, 3),
        _db(peak),
        _db(rms),
        lufs,
        len(onsets),
        bpm,
        bpm_conf,
        key,
        key_conf,
        round(centroid, 1),
        low_ratio,
        percussive_ratio,
        form,
    )


def tags_from(features: Features) -> list[tuple[str, str, float]]:
    """Searchable tags (name, value, confidence) derived from features."""
    tags = [
        ("form", features.form, 0.7),
        (
            "character",
            "percussive" if features.percussive_ratio >= PERCUSSIVE_SPLIT else "tonal",
            0.7,
        ),
    ]
    if features.bpm is not None and features.bpm_confidence >= BPM_TAG_MIN_CONFIDENCE:
        tags.append(("bpm", f"{features.bpm:g}", features.bpm_confidence))
    if features.key is not None and features.key_confidence >= KEY_TAG_MIN_MARGIN:
        tags.append(("key", features.key, min(0.4 + features.key_confidence * 2, 0.9)))
    return tags


def analyse_file(path: str) -> tuple[dict[str, object] | None, str | None]:
    """Process-pool entry point: (features, error)."""
    try:
        samples, rate = decode(Path(path))
        return asdict(analyse_signal(samples, rate)), None
    except DecodeError as exc:
        return None, str(exc)


@dataclass(slots=True)
class AnalysisRun:
    analysed: int = 0
    failed: int = 0
    cached: int = 0


def pending(conn: sqlite3.Connection, hashes: Iterable[str], *, force: bool) -> list[str]:
    if force:
        return list(hashes)
    query = "SELECT 1 FROM analysis WHERE asset_hash = ? AND analyser = ? AND version = ?"
    return [h for h in hashes if not conn.execute(query, (h, ANALYSER, VERSION)).fetchone()]


def locate(conn: sqlite3.Connection, digest: str) -> Path | None:
    row = conn.execute(
        "SELECT s.location, f.rel_path FROM source_file f JOIN source s ON s.id = f.source_id "
        "WHERE f.asset_hash = ? AND s.kind = 'local' ORDER BY f.rel_path LIMIT 1",
        (digest,),
    ).fetchone()
    return Path(row[0]).expanduser() / row[1] if row else None


def run(
    conn: sqlite3.Connection,
    hashes: list[str],
    *,
    workers: int,
    force: bool,
    clock: Callable[[], str],
) -> AnalysisRun:
    todo = pending(conn, hashes, force=force)
    report = AnalysisRun(cached=len(hashes) - len(todo))
    located = [(h, p) for h in todo if (p := locate(conn, h)) is not None]
    # spawn, not fork: librosa/numba start threads, and macOS defaults to spawn anyway.
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        results = pool.map(analyse_file, [str(p) for _, p in located], chunksize=4)
        for (digest, _), (features, error) in zip(located, results, strict=True):
            _store(conn, digest, features, error, clock())
            report.analysed += features is not None
            report.failed += error is not None
    return report


def _store(
    conn: sqlite3.Connection,
    digest: str,
    features: dict[str, object] | None,
    error: str | None,
    now: str,
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO analysis (asset_hash, analyser, version, analysed_at, "
            "result_json, error) VALUES (?, ?, ?, ?, ?, ?)",
            (digest, ANALYSER, VERSION, now, json.dumps(features or {}, sort_keys=True), error),
        )
        conn.execute("DELETE FROM tag WHERE asset_hash = ? AND origin = 'analysis'", (digest,))
        if features is None:
            return
        for name, value, confidence in tags_from(Features(**features)):  # type: ignore[arg-type]
            conn.execute(
                "INSERT OR IGNORE INTO tag (asset_hash, name, value, origin, confidence) "
                "VALUES (?, ?, ?, 'analysis', ?)",
                (digest, name, value, round(confidence, 3)),
            )
