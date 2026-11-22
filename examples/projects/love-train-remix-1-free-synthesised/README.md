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
  C major and A minor, each cued on an even bar as the song's lines are,
  sung by machines with soul. A talk box (a synth through a mouth) sings
  verse 1, high and busy, the pre-choruses, the hooks and the ad-libs; a
  vocoder teases the hook, sings the bridge and verse 2, joins the hook at
  the peak and sings the tag; a group of three voices answers the hooks and
  holds chords under the ad-libs; and the group, as vocoders, shouts "ho,
  hey!" on each drop and back at the talk box in the gaps of every verse
  line. Every note, syllable, accent and slide is written in
  BM-LTFS-05.toml, so any line can be changed.
- **The train's horn**, synthesised as an air horn sounds: five chimes in a
  real horn's voicing, moved into the key (E3 G3 A3 C4 E4), that swell and
  fall a fourth across the drops at bars 34, 110 and 194.

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
change. In a Linux cloud copy each block took 70 to 115 seconds to render
in SuperCollider, and the whole track about 5 minutes; on Dean's Mac the
whole track's Renoise take took under 13 minutes (2026-11-21). Every take
rendered so far passes every measurement.

## What Dean thought

BM-LTFS-01's SuperCollider take, 1 star (rating 246, 2026-11-22): "The
chords of the train horn have dissonant notes, and are too video game like.
I know there is much more potential for more sophisticated sound. The voice
substitutes don't match, we want high energy, soulful and interesting...
what we have is lethargic and low energy." Its Renoise take didn't render
then: a voice that rests in a block broke the song plan (fixed in PR #71).

What Dean asked for next (2026-11-22):
- Get BM-LTFS-01 right in both engines before the rest of the track.
- In this project the Renoise take doesn't rely on SuperCollider: it is
  built as a Renoise programmer would, with chosen plug-ins and Renoise's
  own effects and features.
- The vocals aim for machine soul with high energy: talkbox and vocoder
  leads with gospel group shouts, written for energy and owning that they
  are machines, as talkbox funk does.
- A way to fit a synth to a sound he provides, tuned to the track's key and
  tempo, that runs on his Mac without cloud credits. It is designed, and
  comes as its own spec and PR after this one.

What changed since:
- The horn is in the key, voiced as a real five-chime horn is, and built as
  an air horn sounds (a bell, air, beating chimes and the ground's
  reflection). It is built by hand, not yet fitted to the song's horn:
  that waits for the matcher.
- The vocals are machine soul: a talk box lead sings verse 1 high and busy
  (D4 to D5, where it was low and close), the vocoder sings the same lines
  in verse 2, and the group shouts on the drops and in the verse's gaps.
  The pre-choruses, hooks, vamp and ad-libs keep their lines, now on the
  talk box, until Dean has heard the verse.
