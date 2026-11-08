# ADR-0002: Python orchestrates, SuperCollider synthesises, Renoise finishes

**Status:** accepted
**Date:** 2026-11-08

## Context
Dean has Renoise and SuperCollider, maybe Tidal. The output must come from
deterministic tools, end in an editable Renoise project, and be performable live.

## Decision
- **Python** for orchestration, catalogue, analysis, score data, file
  generation and QA.
- **SuperCollider in NRT mode** for synthesised parts (drums, 303, FX
  sources). Rendering from a score file is deterministic and faster than realtime.
- **Tidal Cycles** is optional, for live-coding patterns. Patterns that end up
  in a track get captured as score data.
- **Renoise 3.5.4** as the final creative tool: arrangement, mixing, live
  keyboard control, master render, driven by Lua and OSC.

## Alternatives considered
- *Everything in SuperCollider:* deterministic, but not the editable,
  performable project Dean wants.
- *Ardour or another DAW:* strong scripting, but Dean knows and owns Renoise.
- *Pure Python DSP:* reinvents tested tools and fails the "human team could
  have done this" test.

## Consequences
Three languages (Python, sclang, Lua), each confined to a narrow role behind
an interface. `.xrns` generation depends on Renoise's XML schema, which
REQ-REN-02 guards.
