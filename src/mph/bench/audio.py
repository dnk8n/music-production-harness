"""Audio for benchmarks: stems, slicing and measurement (REQ-BEN-01, REQ-BEN-03).

Timing is checked by matching: each voice is also rendered alone once (its
*template*), and every scored hit is found in the voice's stem by
cross-correlating the template's first 20 ms within +/-5 ms of where the hit
should be. That gives the timing error to the sample and the hit's level
(the least-squares gain), which is its velocity. Stems are separate, so other
voices never confuse the match.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile
import soxr

from mph.bench.score import Event

TEMPLATE_S = 0.02
SEARCH_S = 0.005
SILENCE = 10 ** (-60 / 20)  # -60 dBFS
TRUE_PEAK_OVERSAMPLING = 4
SLICES_PER_BAR = 8
ONSET_SNAP_S = 0.03
WAVE_FLOAT = 3  # WAVE_FORMAT_IEEE_FLOAT
AUDIBLE_RANGE = (20.0, 20000.0)
HARMONIC_WIDTH = 0.02  # +/- of the fundamental counts as on a harmonic
FIT_RANGE = (0.75, 1.5)  # break playback-rate factors allowed before doubling or halving

Audio = np.ndarray


@dataclass(frozen=True, slots=True)
class Match:
    event: Event
    error_ms: float
    level: float  # measured velocity


def read(path: Path) -> tuple[Audio, int]:
    data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    return data, int(rate)


def write_float(path: Path, data: Audio, rate: int) -> None:
    """32-bit float WAV at the given rate (REQ-AUD-02).

    Written by hand because libsndfile adds a PEAK chunk holding the time of
    writing, which would make identical audio give different files (REQ-AUD-05).
    """
    frames = np.ascontiguousarray(data, dtype="<f4")
    channels = frames.shape[1] if frames.ndim > 1 else 1
    body = frames.tobytes()
    fmt = struct.pack("<HHIIHH", WAVE_FLOAT, channels, rate, rate * channels * 4, channels * 4, 32)
    fact = struct.pack("<I", len(frames))
    chunks = (
        b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"fact" + struct.pack("<I", len(fact)) + fact
        + b"data" + struct.pack("<I", len(body)) + body
    )  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)


def split(data: Audio, names: Sequence[str]) -> dict[str, Audio]:
    return {name: data[:, 2 * n : 2 * n + 2] for n, name in enumerate(names)}


def mixdown(stems: dict[str, Audio]) -> Audio:
    total = np.zeros_like(next(iter(stems.values())))
    for stem in stems.values():
        total = total + stem
    return total


def mono(data: Audio) -> Audio:
    return data.mean(axis=1) if data.ndim > 1 else data


# --- matching hits ---------------------------------------------------------------------


def _window(signal: Audio, start: int, length: int) -> Audio:
    out = np.zeros(length, dtype=np.float64)
    lo, hi = max(start, 0), min(start + length, len(signal))
    if hi > lo:
        out[lo - start : hi - start] = signal[lo:hi]
    return out


def match_hits(stem: Audio, template: Audio, events: Sequence[Event], rate: int) -> list[Match]:
    tpl = mono(template)[: round(TEMPLATE_S * rate)].astype(np.float64)
    energy = float(np.dot(tpl, tpl))
    signal = mono(stem)
    reach = round(SEARCH_S * rate)
    found = []
    for event in events:
        at = event.sample(rate)
        segment = _window(signal, at - reach, 2 * reach + len(tpl))
        scores = np.correlate(segment, tpl, mode="valid")
        lag = int(np.argmax(scores)) - reach
        piece = segment[lag + reach : lag + reach + len(tpl)]
        level = float(np.dot(piece, tpl) / energy) if energy else 0.0
        found.append(Match(event, 1000 * lag / rate, level))
    return found


def swing_of(matches: Sequence[Match], step_s: float) -> float | None:
    """Mean measured position of off-beat sixteenths within their 8th (0.5 = straight)."""
    ratios = []
    for match in matches:
        pair, offbeat = divmod(match.event.step, 2)
        if offbeat:
            onset = match.event.time_s + match.error_ms / 1000
            ratios.append((onset - pair * 2 * step_s) / (2 * step_s))
    return float(np.mean(ratios)) if ratios else None


def residue_after(stem: Audio, start_s: float, end_s: float, rate: int) -> float:
    """Peak level between two times, e.g. what's left of an open hat after its choke."""
    lo, hi = round(start_s * rate), round(end_s * rate)
    piece = np.abs(mono(stem)[lo:hi])
    return float(piece.max()) if piece.size else 0.0


