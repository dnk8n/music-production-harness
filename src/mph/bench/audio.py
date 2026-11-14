"""Audio for benchmarks: stems, slicing and measurement (REQ-BEN-01, REQ-BEN-03).

Timing is checked by matching: each voice is also rendered alone once (its
*template*), and every scored hit is found in the voice's stem within
+/-5 ms of where the hit should be. The match uses the template from its
start through its loudest sample (at least 20 ms, at most 250 ms, never
reaching into the voice's next note), and scores each position by shape, not
loudness: the correlation is divided by the stem's energy under the template
there. So a sound that swells in isn't pulled late toward its louder part
(REQ-BEN-01). That gives the timing error to the sample and the hit's level
(the least-squares gain), which is its velocity. Stems are separate, so other
voices never confuse the match.
"""

from __future__ import annotations

import bisect
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile
import soxr
from pyloudnorm.iirfilter import IIRfilter

from mph.bench.score import PULSES, Event

TEMPLATE_S = 0.02  # a match uses at least this much of the template
TEMPLATE_MAX_S = 0.25  # and at most this much, however late its loudest sample
SEARCH_S = 0.005
QUIET = 1e-12  # of the template's energy: below this, the stem counts as silent
SILENCE = 10 ** (-60 / 20)  # -60 dBFS
TRUE_PEAK_OVERSAMPLING = 4
SLICES_PER_BAR = 8
ONSET_SNAP_S = 0.03
WAVE_FLOAT = 3  # WAVE_FORMAT_IEEE_FLOAT
AUDIBLE_RANGE = (20.0, 20000.0)
FLATNESS_FROM_HZ = 2000.0
FLATNESS_SPAN_S = 0.5
FLATNESS_FFT = 16384
FLATNESS_MIN_FRAMES = 256
HARMONIC_WIDTH = 0.02  # +/- of the fundamental counts as on a harmonic
FIT_RANGE = (0.75, 1.5)  # break playback-rate factors allowed before doubling or halving
EXCERPT_PAD_S = 0.05  # resampled either side of a measured excerpt, then cut off
NO_MATCH_DB = 0.0  # what is left after cancelling against silence: nothing cancels

Audio = np.ndarray


@dataclass(frozen=True, slots=True)
class Match:
    event: Event
    error_ms: float
    level: float  # measured velocity


def read(path: Path) -> tuple[Audio, int]:
    data, rate = soundfile.read(str(path), dtype="float32", always_2d=True)
    return data, int(rate)


def info(path: Path) -> tuple[int, int]:
    """A sound file's rate and length in frames, read from its header."""
    found = soundfile.info(str(path))
    return int(found.samplerate), int(found.frames)


def read_frames(path: Path, first: int, count: int) -> Audio:
    """``count`` frames from frame ``first``, exactly as stored (REQ-AUD-01):
    16- and 24-bit samples are exact in 32-bit float."""
    data, _ = soundfile.read(
        str(path), start=first, stop=first + count, dtype="float32", always_2d=True
    )
    return np.asarray(data)


def common_step_s(native: int, rate: int) -> float:
    """The shortest time that is a whole number of samples at both rates:
    1/300 s for 44.1 and 48 or 96 kHz."""
    return 1 / math.gcd(native, rate)


def excerpt_at(path: Path, native: int, first: int, frames: int, rate: int) -> Audio:
    """``frames`` of a recording from frame ``first``, at another rate, for
    measuring only. It is resampled with some room either side, so the
    resampler's edges fall outside it. The room is a whole number of samples
    at both rates, so the excerpt's samples fall where a render's do."""
    step = native // math.gcd(native, rate)
    pad = min(round(EXCERPT_PAD_S * native), first) // step * step
    data = read_frames(path, first - pad, pad + frames + round(EXCERPT_PAD_S * native))
    moved = soxr.resample(data, native, rate, quality="VHQ") if native != rate else data
    skip = pad * rate // native
    return np.asarray(moved[skip : skip + round(frames * rate / native)])


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


def match_hits(
    stem: Audio,
    template: Audio,
    events: Sequence[Event],
    rate: int,
    *,
    voice_times: Sequence[float] = (),
) -> list[Match]:
    """Find each scored hit in the stem (REQ-BEN-01). ``voice_times`` are the
    starts of all the voice's notes, so a match never reaches into the next one."""
    sound = mono(template).astype(np.float64)
    signal = mono(stem)
    reach = round(SEARCH_S * rate)
    shortest, longest = round(TEMPLATE_S * rate), round(TEMPLATE_MAX_S * rate)
    loudest = int(np.argmax(np.abs(sound))) + 1 if len(sound) else 0
    starts = sorted(round(t * rate) for t in voice_times)
    found = []
    for event in events:
        at = event.sample(rate)
        later = bisect.bisect_right(starts, at)
        room = starts[later] - at - reach if later < len(starts) else longest
        tpl = sound[: max(shortest, min(loudest, room, longest))]
        energy = float(np.dot(tpl, tpl))
        segment = _window(signal, at - reach, 2 * reach + len(tpl))
        lag = _best_fit(segment, tpl, energy) - reach
        piece = segment[lag + reach : lag + reach + len(tpl)]
        level = float(np.dot(piece, tpl) / energy) if energy else 0.0
        found.append(Match(event, 1000 * lag / rate, level))
    return found


