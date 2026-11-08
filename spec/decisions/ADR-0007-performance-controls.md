# ADR-0007: Laptop keyboard first, controller mappings as data

**Status:** accepted
**Date:** 2026-11-08

## Context
Dean performs from a laptop keyboard now, with MIDI keyboards and
controllers later.

## Decision
Performance controls are a data table (control, target parameter, range,
curve). Bindings map keys to controls now, and MIDI CC/notes map to the same
controls later. Renoise's own mapping applies them inside the song.

## Consequences
Adding a controller means adding a binding file, with no code changes.
BM-BASS-01 defines the first table.
