# ADR-0009: Renoise holds the patterns, SuperCollider supports

**Status:** accepted
**Date:** 2026-11-08

## Context
Step 3 rendered the drum benchmarks entirely in SuperCollider NRT. That works
and is exact, but the result is a closed audio file. After listening, Dean
asked for patterns he can open and change by hand: one Renoise track per
part, with the sound, notes, volume and effects visible and editable.
SuperCollider is still the right tool for synthesising sounds,
pre-processing, and arrangements too intricate to type into a tracker.

## Decision
- **Renoise is where patterns live.** Each benchmark, and later each track,
  is a small Renoise song: one track and one instrument per voice, the notes
  in the pattern editor, and a volume and filter on every track. mph renders
  the delivered audio from that song, so what Dean hears is what he can open.
- **SuperCollider makes ingredients.** It synthesises one-shots and other
  sounds, does pre-processing, and handles coded arrangements. What it makes
  goes into the Renoise song as samples (or, for long coded parts, rendered
  tracks). It never produces the final mix on its own.
- **mph drives Renoise through a small Lua tool (REQ-REN-03).** Python
  writes a plan (tempo, tracks, sounds, notes, renders), and the tool builds
  the song through Renoise's own API, saves it, renders it, and reports back.
  The song is built by Renoise itself, not written as XML by hand.
- **The SuperCollider-only render stays as the fallback engine.** It runs
  where Renoise can't: CI and machines without Renoise. Both engines read the
  same score and kit and are measured by the same checks (REQ-BEN-01).

## Alternatives considered
- *Keep SuperCollider as the main engine and export a Renoise song on the
  side:* two sources of truth for one sound. Hand edits in Renoise would
  never be what was measured.
- *Write `Song.xml` directly from Python (the earlier plan in REQ-REN-01):*
  it works without Renoise running, but the schema is undocumented and
  changes between versions. Building through the Lua API gets correct songs
  from Renoise itself, and REQ-REN-02 becomes much easier to meet.
- *Tidal Cycles for patterns:* code, not a project a person edits by hand.

## Consequences
- A render needs Renoise running. It opens in the background and needs no
  clicks, but it isn't headless: Renoise has no headless mode.
- Renoise places notes to 1/256 of a line (under 0.5 ms at the benchmark
  tempos), so timing stays within REQ-BEN-01.
- Synth voices are rendered once by SuperCollider into one-shot samples, so
  a Renoise render of BM-DRUM-04 uses SuperCollider's sound and Renoise's
  timing.
- Refines ADR-0002: SuperCollider still synthesises, but Renoise also holds
  the patterns from step 3 on, not only the final arrangement.
