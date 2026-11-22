"""What a reference sounds like, to help write its match file
(spec/components/matching.md, REQ-MAT-01): its level and pitch every quarter
of a second, and its strongest partials where it is loudest."""

from __future__ import annotations

import math
from dataclasses import dataclass

import librosa
import numpy as np

from mph.match import views

QUARTER_S = 0.25
LOWEST_HZ = 30.0  # the pitch is looked for from here...
HIGHEST_HZ = 2000.0  # ...to here
PITCH_HOP = 256
PARTIALS = 8
PARTIAL_FFT = 1 << 16  # zero-padded: the peaks' frequencies to well under a hertz
APART_HZ = 15.0  # two peaks closer than this are one partial
QUIET_DB = -120.0
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


@dataclass(frozen=True, slots=True)
class Quarter:
    """A quarter of a second of the reference."""

    at_s: float  # from the span's start
    level_db: float  # RMS, dBFS
    pitch_hz: float  # the median pitch where it is voiced; 0 when it isn't


@dataclass(frozen=True, slots=True)
class Partial:
    hz: float
    db: float  # against the strongest partial


def note_name(hz: float) -> str:
    """A frequency as the nearest note and how far off it is, like "E3 +12"."""
    if hz <= 0:
        return "-"
    midi = 69 + 12 * math.log2(hz / 440)
    near = round(midi)
    cents = round((midi - near) * 100)
    return f"{NAMES[near % 12]}{near // 12 - 1} {cents:+d}"


def quarters(signal: np.ndarray, rate: int) -> list[Quarter]:
    """Level and pitch every quarter second of a mono signal. A quarter's
    pitch counts only where it is within views.VOICED_DB of the loudest."""
    step = round(QUARTER_S * rate)
    levels = [_db(signal[k : k + step]) for k in range(0, len(signal) - step + 1, step)]
    if not levels:
        return []
    f0 = librosa.yin(
        signal, fmin=LOWEST_HZ, fmax=HIGHEST_HZ, sr=rate, frame_length=4096, hop_length=PITCH_HOP
    )
    loudest = max(levels)
    found = []
    for n, level in enumerate(levels):
        frames = f0[n * step // PITCH_HOP : (n + 1) * step // PITCH_HOP]
        voiced = level >= loudest - views.VOICED_DB and frames.size > 0
        found.append(Quarter(n * QUARTER_S, level, float(np.median(frames)) if voiced else 0.0))
    return found


def partials(signal: np.ndarray, rate: int, count: int = PARTIALS) -> list[Partial]:
    """The strongest peaks of the loudest quarter second's spectrum."""
    step = round(QUARTER_S * rate)
    starts = range(0, max(len(signal) - step, 0) + 1, step)
    first = max(starts, key=lambda k: _db(signal[k : k + step]))
    piece = signal[first : first + step]
    padded = np.zeros(max(PARTIAL_FFT, len(piece)))
    padded[: len(piece)] = piece * np.hanning(len(piece))
    power = np.abs(np.fft.rfft(padded))
    freqs = np.fft.rfftfreq(len(padded), 1 / rate)
    peaks = np.flatnonzero((power[1:-1] > power[:-2]) & (power[1:-1] >= power[2:])) + 1
    picked: list[int] = []
    for peak in sorted(peaks, key=lambda p: -power[p]):
        if all(abs(freqs[peak] - freqs[p]) >= APART_HZ for p in picked):
            picked.append(int(peak))
        if len(picked) == count:
            break
    if not picked:
        return []
    top = power[picked[0]]
    return [Partial(float(freqs[p]), _ratio_db(power[p] / top)) for p in sorted(picked)]


def _db(piece: np.ndarray) -> float:
    return _ratio_db(views.rms_of(piece))


def _ratio_db(ratio: float) -> float:
    return round(20 * math.log10(ratio), 1) if ratio > 0 else QUIET_DB