def _frames(signal: Audio, start: int, length: int) -> Audio:
    """Frames of a stem from ``start``, zero where they fall outside it."""
    out = np.zeros((length, signal.shape[1]), dtype=np.float64)
    lo, hi = max(start, 0), min(start + length, len(signal))
    if hi > lo:
        out[lo - start : hi - start] = signal[lo:hi]
    return out


def aligned(stem: Audio, wanted: Audio, at: int, reach: int) -> tuple[int, float, float]:
    """Where ``wanted`` sits in ``stem`` near frame ``at``: the lag in frames,
    within +/- ``reach``; how much of the stem is left there once ``wanted``,
    at its best level, is cancelled from it, in dB; and that level in dB
    (REQ-TRK-02). A stem with nothing there cancels nothing: 0 dB. A mono
    recording plays on both of a stem's channels."""
    shape = mono(wanted).astype(np.float64)
    segment = _window(mono(stem), at - reach, 2 * reach + len(shape))
    lag = int(np.argmax(np.correlate(segment, shape, mode="valid"))) - reach
    heard = _frames(stem, at + lag, len(wanted)).ravel()
    channels = stem.shape[1] if wanted.shape[1] == 1 else 1
    want = np.repeat(wanted.astype(np.float64), channels, axis=1).ravel()
    energies = float(np.dot(heard, heard)), float(np.dot(want, want))
    if not all(energies):
        return lag, NO_MATCH_DB, -math.inf
    shared = float(np.dot(heard, want))
    kept = max(1.0 - shared * shared / (energies[0] * energies[1]), 1e-12)
    return lag, 10 * math.log10(kept), db(abs(shared) / energies[1])


def _best_fit(segment: Audio, tpl: Audio, energy: float) -> int:
    """Where in the segment the template fits best by shape: the correlation at
    each offset, divided by the segment's energy under the template there."""
    scores = np.correlate(segment, tpl, mode="valid")
    sums = np.concatenate(([0.0], np.cumsum(segment * segment)))
    power = sums[len(tpl) :] - sums[: -len(tpl)]
    return int(np.argmax(scores / np.sqrt(np.maximum(power, energy * QUIET))))


def swing_of(matches: Sequence[Match], step_s: float) -> float | None:
    """Mean measured position of off-beat sixteenths within their 8th (0.5 = straight)."""
    ratios = []
    for match in matches:
        pair, offbeat = divmod(match.event.step, 2)
        if offbeat:
            onset = match.event.time_s + match.error_ms / 1000
            ratios.append((onset - pair * 2 * step_s) / (2 * step_s))
    return float(np.mean(ratios)) if ratios else None


def feel_of(matches: Sequence[Match], beat_s: float) -> dict[int, float]:
    """Mean measured start of each triplet pulse within its beat, as a share of
    the beat, for the pulses a voice plays after the first (REQ-BEN-13)."""
    found: dict[int, list[float]] = {}
    for match in matches:
        beat, pulse = divmod(match.event.step, PULSES)
        if pulse:
            onset = match.event.time_s + match.error_ms / 1000
            found.setdefault(pulse, []).append(onset / beat_s - beat)
    return {pulse: float(np.mean(found[pulse])) for pulse in sorted(found)}


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


def gain_curve(wet: Audio, dry: Audio, frames: int, floor: float = 1e-4) -> np.ndarray:
    """The level of ``wet`` against ``dry`` (the same signal before a gain was
    applied), in dB, over windows of ``frames``. Windows where ``dry`` is
    quieter than ``floor`` (RMS) can't say anything, so they're NaN."""
    count = min(len(wet), len(dry)) // frames
    shape = (count, frames)
    w = mono(wet)[: count * frames].astype(np.float64).reshape(shape)
    d = mono(dry)[: count * frames].astype(np.float64).reshape(shape)
    wet_power, dry_power = (w**2).sum(axis=1), (d**2).sum(axis=1)
    heard = dry_power > frames * floor**2
    out = np.full(count, np.nan)
    out[heard] = 10 * np.log10(np.maximum(wet_power[heard], 1e-30) / dry_power[heard])
    return out


@dataclass(frozen=True, slots=True)
class Dip:
    """One trigger hit's dip in a gain curve (REQ-BEN-12)."""

    depth_db: float  # how far the level fell
    reach_ms: float  # from the hit (or the target's first sound) until it had fallen ``reached_db``
    recovered_db: float  # the level ``recover_s`` after the hit, or as soon as heard after it
    # (nan if silent until the next hit, or cut short by it)
    step_db: float  # the biggest change between neighbouring windows on the way back


