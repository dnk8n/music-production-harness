# Vision

**Status:** accepted

## Problem

Dean wants to make club-ready electronic music with a system that is easy to read,
tweak and rebuild. The system should not hand over a finished artefact from
a black box. The value is in the specification and the deterministic
toolchain, where every sound traces back to a sample, a synth patch or a
pattern that a human could have programmed by hand.

## Goals

1. **Spec as source of truth.** A senior engineer can regenerate the whole
   codebase from `spec/` in one pass, and judge alignment by reading both.
2. **Deterministic rendering.** The same spec, config and sources render to
   the same audio, bit for bit where the tools allow it.
3. **Judge by ear, not by code.** Every capability ships with a listening
   benchmark plus objective measurements, so Dean can give feedback without
   reading Python.
4. **Hands on.** The final project is an editable Renoise song that Dean can
   perform live from a laptop keyboard, and later from MIDI controllers.
5. **Pro delivery.** 48 kHz / 24-bit AIFF, mastered for an underground club
   system, at any length (13 minutes for the first track).
6. **Free first.** Free and open-source tools by default. Paid services plug
   in through config, and local fallbacks always exist.

## Non-goals

- Generating music with neural audio models (Suno-style). Neural tools are
  only allowed for analysis and source separation (Demucs), never to create
  the musical content.
- Publishing. Tracks are for private performance. Tracks whose every
  ingredient is cleanly licensed get the `licence:clear` marker so they
  *could* be published.
- Supporting operating systems other than macOS (Apple Silicon) at runtime.
  CI runs the platform-independent parts on Linux.

## Principles

- **Sources are sacred.** Source folders are read-only. Everything derived
  lives in `data_dir` and can be rebuilt from sources.
- **Bounded and pre-declared.** Inspired by the architect's notes: pipelines
  declare their resources (voices, stems, durations) up front and fail fast
  when limits are exceeded, rather than growing without bound.
- **Cheapest check first.** Deterministic checks first, then advisory AI,
  then a human.
- **Benchmarks before tracks.** No track spec is written until the benchmark
  kit passes Dean's ear (see [components/benchmarks.md](components/benchmarks.md)).
