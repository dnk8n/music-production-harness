"""How close a candidate sounds to its reference (spec/components/matching.md, REQ-MAT-03).

Five views of a sound, each a classic measurement (ADR-0008): log spectra
at three window lengths, 96 mel bands, the level over time, the pitch over
time (YIN) and the noise against the tone (spectral flatness). A candidate
is scaled to the reference's RMS level first, so the distance is about the
sound, not its level. The spectra and the level hear down to HEARD_DB under
the sound's own level, so a separated stem's faint residue and a synth's
digital silence aren't told apart: neither is heard in a mix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import librosa
import numpy as np

from mph.match.matchfile import VIEWS

WINDOWS = (256, 1024, 4096)  # samples: about 5, 21 and 85 ms
MELS = 96
HOP = 512  # the level, pitch and noise views look every 512 samples
TINY = 1e-6
HEARD_DB = 60.0  # quieter than this under the sound's own level counts as silence
HEARD = 10 ** (-HEARD_DB / 20)
VOICED_DB = 30.0  # a frame's pitch counts within this of the sound's loudest
NOISE_DB = 40.0  # a frame's flatness counts within this of the reference's loudest
MISS_CENTS = 1200.0  # the pitch distance when the two never sound together
LOWEST = 2 ** (-7 / 12)  # pitch is searched from a fifth under the lowest note...
HIGHEST = 2.0  # ...to an octave over the highest


@dataclass(frozen=True, slots=True)
class Sound:
    """The views of one sound, ready to compare with another's."""

    spectra: tuple[np.ndarray, ...]  # log magnitude, one per window length
    mel: np.ndarray  # log power in each mel band
    level: np.ndarray  # dB every HOP samples
    pitch: np.ndarray  # cents every HOP samples (NaN where not voiced)
    noise: np.ndarray  # log10 of the spectral flatness every HOP samples


def mono(data: np.ndarray) -> np.ndarray:
    return np.asarray(data.mean(axis=1) if data.ndim == 2 else data, dtype=np.float64)  # noqa: PLR2004


def rms_of(signal: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(signal)))) if signal.size else 0.0


def scaled_to(signal: np.ndarray, rms: float) -> np.ndarray:
    """``signal`` at the RMS level ``rms`` (silence stays silent)."""
    own = rms_of(signal)
    return signal * (rms / own) if own > 0 else signal


def look(signal: np.ndarray, rate: int, notes_hz: tuple[float, ...]) -> Sound:
    """The five views of a mono signal; ``notes_hz`` bounds the pitch search."""
    own = rms_of(signal)
    magnitudes = {n: np.abs(librosa.stft(signal, n_fft=n, hop_length=n // 4)) for n in WINDOWS}
    # A window of n samples sums a steady level to n / 2 times it: the floor
    # sits HEARD_DB under that, the same for any two sounds at one level.
    heard = {n: np.maximum(magnitudes[n], max(own * n / 2 * HEARD, TINY)) for n in WINDOWS}
    spectra = [np.log(heard[n]) for n in WINDOWS]
    middle = magnitudes[HOP * 2]  # 1,024 samples, hopping by 256
    mel = librosa.feature.melspectrogram(S=heard[HOP * 2] ** 2, sr=rate, n_mels=MELS)
    frames_rms = librosa.feature.rms(y=signal, frame_length=HOP * 4, hop_length=HOP)[0]
    level = 20 * np.log10(np.maximum(frames_rms, max(own * HEARD, TINY)))
    low, high = min(notes_hz) * LOWEST, min(max(notes_hz) * HIGHEST, rate / 4)
    frame = max(HOP * 4, int(2 ** np.ceil(np.log2(4 * rate / low))))  # room for the longest period
    f0 = librosa.yin(signal, fmin=low, fmax=high, sr=rate, frame_length=frame, hop_length=HOP)
    frames = min(len(f0), len(level))
    voiced = level[:frames] >= level.max() - VOICED_DB
    pitch = np.where(voiced, 1200 * np.log2(np.maximum(f0[:frames], TINY)), np.nan)
    flat = librosa.feature.spectral_flatness(S=middle[:, ::2])[0]  # every 512 samples
    return Sound(
        spectra=tuple(spectra),
        mel=np.log(mel + TINY),
        level=level,
        pitch=pitch,
        noise=np.log10(flat + TINY),
    )


def distances(reference: Sound, candidate: Sound) -> dict[str, float]:
    """How far apart two sounds are in each view (raw, before any scaling)."""
    spectrum = sum(
        _mean_gap(a, b) for a, b in zip(reference.spectra, candidate.spectra, strict=True)
    )
    return {
        "spectrum": spectrum / len(WINDOWS),
        "mel": _mean_gap(reference.mel, candidate.mel),
        "loudness": _mean_gap(reference.level, candidate.level),
        "pitch": _pitch_gap(reference.pitch, candidate.pitch),
        "noise": _noise_gap(reference, candidate),
    }


def total(
    raw: Mapping[str, float], scales: Mapping[str, float], weights: Mapping[str, float]
) -> float:
    """One number: each view's distance over its scale, times its weight, added up."""
    return float(sum(weights.get(v, 1.0) * raw[v] / scales[v] for v in VIEWS))


def scales_of(raws: list[dict[str, float]]) -> dict[str, float]:
    """Each view's median distance over a set of candidates (never 0)."""
    return {v: max(float(np.median([raw[v] for raw in raws])), TINY) for v in VIEWS}


def _mean_gap(a: np.ndarray, b: np.ndarray) -> float:
    frames = min(a.shape[-1], b.shape[-1])
    return float(np.mean(np.abs(a[..., :frames] - b[..., :frames])))


def _pitch_gap(a: np.ndarray, b: np.ndarray) -> float:
    frames = min(len(a), len(b))
    both = ~np.isnan(a[:frames]) & ~np.isnan(b[:frames])
    if not both.any():
        return MISS_CENTS
    return float(np.mean(np.minimum(np.abs(a[:frames][both] - b[:frames][both]), MISS_CENTS)))


def _noise_gap(reference: Sound, candidate: Sound) -> float:
    frames = min(len(reference.noise), len(candidate.noise), len(reference.level))
    heard = reference.level[:frames] >= reference.level.max() - NOISE_DB
    if not heard.any():
        return 0.0
    return float(np.mean(np.abs(reference.noise[:frames][heard] - candidate.noise[:frames][heard])))