def dips(
    wet: Audio,
    dry: Audio,
    triggers_s: Sequence[float],
    *,
    rate: int,
    window_s: float,
    search_s: float,
    recover_s: float,
    reached_db: float,
) -> list[Dip]:
    """The dip at each trigger, from ``wet`` against ``dry``; hits where the
    dry signal is silent are left out. How fast the dip came is counted from
    the hit, or from the target's first sound after it if it starts later (a
    pad fading in on the kick): a dip can't be heard, or measured, in silence.
    A dip that the next hit interrupts is followed only up to that hit, and has
    no recovery to measure."""
    frames = max(1, round(window_s * rate))
    gain = gain_curve(wet, dry, frames)
    window = frames / rate
    ordered = sorted(triggers_s)
    found = []
    for n, at in enumerate(ordered):
        start = int(at / window)
        near = gain[start : start + math.ceil(search_s / window)]
        if near.size == 0 or np.isnan(near).all():
            continue
        deepest = start + int(np.nanargmin(near))
        heard = int(np.flatnonzero(~np.isnan(near))[0])
        reached = np.flatnonzero(near[heard:] <= -reached_db)
        reach = (reached[0] + 1) if reached.size else len(near) - heard + 1
        end = start + round(recover_s / window)
        following = int(ordered[n + 1] / window) if n + 1 < len(ordered) else len(gain)
        last = min(end, following - 1)
        steps = np.abs(np.diff(gain[deepest : last + 1]))  # neighbours only: NaN across silence
        steps = steps[~np.isnan(steps)]
        found.append(
            Dip(
                depth_db=-float(gain[deepest]),
                reach_ms=float(reach * window * 1000),
                recovered_db=_first_heard(gain[end:following]) if end == last else math.nan,
                step_db=float(steps.max()) if steps.size else 0.0,
            )
        )
    return found


def _first_heard(gain: np.ndarray) -> float:
    """The first level in a stretch of a gain curve where the target sounds:
    between two notes of a bass line, the gain waits for the next note."""
    heard = np.flatnonzero(~np.isnan(gain))
    return float(gain[heard[0]]) if heard.size else math.nan


def k_weighted(data: Audio, rate: int) -> Audio:
    """``data`` through ITU-R BS.1770's K-weighting (a high shelf, then a
    high pass), the filters pyloudnorm applies before measuring loudness, so
    a level compares as loudness does: sub-bass counts for less."""
    out = np.array(data, dtype=np.float64)
    for stage in (
        IIRfilter(4.0, 1 / math.sqrt(2), 1500.0, rate, "high_shelf"),
        IIRfilter(0.0, 0.5, 38.0, rate, "high_pass"),
    ):
        for channel in range(out.shape[1]):
            out[:, channel] = stage.apply_filter(out[:, channel])
    return out


def window_changes(
    wet: Audio,
    dry: Audio,
    hits_s: Sequence[float],
    *,
    rate: int,
    start_s: float,
    end_s: float,
    heard: Audio | None = None,
) -> list[float]:
    """For each hit, how much louder ``wet`` is than ``dry`` (dB, negative
    when quieter) over the window from ``start_s`` to ``end_s`` after it.
    Hits whose dry window is silent are left out, and so are hits where
    ``heard`` (what the change comes from) is silent in the window."""
    found = []
    for at in hits_s:
        first, last = round((at + start_s) * rate), round((at + end_s) * rate)
        dry_part, wet_part = dry[first:last], wet[first:last]
        if dry_part.size == 0 or wet_part.size == 0:
            continue
        dry_power = float(np.mean(np.square(dry_part, dtype=np.float64)))
        if dry_power <= SILENCE**2 or (heard is not None and rms(heard[first:last]) <= SILENCE):
            continue
        wet_power = float(np.mean(np.square(wet_part, dtype=np.float64)))
        found.append(10 * math.log10(max(wet_power, 1e-30) / dry_power))
    return found


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


def flatness(signal: Audio, rate: int, above_hz: float = FLATNESS_FROM_HZ) -> float:
    """Spectral flatness above ``above_hz`` over the first half second: the
    geometric over the arithmetic mean of the power spectrum. Near 0 for a
    tone (energy in a few sharp peaks), near 1 for white noise. A real hi-hat
    sits around 0.1 to 0.2; a hat that whistles sits under 0.05."""
    flat = mono(signal)[: round(FLATNESS_SPAN_S * rate)].astype(np.float64)
    if len(flat) < FLATNESS_MIN_FRAMES or not np.any(flat):
        return 1.0  # too short or silent to call tonal
    padded = np.zeros(max(FLATNESS_FFT, len(flat)))
    padded[: len(flat)] = flat * np.hanning(len(flat))
    power = np.abs(np.fft.rfft(padded)) ** 2
    band = power[np.fft.rfftfreq(len(padded), 1 / rate) >= above_hz] + 1e-30
    return float(np.exp(np.mean(np.log(band))) / np.mean(band))


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
