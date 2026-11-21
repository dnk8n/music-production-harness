# Love Train remix 1, free: synthesised

Remix 1 of "Love Train" for everyone: the same 8-minute Detroit techno track
with a splash of Jersey garage as
[Dean's remix 1](../love-train-remix-1-detroit-x-jersey/README.md), at 120
BPM, with nothing taken from the song and no samples at all. Synths play
every sound, so anyone can render it from a fresh clone of this repo, it
sounds the same wherever it is rendered, and every render is marked
`licence:clear`. It is written up in
[spec/tracks/love-train.md](../../../spec/tracks/love-train.md), under "Love
Train remix 1, free: synthesised" (issue #68).

| Benchmark | What it is |
|---|---|
| BM-LTFS-05 | the whole track: 240 bars, 8 minutes |
| BM-LTFS-01 | bars 25-52: build, departure and verse 1 |
| BM-LTFS-02 | bars 53-80: pre-chorus and hook |
| BM-LTFS-03 | bars 97-128: breakdown, the horn again and the peak |
| BM-LTFS-04 | bars 173-204: dub-out, the last train and the outro |

The blocks are excerpts of BM-LTFS-05 (REQ-TRK-03), cut at the same bars as
remix 1's, so the two versions can be heard side by side.

## What is in it

- **Kept from remix 1**: the form and sections, the rolling bass, the
  Jersey organ bass, the chord stabs and the strings note for note, and the
  whole mix: the strips, the dub echo, the hall, every filter move and
  send, and the kick's 24 dB duck.
- **A 909-style kit**, synthesised: kick, clap, closed and open hats, ride,
  rim shot and a garage shaker, playing remix 1's patterns.
- **New vocal lines**, wordless soul phrases (oh, yeah, la, ooh, a hum) in
  C major and A minor, each cued on an even bar as the song's lines are.
  A soul singer (a formant synth) sings verse 1, the pre-choruses, the
  hooks and the ad-libs; a machine (a vocoder) teases the hook, sings the
  bridge and verse 2, joins the hook at the peak and sings the tag; and a
  group of three voices answers the hooks and holds chords under the
  ad-libs. Every note, syllable, accent and slide is written in
  BM-LTFS-05.toml, so any line can be changed.
- **The train's horn**, synthesised: an E major chord that swells and bends
  down across the drops at bars 34, 110 and 194.

## How to hear it

From a fresh clone, with no config and no samples:

```sh
uv sync
uv run mph doctor                         # SuperCollider is the one tool it needs
uv run mph bench render BM-LTFS-01 BM-LTFS-02 BM-LTFS-03 BM-LTFS-04 BM-LTFS-05
uv run mph bench serve                    # listen, and rate each take
```

On a Mac without Renoise, or anywhere with `--engine supercollider`, each
benchmark renders in SuperCollider; with Renoise set up (`mph renoise
install`), the `renoise` take renders too, as a song you can open and
change. In a Linux cloud copy each block took about 70 to 80 seconds to
render in SuperCollider (2026-11-21).

## What Dean thought

Not rated yet: the five benchmarks wait for Dean's ears (2026-11-21).
