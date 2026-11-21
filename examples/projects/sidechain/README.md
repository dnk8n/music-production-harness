# Sidechain

BM-SC-01: a held pad and the acid line ducked by the kick, in four takes:
the score's own (12 dB, 2 ms attack, 400 ms release), `exaggerated` (24 dB,
430 ms), `over-the-top` (36 dB, held 150 ms, 290 ms back) and `bypass`. Its
description and measurements are in
[spec/components/benchmarks.md](../../../spec/components/benchmarks.md)
(section "Sidechain"), with REQ-BEN-12.

```sh
uv run mph bench render BM-SC-01
```

## What Dean thought

- 2026-11-11: Dean couldn't hear the first version (6 dB, 200 ms release). The
  whole-mix measurement agreed: it moved the mix by about 0.5 dB. Ducking
  Dean's own stems offline gave the change 100 to 200 ms after each kick:
  6 dB/200 ms -0.5 dB, 10/350 -2.4, 12/400 -2.9, 18/430 -3.5, 24/430
  -3.7. The release matters more than the depth, because Dean's sample kick
  masks the first 100 ms or so. Synth kits read bigger (12/400 -4.7,
  24/430 -6.5), so thresholds come from Dean's renders.
- `exaggerated` is Dean's pick for the production sound: "if I had to choose
  one for the production sound I would pick it above all else".
- 2026-11-13: the 12 dB and exaggerated takes sound almost the same to Dean.
  Bypass against over-the-top is what teaches the effect, and over-the-top
  is "well worth implementing in a full track, for small portions around
  the buildup/drop".
- So for tracks: exaggerated is the default duck, and over-the-top is for
  short moments around build-ups and drops. Remix 1 ducks at 24 dB, 2 ms
  and 430 ms.
- Renoise takes, made the way Renoise users duck (Gainer automation, the
  *LFO or the *Signal Follower), are issue #30.
