# North star

The palette of Dean's first track: spoken word over Detroit techno, with a
Jersey garage twist (Tony Humphries, Kerri Chandler) and West African drums
that mix machines with recordings. The precedent is Jeff Mills playing a
TR-909 live against Tony Allen's Afrobeat kit on *Tomorrow Comes the
Harvest* (2018). The section "North star" of
[spec/components/benchmarks.md](../../../spec/components/benchmarks.md)
describes eight benchmarks and their sources.

Built so far:

- BM-AFRO-01: the standard bell pattern on a 12-step triplet grid over a 909
  four, with a `flattened` take that squeezes it onto sixteenths, the way
  most Afro house programs it.
- BM-AFRO-02: the uneven long-short-short feel of Malian jembe drumming
  (41:31:28 of a beat, from Polak and London 2014), with an `even` take.

```sh
uv run mph bench render BM-AFRO-01
```

The bell, djembe (its stroke is a parameter: 0 bass, 1 tone, 2 slap) and
dunun are SuperCollider synths: Dean's library has no agogo, shekere or
talking drum, and its djembes are loops. `mph projects fill north-star`
looks for one-shots; on Dean's own catalogue these five voices are all it
finds missing across every example project (2026-11-21). On BM-AFRO-01
Dean preferred the synth-kit shaker.

Planned, one PR each: BM-AFRO-03 (a borrowed conga groove) and BM-MIX-01 (a
layered kick) in issue #39; BM-KEYS-01 (Detroit parallel stabs) and
BM-BASS-02 (a Jersey organ bass) in issue #40; BM-VOX-03 (a sermon in the
gaps) and BM-AFRO-04 (talking drum answers) in issue #41.
