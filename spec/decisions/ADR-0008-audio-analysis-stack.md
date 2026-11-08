# ADR-0008: Audio analysis with librosa, soundfile and pyloudnorm

**Status:** accepted
**Date:** 2026-11-08

## Context
REQ-CAT-04 needs BPM, key, loudness, onsets and spectral features, computed on
demand on a 16 GB M1 Pro. REQ-ENG-05 requires runtime dependencies to be justified.

## Decision
- **soundfile** (BSD, libsndfile) decodes WAV, AIFF, FLAC, OGG and MP3 into
  float32 in memory. Nothing is written to disk (REQ-AUD-01).
- **librosa** (ISC) provides onsets, beat tracking, chroma, HPSS and spectral
  features. Key is estimated from mean chroma by correlating with key
  profiles, implemented here in a few lines so it's easy to read. Temperley's
  profiles replaced Krumhansl-Kessler in analyser v2 after a check on 50
  labelled loops from the Novation Anniversary 3rd Drop: key or relative
  matched the label on 25 against 18, and key or a neighbouring key on 36
  against 32. EDM-specific profiles (Faraldo's "edma") scored 18 and 31.
- **pyloudnorm** (MIT) measures integrated loudness (ITU-R BS.1770-4).
- **numpy** for the glue.

All of them are deterministic, classic DSP. No neural models are involved.

## Alternatives considered
- *Essentia:* excellent key and BPM models, but AGPL-3.0, which is
  incompatible with this MIT repo if it's ever shared.
- *aubio:* GPL-3.0, and no macOS arm64 wheels.
- *Hand-rolled numpy DSP:* reinvents well-tested algorithms.

## Consequences
- Adds numpy, scipy, numba (via librosa), soundfile and soxr to the environment.
  These are mainstream packages with wheels for macOS arm64 and Linux.
- AAC/M4A (35 files in Dean's library, one of them misnamed .wav) isn't
  decodable by libsndfile. Those files are decoded by ffmpeg (already a doctor
  requirement) into a pipe through `mph.proc.Runner`: float32 at the native
  rate, held in memory only, so no file is written and no resampling happens
  (REQ-AUD-01). Failed analyses are retried on the next run.
- Benchmark measurement (`mph.bench.audio`, step 3) reuses soundfile, numpy
  and pyloudnorm: matching hits in stems, loudness and true peak. Stems are
  written by a few lines of code rather than soundfile, because libsndfile
  stamps float WAVs with the time of writing, and identical renders must be
  identical files (REQ-AUD-05).
