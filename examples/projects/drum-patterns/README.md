# Drum patterns

Ten drum patterns, each a benchmark: BM-DRUM-01 to BM-DRUM-10. What each
should sound like, and how it is measured, is in
[spec/components/benchmarks.md](../../../spec/components/benchmarks.md)
(section "Drum patterns"). Every pattern has a Renoise take, a SuperCollider
take, a synth-kit take where it has sampled voices, and a fresh take with
sounds an agent picked from Dean's catalogue by hash (REQ-BEN-16).

```sh
uv run mph projects fill drum-patterns          # what your catalogue lacks
uv run mph bench render BM-DRUM-02              # every take of one pattern
uv run mph bench serve                          # listen and rate
```

Without a sample library every drum falls back to a SuperCollider synth,
except BM-DRUM-09, which slices a breakbeat and needs a break loop with a
known tempo. The fresh takes pin sounds from Dean's packs; elsewhere they
search the catalogue instead and say so in the report.

## What Dean thought

- 2026-11-09: one hat, BHG2_Analogue_Hat_17, made seven patterns jarring.
  It rings at a few pitches instead of hissing, so the kit now passes over
  tonal hats, shakers and cymbals (REQ-BEN-05).
- 2026-11-13: 45 of 46 takes resolved. BM-DRUM-04's synth-kit take stayed
  unresolved because it was identical to its other takes (the pattern is
  all synths), so all-synth scores no longer get a synth-kit take.
  BM-DRUM-03's synth-kit take was "too mechanical": Dean wants breakbeat
  or chopped samples in patterns like it later.
- 2026-11-13: Dean asked for a fresh take of every benchmark, choosing
  sounds with the newest tools. All of them passed their measurements.
- Open ideas: a jungle take of BM-DRUM-09 and an eleventh pattern,
  oldskool tech house (issue #49). The spec gets its benchmark first.