def edge_jumps(stem: Audio, edges_s: Sequence[float], rate: int) -> float:
    """How much bigger the one-sample jumps at slice edges are than the jumps
    just around them. A click is a jump the music around it doesn't have, so
    a smooth edge scores about 1 or less, whatever the material."""
    signal = mono(stem)
    floor = 0.01 * (float(np.abs(signal).max()) or 1.0)
    edge, near, far = (max(round(s * rate), 1) for s in (0.0005, 0.001, 0.005))
    worst = 0.0
    for at in (round(t * rate) for t in edges_s):
        steps = np.abs(np.diff(signal[max(at - far, 0) : at + far]))
        middle = at - max(at - far, 0)
        jump = steps[max(middle - edge, 0) : middle + edge].max(initial=0.0)
        around = np.concatenate([steps[: max(middle - near, 0)], steps[middle + near :]])
        worst = max(worst, float(jump) / max(float(around.max(initial=0.0)), floor))
    return worst


# --- tone -----------------------------------------------------------------------------


def rms(signal: Audio) -> float:
    flat = mono(signal)
    return float(np.sqrt(np.mean(flat.astype(np.float64) ** 2))) if flat.size else 0.0


def centroid(signal: Audio, rate: int) -> float:
    """Spectral centroid in Hz: where the brightness sits."""
    flat = mono(signal).astype(np.float64)
    power = np.abs(np.fft.rfft(flat * np.hanning(len(flat)))) ** 2
    freqs = np.fft.rfftfreq(len(flat), 1 / rate)
    return float((power * freqs).sum() / power.sum()) if power.sum() else 0.0


def pitch_hz(signal: Audio, rate: int, low: float, high: float) -> float:
    """Fundamental of a steady note, by the YIN difference function: the period
    (searched from 1/high to 1/low) whose shifted copy differs least, refined
    between samples. Give a narrow range around the pitch you expect, so an
    octave mistake can't happen. 0 if the signal is too short or silent."""
    flat = mono(signal).astype(np.float64)
    flat = flat - flat.mean()
    shortest, longest = max(int(rate / high), 2), int(np.ceil(rate / low)) + 1
    width = len(flat) - longest - 1
    if width <= 0 or not np.any(flat):
        return 0.0
    lags = np.arange(shortest - 1, longest + 2)
    diff = np.array([np.sum((flat[:width] - flat[lag : lag + width]) ** 2) for lag in lags])
    best = int(np.argmin(diff[1:-1])) + 1
    a, b, c = diff[best - 1], diff[best], diff[best + 1]
    bend = a - 2 * b + c
    shift = 0.5 * (a - c) / bend if bend > 0 else 0.0
    return float(rate / (lags[best] + shift))


def blackman_harris(n: int) -> np.ndarray:
    """4-term Blackman-Harris window: side lobes under -92 dB, so a harmonic's
    own leakage doesn't read as energy between harmonics."""
    k = 2 * np.pi * np.arange(n) / (n - 1)
    return 0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k) - 0.01168 * np.cos(3 * k)


