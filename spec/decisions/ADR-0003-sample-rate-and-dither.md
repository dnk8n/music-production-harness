# ADR-0003: Produce at 96 kHz float, deliver once to 48 kHz / 24-bit

**Status:** proposed (settled by BM-MST-03)
**Date:** 2026-11-08

## Context
Dean: no pointless conversions, higher quality during production, and
48 kHz / 24-bit as the highest output needed. The library is mostly
44.1 kHz / 16-bit.

## Decision
- Synthesis and stems: 32-bit float at 96 kHz (configurable).
- Samples are never converted on disk. Renoise resamples them on playback
  with its sinc interpolation, which is one conversion per sample, unavoidable.
- Renoise renders the master at 96 kHz / 32-bit float.
- Delivery does exactly one SRC (libsoxr, very high quality, linear phase)
  and one TPDF dither to 48 kHz / 24-bit.

## Alternatives considered
- *Renoise renders 48 kHz / 24-bit directly:* one fewer tool, but we can't
  control its SRC/dither quality, and true peak must be measured after SRC anyway.
- *Run everything at 48 kHz:* simplest, but synth aliasing rises and there's
  no headroom for pitch-shifting.

## Consequences
More CPU and disk during production (fine on the M1 Pro for 13-minute
tracks). BM-MST-03 compares both delivery paths by null test and blind A/B,
and this ADR becomes `accepted` or `superseded` from the evidence.

The delivery step is `mph.deliver`: the `soxr` Python binding of libsoxr
(LGPL, already installed with librosa) at its VHQ setting, then seeded TPDF
dither in numpy, written by soundfile. Step 3 benchmarks are the first users.
SuperCollider plays catalogue samples at their native rate (`BufRateScale`),
which, like Renoise, is one unavoidable resampling on playback, never on disk.
