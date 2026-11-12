"""Level-matching kit sounds by role (spec/components/benchmarks.md, REQ-BEN-09).

A sound's *hit loudness* is the K-weighted power (the ITU-R BS.1770 filter)
of its loudest 100 ms, in dB. Each voice gets the gain that brings its sound
to its role's target, plus the score's own ``gain_db`` for that voice.
"""

from __future__ import annotations

import math

import numpy as np
from pyloudnorm.iirfilter import IIRfilter

from mph.bench.score import Voice

WINDOW_S = 0.1
DEFAULT_TARGET_DB = -18.0
GAIN_RANGE_DB = (-30.0, 12.0)
BS1770_OFFSET_DB = -0.691

# role -> target hit loudness in dB. The table in benchmarks.md is the source.
TARGETS_DB: dict[str, float] = {
    "kick": -12.0,
    "snare": -14.0,
    "clap": -14.0,
    "break": -14.0,
    "tom": -16.0,
    "dunun": -16.0,
    "conga": -17.0,
    "djembe": -17.0,
    "perc": -18.0,
    "bell": -19.0,
    "rim": -20.0,
    "clave": -20.0,
    "hat_open": -21.0,
    "hat": -22.0,
    "shaker": -24.0,
}


def k_weight(data: np.ndarray, rate: int) -> np.ndarray:
    """The BS.1770 pre-filter: a high shelf, then a high pass (as pyloudnorm uses)."""
    shelf = IIRfilter(4.0, 1 / math.sqrt(2), 1500.0, rate, "high_shelf")
    high_pass = IIRfilter(0.0, 0.5, 38.0, rate, "high_pass")
    return np.asarray(high_pass.apply_filter(shelf.apply_filter(data)))


def hit_loudness(data: np.ndarray, rate: int) -> float:
    """dB of the loudest 100 ms after K-weighting; -inf for silence."""
    signal = data if data.ndim == 2 else data[:, None]  # noqa: PLR2004 - (frames, channels)
    weighted = np.stack([k_weight(signal[:, c], rate) for c in range(signal.shape[1])], axis=1)
    power = (weighted**2).sum(axis=1)
    window = max(1, min(len(power), round(WINDOW_S * rate)))
    if not power.size:
        return float("-inf")
    sums = np.convolve(power, np.ones(window), mode="valid") / window
    loudest = float(sums.max())
    return BS1770_OFFSET_DB + 10 * math.log10(loudest) if loudest > 0 else float("-inf")


def target_for(voice: Voice) -> float:
    role = voice.sound.role or voice.name
    return TARGETS_DB.get(role, DEFAULT_TARGET_DB) + voice.gain_db


def gain_db(voice: Voice, loudness_db: float) -> float:
    """The gain that brings a sound to its voice's target, clamped."""
    low, high = GAIN_RANGE_DB
    if not math.isfinite(loudness_db):
        return 0.0
    return round(min(max(target_for(voice) - loudness_db, low), high), 2)


def linear(db: float) -> float:
    return float(10 ** (db / 20))
