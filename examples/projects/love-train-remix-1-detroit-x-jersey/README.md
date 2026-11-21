# Love Train remix 1: Detroit x Jersey

An 8-minute remix of "Love Train" by PillowTalk x Soul Clap, featuring Greg
Paulus and Crew Love: Detroit techno with a splash of Jersey garage, at the
song's own 120 BPM. It is for Dean's own listening and is never to be
released: every render is marked `licence:private`. The track and its parts
are written up in [spec/tracks/love-train.md](../../../spec/tracks/love-train.md),
how remixes are made here in the `remix` skill
(`.claude/skills/remix/SKILL.md`), and the research behind it in
[docs/research/love-train-remix-practice.md](../../../docs/research/love-train-remix-practice.md).

| Benchmark | What it is |
|---|---|
| BM-LTDJ-05 | the whole track: 240 bars, 8 minutes |
| BM-LTDJ-01 | bars 25-52: build, departure and verse 1 |
| BM-LTDJ-02 | bars 53-80: pre-chorus and hook |
| BM-LTDJ-03 | bars 97-128: breakdown, the horn again and the peak |
| BM-LTDJ-04 | bars 173-204: dub-out, the last train and the outro |

The blocks are excerpts of BM-LTDJ-05 (REQ-TRK-03), so they play exactly
what the whole track plays in those bars.

## What it needs

Only the vocals and the train's horn come from the song, as clips of its
Demucs stems, pinned by hash. Everything else is synthesised or comes from
the catalogue. To render it you need:

1. Your own copy of the song: Love Train Remixes (Soul Clap Records SCR100),
   track 7, the 2016 original, as FLAC in a source folder. Dean's copy hashes
   to `406fc403707baff75c28c7605a3aa49fb0d6f57653a10c6ea222af76bc19a17b`;
   `mph catalogue sound <path>` shows yours.
2. `mph catalogue scan` and `mph catalogue analyse`, then its stems:
   `mph catalogue derive <its hash> stems` (Demucs htdemucs_ft on the CPU,
   about 9 minutes on an M1 Pro). A second run on Dean's Mac gave the same
   stems byte for byte, so the same file on a similar Mac should give stems
   with the same hashes as Dean's, and the scores then play as they are.
3. If your stems hash differently (another master, a WAV rip, or another
   machine's arithmetic), copy the project (`mph projects copy
   love-train-remix-1-detroit-x-jersey`) and put your stems' hashes in place
   of `c2b24168…` (vocals) and `4ed4ea4d…` (other) in BM-LTDJ-05.toml.
   Check the grid first: the scores assume bar 1 starts at 0 s at exactly
   120 BPM.

`mph projects fill` finds placeholders for the drums (the ride has no synth
stand-in, so it needs one), but never for the recordings.

## What Dean thought

- Version 1 (2026-11-14) was a new intro and then a cut to the original
  song. Dean rated all of it 0 stars: a re-edit of source bars isn't a
  remix. Dean's rule since: keep only the vocals and a signature sound or two
  from the source, and build drums, bass, harmony and effects new, weaving
  the vocal phrases through, with real sound design from the first block.
- Version 2 (2026-11-14) follows that rule. Dean, 2026-11-15: "great for
  now", and asked for a Renoise version to open and edit.
- 2026-11-21: all five Renoise takes pass every measurement and render
  unattended. The whole track's Renoise take is 26.5 minutes to render on
  an M1 Pro, -16.08 LUFS, +0.13 dBTP, a pump of -2.50 dB, duck steps of
  0.245 dB at most, and a 2.8 GB render folder; it needs about 6 GB free
  while it renders. The blocks measure -15.93, -15.12, -16.31 and -16.11
  LUFS.
- Next: a version anyone can render from free sources (issue #68).
