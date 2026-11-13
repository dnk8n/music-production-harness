"""Tempo grids for long recordings: tracks and long phrases (REQ-CAT-10).

A loop gets its tempo from its length (``analysis.refine_loop_bpm``). A track
has no such anchor, and a beat tracker's local guesses wander: librosa put
Love Train, made at exactly 120 BPM, at 117.4. So this fits one fixed grid to
the whole recording:

1. Candidate beat lengths, 60 to 200 BPM, from the autocorrelation of its
   onset strength.
2. For each, the beat length whose grid of beats, at its best phase, collects
   the most onset strength, searched coarse then fine. Over hundreds of beats
   a length that is off by 0.01 BPM drifts tens of milliseconds, so the fit
   is good to a few thousandths of a BPM.
3. The likeliest tempo octave: each candidate's score, weighted by how close
   it is to 125 BPM in octaves.
4. Where the beat falls, from rises in the low band's energy folded onto one
   beat. Kicks mark the beat in club music; the full band would often pick
   the hats on the off-beat. If the fold's peak stands out no more than it
   does at beat lengths that fit nothing, there is no steady beat to fit.
5. How far the beat strays: the same fold over each 32 beats. A grid made in
   a DAW stays within a few milliseconds; a played track drifts.

Plain numpy and librosa, so the same audio always gives the same grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import librosa
import numpy as np
from numpy.typing import NDArray

Signal = NDArray[np.float32]
Floats = NDArray[np.float64]

HOP = 256  # onset frames of 11.6 ms at 22.05 kHz; frames of 23 ms took Love Train for 80 BPM
TEMPO_RANGE = (60.0, 200.0)
CANDIDATES = 4
DISTINCT = 0.03  # candidate beat lengths at least 3% apart
SEARCHES = ((0.02, 81), (0.001, 101))  # (+/- share of the beat length, steps): coarse, fine
PHASES = 64
CLUB_TEMPO = 125.0
LOW_RATE = 441  # resampling to 441 Hz keeps the low band, below about 200 Hz
LOW_SMOOTH = 9  # 20 ms: a whole cycle of a 50 Hz bass, so its ripple isn't taken for onsets
LOW_LEAD_S = 0.0076  # the smoothed energy rises this long before a kick's attack
FOLD_BINS = 256
MISFITS = (0.871, 1.137, 1.291)  # beat lengths that fit no simple ratio of the beat
MIN_CONTRAST = 1.5  # how much more a beat stands out than a misfit: noise 0.9 to 1.2, Love Train 5
ON_TIME_S = 0.005  # a first beat this close before the start is on it: the fit is that good
WINDOW_BEATS = 32
STEADY_MS, LOOSE_MS = 10.0, 30.0
SNAP_BPM = 0.01  # a fit this close to a whole BPM is that BPM


@dataclass(frozen=True, slots=True)
class Grid:
    bpm: float
    confidence: float  # 0.9 on a fixed grid, 0.6 if it strays a little, 0.3 if it drifts
    beat_s: float  # where the first beat falls: 0 <= beat_s < one beat
    drift_ms: float  # how far each 32 beats stray from the grid (75th percentile)


def fit(mono: Signal, rate: int) -> Grid | None:
    """The fixed grid that best fits ``mono``, or None if no steady beat drives it."""
    frame_s = HOP / rate
    onsets = _onset_strength(mono, rate)
    scored = [_refine(onsets, frame_s, guess) for guess in _candidates(onsets, frame_s)]
    if not scored:
        return None
    length = max(scored, key=_likelihood)[1]
    rises, rises_s = _low_rises(mono, rate)
    folded = _fold(rises, rises_s, length)
    misfit = max(_clarity(_fold(rises, rises_s, length * share)) for share in MISFITS)
    if _clarity(folded) <= MIN_CONTRAST * misfit:
        return None
    peak = int(np.argmax(folded.mean(axis=0)))
    drift = _drift_ms(folded, peak, length)
    return Grid(
        _snap(60 / length),
        0.9 if drift <= STEADY_MS else 0.6 if drift <= LOOSE_MS else 0.3,
        _first_beat(peak * length / FOLD_BINS + LOW_LEAD_S, length),
        round(drift, 1),
    )


def _onset_strength(mono: Signal, rate: int) -> Floats:
    strength = np.asarray(
        librosa.onset.onset_strength(y=mono, sr=rate, hop_length=HOP), dtype=np.float64
    )
    flattened: Floats = np.maximum(strength - np.median(strength), 0.0)  # only peaks count
    return flattened


def _candidates(onsets: Floats, frame_s: float) -> list[float]:
    """The strongest beat lengths in the onsets' autocorrelation, at least 3% apart."""
    correlation = np.asarray(librosa.autocorrelate(onsets), dtype=np.float64)
    lags = np.arange(len(correlation)) * frame_s
    inside = np.flatnonzero((lags >= 60 / TEMPO_RANGE[1]) & (lags <= 60 / TEMPO_RANGE[0]))
    found: list[float] = []
    for index in inside[np.argsort(-correlation[inside], kind="stable")]:
        lag = float(lags[index])
        if all(abs(lag - other) > DISTINCT * other for other in found):
            found.append(lag)
        if len(found) == CANDIDATES:
            break
    return found