def inharmonic_db(signal: Audio, rate: int, fundamental: float, span: tuple[float, float]) -> float:
    """Energy away from the harmonics of a held note, relative to all of it, in
    dB, from 20 Hz to 20 kHz. Aliasing shows up here as tones between the harmonics."""
    flat = mono(signal)[round(span[0] * rate) : round(span[1] * rate)].astype(np.float64)
    flat = flat - flat.mean()  # saturation leaves a little DC, which isn't aliasing
    power = np.abs(np.fft.rfft(flat * blackman_harris(len(flat)))) ** 2
    freqs = np.fft.rfftfreq(len(flat), 1 / rate)
    audible = (freqs >= AUDIBLE_RANGE[0]) & (freqs < AUDIBLE_RANGE[1])
    nearest = np.round(freqs / fundamental) * fundamental
    width = max(HARMONIC_WIDTH * fundamental, 6 * rate / len(flat))
    harmonic = (np.abs(freqs - nearest) <= width) & (nearest > 0)
    total = float(power[audible].sum())
    stray = float(power[audible & ~harmonic].sum())
    return db(np.sqrt(stray / total)) if total and stray else float("-inf")


# --- slicing a break ------------------------------------------------------------------


def fit_tempo(break_bpm: float, target_bpm: float) -> float:
    """The break's tempo after doubling or halving to sit near the target."""
    bpm = break_bpm
    while target_bpm / bpm > FIT_RANGE[1]:
        bpm *= 2
    while target_bpm / bpm < FIT_RANGE[0]:
        bpm /= 2
    return bpm


def _flux(signal: Audio, hop: int) -> Audio:
    frames = len(signal) // hop
    energy = np.array([np.sum(signal[i * hop : (i + 1) * hop] ** 2) for i in range(frames)])
    return np.maximum(np.diff(energy, prepend=0.0), 0.0)


def _quietest_before(signal: Audio, at: int, reach: int) -> int:
    window = np.abs(signal[max(at - reach, 0) : at + 1])
    if not window.size:
        return at
    latest_quietest = len(window) - 1 - int(np.argmin(window[::-1]))
    return max(at - reach, 0) + latest_quietest


def slice_points(signal: Audio, rate: int, bpm: float) -> list[tuple[int, int]]:
    """Eight slices of the first bar, each start moved to the strongest onset
    within 30 ms, then back to the quietest sample just before it, so the cut
    never lands inside an attack. (start, frames) pairs."""
    eighth = round(60 / bpm / 2 * rate)
    hop = max(rate // 1000, 1)
    flux = _flux(signal, hop)
    reach = round(ONSET_SNAP_S * rate) // hop
    starts = []
    for n in range(SLICES_PER_BAR):
        frame = n * eighth // hop
        lo, hi = max(frame - reach, 0), min(frame + reach + 1, len(flux))
        best = lo + int(np.argmax(flux[lo:hi])) if hi > lo else frame
        starts.append(_quietest_before(signal, best * hop, hop) if n else 0)
    ends = [*starts[1:], min(SLICES_PER_BAR * eighth, len(signal))]
    return [(s, max(e - s, 1)) for s, e in zip(starts, ends, strict=True)]


# --- loudness and levels -------------------------------------------------------------


def db(value: float) -> float:
    return float(20 * np.log10(value)) if value > 0 else float("-inf")


def levels(data: Audio, rate: int) -> dict[str, float]:
    meter = pyloudnorm.Meter(rate)
    upsampled = soxr.resample(data, rate, rate * TRUE_PEAK_OVERSAMPLING, quality="VHQ")
    audible = np.nonzero(np.abs(data).max(axis=1) > SILENCE)[0]
    return {
        "integrated_lufs": round(float(meter.integrated_loudness(data)), 2),
        "sample_peak_dbfs": round(db(float(np.abs(data).max())), 2),
        "true_peak_dbtp": round(db(float(np.abs(upsampled).max())), 2),
        "dc_offset_dbfs": round(db(float(np.abs(data.mean(axis=0)).max())), 1),
        "lead_in_ms": round(1000 * int(audible[0]) / rate, 2) if audible.size else 0.0,
        "seconds": round(len(data) / rate, 3),
    }
