"""The single delivery step: one SRC, one dither (REQ-AUD-03, ADR-0003).

A 32-bit float master at the production rate becomes the deliverable: libsoxr
at very high quality (linear phase) to ``delivery_sample_rate``, then TPDF
dither once while reducing to ``delivery_bit_depth``, written as AIFF. The
dither noise is seeded, so delivering the same master twice gives the same
bytes (REQ-AUD-05). Nothing else in mph changes rate or bit depth.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile
import soxr

SUBTYPES = {16: "PCM_16", 24: "PCM_24"}
FORMATS = {"aiff": "AIFF", "wav": "WAV"}
DITHER_SEED = 0x6D7068  # "mph"


class DeliveryError(ValueError):
    pass


def tpdf_quantise(data: np.ndarray, bits: int, seed: int = DITHER_SEED) -> np.ndarray:
    """Float in [-1, 1) to integers of ``bits``, with triangular dither of +/-1 LSB."""
    scale = float(2 ** (bits - 1))
    rng = np.random.default_rng(seed)
    noise = rng.uniform(-0.5, 0.5, data.shape) + rng.uniform(-0.5, 0.5, data.shape)
    quantised = np.round(data.astype(np.float64) * scale + noise)
    return np.clip(quantised, -scale, scale - 1).astype(np.int32)


def deliver(
    master: np.ndarray, rate: int, out: Path, *, to_rate: int, bits: int, fmt: str = "aiff"
) -> Path:
    if bits not in SUBTYPES or fmt not in FORMATS:
        raise DeliveryError(f"can't deliver {bits}-bit {fmt}: use 16 or 24-bit aiff or wav")
    resampled = master if rate == to_rate else soxr.resample(master, rate, to_rate, "VHQ")
    integers = tpdf_quantise(np.asarray(resampled), bits)
    # libsndfile reads int32 as full scale: shift the samples to the top bits.
    out.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(
        str(out), integers << (32 - bits), to_rate, subtype=SUBTYPES[bits], format=FORMATS[fmt]
    )
    return out