def _refine(onsets: Floats, frame_s: float, guess: float) -> tuple[float, float]:
    """(score, beat length) of the grid near ``guess`` that collects the most onsets."""
    best = (0.0, guess)
    for share, steps in SEARCHES:
        lengths = np.linspace(best[1] * (1 - share), best[1] * (1 + share), steps)
        best = max((_comb(onsets, frame_s, float(length)), float(length)) for length in lengths)
    return best


def _comb(onsets: Floats, frame_s: float, length: float) -> float:
    """Mean onset strength on a grid of beats ``length`` apart, at its best phase."""
    beats = np.arange(int(len(onsets) * frame_s / length))
    phases = np.arange(PHASES) * length / PHASES
    frames = (phases[:, None] + beats[None, :] * length) / frame_s
    values = np.interp(frames, np.arange(len(onsets)), onsets)
    return float(values.mean(axis=1).max())


def _likelihood(scored: tuple[float, float]) -> float:
    """A comb's score, weighted towards the tempo a club track most likely has."""
    score, length = scored
    octaves = math.log2(60 / length / CLUB_TEMPO)
    return score * math.exp(-0.5 * octaves**2)


def _low_rises(mono: Signal, rate: int) -> tuple[Floats, float]:
    """Rises in the energy below about 200 Hz, in dB, every 2.3 ms."""
    low = np.asarray(librosa.resample(mono, orig_sr=rate, target_sr=LOW_RATE), dtype=np.float64)
    energy = np.convolve(low**2, np.ones(LOW_SMOOTH) / LOW_SMOOTH, mode="same")
    level = 10 * np.log10(np.maximum(energy, 1e-12))
    return np.maximum(np.diff(level, prepend=level[:1]), 0.0), 1 / LOW_RATE


def _fold(rises: Floats, frame_s: float, length: float) -> Floats:
    """``rises`` cut into beats and stacked: a row per beat, FOLD_BINS columns."""
    beats = int(len(rises) * frame_s / length)
    times = np.arange(beats)[:, None] * length + np.arange(FOLD_BINS)[None, :] * length / FOLD_BINS
    folded: Floats = rises[np.minimum(np.round(times / frame_s).astype(int), len(rises) - 1)]
    return folded


def _clarity(folded: Floats) -> float:
    """How far the folded beat's peak stands above its mean."""
    profile = folded.mean(axis=0)
    mean = float(profile.mean())
    return float(profile.max()) / mean if mean > 0 else 0.0


def _drift_ms(folded: Floats, peak: int, length: float) -> float:
    """How far each 32 beats' own peak sits from the whole recording's."""
    strays = []
    for start in range(0, len(folded) - WINDOW_BEATS + 1, WINDOW_BEATS // 2):
        local = int(np.argmax(folded[start : start + WINDOW_BEATS].mean(axis=0)))
        bins = (local - peak + FOLD_BINS // 2) % FOLD_BINS - FOLD_BINS // 2
        strays.append(abs(bins) * length / FOLD_BINS * 1000)
    return float(np.percentile(strays, 75)) if strays else 0.0


def _first_beat(at: float, length: float) -> float:
    beat = at % length
    return 0.0 if beat > length - ON_TIME_S else round(beat, 3)


def _snap(bpm: float) -> float:
    whole = round(bpm)
    return float(whole) if abs(bpm - whole) <= SNAP_BPM else round(bpm, 2)
