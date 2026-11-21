# Bass

BM-BASS-01, a 303-style acid line, with takes for the cutoff turned down,
opened up and the resonance near the edge. Its description and measurements
are in [spec/components/benchmarks.md](../../../spec/components/benchmarks.md)
(section "Bass").

```sh
uv run mph bench render BM-BASS-01
```

It needs nothing from a sample library: the line is a SuperCollider synth.
The saw is built from sine partials, because SuperCollider's `Saw.ar` leaks
inharmonic tones at about -50 dB. Melodic scores render in SuperCollider
only for now; a Renoise take (a native Renoise instrument, with a modulated
filter and glides) is issue #30.

Dean resolved all its takes on 2026-11-13. It has no fresh take, because its
only voice is the synth: Dean's idea for one (idea 14) has nothing to pick,
so it can be dropped.
