---
name: remix
description: Make a remix of a song from Dean's library as mph scores (a whole-track score and short blocks cut from it), rendered in SuperCollider and Renoise. Covers Dean's rules for remixes, the steps from stems to scores, and what went wrong before.
---

# Remix a song

The worked example is Love Train remix 1, Detroit x Jersey
(`spec/tracks/love-train.md`; scores BM-LTDJ-01 to BM-LTDJ-05 in the example
project `examples/projects/love-train-remix-1-detroit-x-jersey/`, whose
README says what Dean thought of each version). Read it first: it shows
every step below. The rules for tracks, clips, excerpts and stems are in
`spec/components/tracks.md`, the mix in `spec/components/mix.md`, and the
background research in `docs/research/love-train-remix-practice.md` (the
spec wins where they differ).

A new remix is a new project: a folder with a `project.toml` and its scores
(spec/components/projects.md). Dean's own remixes go under
`examples/projects/`; anyone else's go in a folder listed in
`[projects].folders` of their own config. `mph projects fill` lists what a
project needs that the catalogue lacks, and a remix's stems are recordings
no placeholder can stand in for.

## Dean's rules

- Keep only the vocals and one or two signature sounds from the source
  (Love Train: the train's horn). Everything else is new: drums, bass,
  harmony and effects, written to follow the source's chords, with real
  sound design (sends, filter moves, automation) from the first block. A
  re-edit of the source's own bars isn't a remix to Dean: remix 1's first
  version was one, every measurement passed, and Dean gave every block 0
  stars (ratings 226 to 234, 2026-11-14).
- Weave the vocal lines through the arrangement one at a time, each on its
  own chords, rather than playing long passages of the source.
- Never stretch the vocals: the remix runs at the source's tempo.
- About 8 minutes, as one Renoise song a person can edit, with short blocks
  of 28 to 32 bars that Dean rates first (REQ-TRK-03). Finish one remix
  before starting the next.
- Remixes are for Dean's own listening: every render carries
  `licence:private` (REQ-MST-04). Never quote the lyrics, in the repo or in
  messages. Name a line by where it sits and how long it lasts. A free
  version that uses nothing of the source is the exception: it is
  `licence:clear` (below).

## Steps

1. Name the source by its catalogue hash, never by its path, and write its
   facts into the track's spec file (`spec/tracks/<song>.md`): format,
   length, loudness, tags and grid, as Love Train's "The source and its
   stems" does.
2. Separate it on the Mac: `uv run mph catalogue derive SOUND stems`
   (htdemucs_ft on the CPU: about 9 minutes for a 5.5-minute song; cached
   in mph's data folder; the same bytes every run). Write the stems' hashes
   into the spec file too.
3. Map it on the Mac, from the full mix and the stems: the bars and
   sections, the chords and bass per bar, each vocal line's first beat,
   length and range (as bar.beat), and where each signature sound starts,
   peaks and ends. The catalogue's first beat and drift give the grid.
   Write the map into the spec file, and keep the raw measurements in mph's
   data folder under `analysis/<song>/` (Love Train's are in
   `analysis/love-train/`) for the next remix of the same song.
4. Write the whole track as one score: its sections, the new parts, the
   vocal lines as clips cued on their own chords (REQ-TRK-02, REQ-TRK-05),
   and the mix: strips, sends, automation and the sidechain. Then cut the
   blocks from it as excerpts, `excerpt = { of = "BM-<ID>-05", bars = [25, 52] }`,
   so a fix to the whole track reaches every block.
5. Spec each score with what to listen for ("Hear"), a Why and
   "Verify: measure, listen".
6. Render on the Mac, blocks first, in SuperCollider and in Renoise (the
   mac-work skill: check the disk first; a whole track is several GB).
   Every measurement must pass before Dean listens.
7. Dean rates each take on the listening page. Read the unresolved ratings
   and ideas with the `feedback` MCP tool, and list the rating ids a PR
   answers (CLAUDE.md rule 2).

## What went wrong before

- A kick with a long body hides the pump: remix 1's first kick held near
  full level for 150 ms, and the duck couldn't be heard in the whole mix.
  Pick a tight kick (about 18 dB down 150 ms after it hits) and watch the
  `sidechain: whole mix 100-200 ms after the kick` check.
- A loud source sound bleeds into the vocal stem: Love Train's horn plays
  from both its "other" and "vocals" stems at once, so cue both together.
- Look for where the lines sit on the song's loop. Every vocal line in Love
  Train starts on beat 2 of an even bar, a pickup into the next odd bar, so
  a line placed on an even bar of the remix stays on its own chords.
- Dean's production duck is BM-SC-01's exaggerated take: 24 dB in 2 ms,
  released over 430 ms. The parts that hold under the kick take it (bass,
  organ bass, strings); off-beat stabs need none.

## A free version: lines written as phrases

A free version is for anyone without Dean's library (issue #68). The first
is `examples/projects/love-train-remix-1-free-synthesised/` (BM-LTFS-01 to
05, spec/tracks/love-train.md): remix 1's form, parts and mix, with a 909
kit, a talk box lead (`mph_talkbox`), a vocoder lead (`mph_vocoder`), a
group (`mph_singer` with a chord), the group's shouts (`mph_vocoder` with a
chord) and the horn (`mph_horn`), all synths, so it
renders the same from any fresh clone. Its vocal lines are phrases
(REQ-TRK-08): notes with a syllable each (REQ-BEN-19), accents and slides,
one table per line, cued like clips. What it taught:

- Write the lines the source's way: from beat 2 of an even bar, 2 to 4
  bars long, in the key over the parts' chords, with a hook that returns
  and a group that answers it. A group in parallel triads stays in the key
  only on some roots (F, G and C major over C major's two chords): check
  every chord tone.
- Write the rows in aligned columns, a bar between each `|`. Only a
  one-step note slides, into a note on the very next step, and that note
  sings `-` (it keeps the syllable it glides from).
- A voice's level is measured on the first note of its first phrase, so
  put a line that represents the voice first.
- A section that lists a phrase voice needs one of its phrases playing in
  it, in every block too: a block cuts a section at its own bars, so a cue
  just past the block leaves the voice with nothing to carry on. Split the
  section where the voice comes in (the free version's dub is two
  sections). A phrase that runs on into the next section needs that
  section to list the voice too, or it stops there: a shout on the drop's
  pickup is cued in the section before the drop.
- Synth vocals that sing (syllables) ride on top of the pump, as a remix's
  recorded vocals do: the whole-mix check leaves them out (REQ-BEN-12), so
  set their level by the balance, not by the pump. The talk box lead sits
  about level with the music under it in the verse (`gain_db = -4`). A
  voice is level-matched on the first note of its first phrase, so a line
  sung much higher than that note comes out louder than its match.
- Synth vocals should own being machines (Dean, 2026-11-22): a formant
  singer imitating a soul voice, low and close, was "lethargic and low
  energy". Write high, busy lines with gaps for the group to shout back,
  and pick sounds that sound like what they are (a talk box, a vocoder).
- Check a render's balance bar by bar (each stem's K-weighted loudness
  against the mix) and each voice's gain in the report: a synth held at
  +12 dB is too quiet at its source (REQ-BEN-09). The first singing synths
  were 17 dB under their target until their output was raised.
- A free version with cleared vocal samples (Freesound and other free
  sources) is the next variant of issue #68.
