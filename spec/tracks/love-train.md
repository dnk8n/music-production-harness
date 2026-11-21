# Love Train remixes

**Status:** draft (remix 1 started 2026-11-14; version 2 the same day; its free
version 2026-11-21)

Remixes of "Love Train" by PillowTalk x Soul Clap, featuring Greg Paulus and
Crew Love, for Dean's own listening and never to be released (2026-11-13).
Every render carries `licence:private` (REQ-MST-04), except those of remix
1's free version, which uses nothing of the song. Dean's guide to the
styles, in order: Detroit techno with Jersey garage, Afro with acid techno,
jungle with djembe, clean Detroit, dub techno and acid tech. Each remix is
about 8 minutes, one Renoise song with short blocks rated first (REQ-TRK-03),
and each is finished before the next starts. Research:
[Remix practice for Love Train](../../docs/research/love-train-remix-practice.md)
(2026-11-14; this spec wins where they differ).

## The source and its stems

The source is Dean's copy in Dean's full tracks folder, named here by its
catalogue hash (BLAKE2b-256),
`406fc403707baff75c28c7605a3aa49fb0d6f57653a10c6ea222af76bc19a17b`: a FLAC,
44.1 kHz, 16-bit stereo, 14,885,265 frames (5:37.53). Its tags name the
album Love Train Remixes (Soul Clap Records SCR100, 2026), track 7 of 7, the
2016 original. That release also has a percapella and instrumentals, which
would be cleaner sources than separated stems. Its loudness is -11.9 LUFS
over the whole song, peaking at -0.09 dBFS: bars 1-8 sit at -22.9 LUFS, the
train at -19.9 and the grooves at about -12.

It sits on a fixed grid of exactly 120 BPM, and its bar 1 starts at its
first sample, so bar *n* starts at (*n* - 1) x 2 s (REQ-CAT-10). The
catalogue's first beat (0 s) and drift (0 ms) for it are measured, not
defaults. It runs 168 bars and a short tail.

Its stems come from `mph catalogue derive SOUND stems` on Dean's Mac
(2026-11-14): Demucs' htdemucs_ft on the CPU, about 9 minutes, giving 32-bit
float WAVs at 44.1 kHz, 337.5 s each, kept in mph's cache. A second run
matched byte for byte, so a stem's hash names it on any machine with the
same source:

| Stem | Hash |
|---|---|
| vocals | `c2b24168a2cb70f09d14efee4b06d95ec36a29b1a80b16fe8fbd775ddd34092e` |
| other (the train's horn) | `4ed4ea4d21f7ca9718a3ee67823eb11f4d762f5023eadcd332b02eca6e44b2c7` |
| drums | starts `3a4dae8778de` |
| bass | starts `981ec42c3b4c` |

Its passages, from a bar-by-bar map of the full mix (levels in four bands,
stereo width and chords):

| Bars | What plays |
|---|---|
| 1-8 | a quiet drum intro, with no kick or bass |
| 9-12 | the train: a chugging figure and a horn that swells and bends, with no low end |
| 13-20 | the groove: kick and bass, narrow |
| 21-36 | the groove with chords, wider |
| 37-52 | narrow and steady: likely the first verse |
| 53-68 | everything, wide: likely the chorus |
| 69-76 | a break: kick and bass, with the middle and top pulled down |
| 77-84 | narrow and brighter: likely the second verse |
| 85-92 | everything, wide: likely the second chorus |
| 93-108 | instrumental, wider from bar 101: likely horns |
| 109-110 | a two-bar stop |
| 111-122 | narrow: likely the third verse |
| 123-142 | wide: chorus and horns |
| 143-158 | narrower: verse-like |
| 159-162 | the outro begins: kick and bass drop back |
| 163-166 | the train again |
| 167-168 | the tail |

## Love Train remix 1: Detroit x Jersey

Dean's first pick (2026-11-13): Detroit techno meets Jersey garage, at the
source's own 120 BPM, so the vocals are never stretched. The plan has 240
bars (8:00).

The first version was a re-edit, as the research advised: the source's own
bars in the remix's form, with BM-DRUM-02's Jersey shuffle where no source
bars played. Every take passed its measurements, and Dean gave all five
blocks 0 stars, unresolved (ratings 226 to 234, 2026-11-14): "I only hear a
different intro, then a cut to the original song. It needs to be a lot more
interwoven. Just take the vocals, and the train siren... and I need to hear
predominantly Detroit Techno with sophisticated sound design, with a splash
of Jersey."

So the second version keeps two things from the source, each from its stem
(REQ-CAT-11, REQ-TRK-05): the vocal lines and the train's horn. Everything
else is new, and the vocal lines are woven through it one at a time.

### What version 2 takes from the source
The Mac mapped the stems on 2026-11-14 (vocal level and pitch per beat,
the horn's level and pitch, the bass and chords per bar). Places are given
as bar.beat.

Love Train is one two-bar loop from bar 13 to bar 158: Cmaj7 on odd bars and
Fmaj7 on even bars, over a bass that walks C, G, C, G and F, F, F, G. The
key reads as C major or A minor. Every vocal line starts on beat 2 of an
even bar, as a pickup into a phrase that starts on the next odd bar (one
starts on beat 3). So a line placed in the remix starts on an even bar too,
which keeps it on its own chords.

| Line | Clip (source bars) | Sings | Lasts |
|---|---|---|---|
| Verse 1, lines 1 to 3 | 36-39, 40-43, 44-47 | from 36.2, 40.2 and 44.2 | 13 beats each |
| Pre-chorus 1 | 48-55 | from 48.2 | 28 beats |
| Hook 1, lines 1 to 3 | 56-59, 60-63, 64-67 | from 56.2, 60.2 and 64.2, higher (around C4) | 12 beats each |
| Vamp | 68-77 | from 68.3 | 35 beats |
| Bridge line | 84-91 | from 84.2 | 29 beats |
| Verse 2, lines 1 to 3 | 110-113, 114-117, 118-121 | as verse 1 | 13 beats each |
| Pre-chorus 2 | 122-129 | from 122.2 | 28 beats |
| Hook 2, lines 1 to 3 | 130-133, 134-137, 138-141 | as hook 1 | 12 beats each |
| Ad-libs 1 to 3 | 142-145, 146-149, 150-153 | from 142.2, 146.2 and 150.2 | 8 beats each |
| Tag | 158-163 | from 158.2 | 22 beats |

The voice sits between D3 and E4, mostly around G#3 to C4.

The train's horn is in the "other" stem, and at its loudest it bleeds into
the vocal stem, so each horn plays from both stems at once. It sounds like
an E major chord (E3 with partials at E5 and G#5), set against the song's
harmony rather than in it.
- Horn 1 (clip 10-13): starts at 10.2, swells to its peak at 12.3, then bends
  down about three semitones (E3 to C#3) across bar 13 and fades by 14.1.
- Horn 2 (clip 164-167): starts at 164.2, holds E3 while it rises, peaks at
  166.3, bends down across bar 167 and ends at 168.1.

The bend crosses the bar where the original's groove drops, so a horn
placed with its bend going into a section's first bar drops that section.

### The parts
Detroit leads, with a splash of Jersey: 120 BPM with a light 16th swing of
55%, in the sections that list each part.
- **Drums**, a 909 kit from Dean's library: four-to-the-floor kick with a
  push on the last eighth of every fourth bar, claps on 2 and 4, closed
  hats on the sixteenths around open hats on the off-beats (the closed hat
  chokes the open one), a ride on the eighths, and a syncopated two-bar rim
  figure. A shaker on the sixteenths is the Jersey part. The kick is a
  tight one, down 18 dB 150 ms after it hits, so the pump comes through:
  the first pick held its body near full level for 150 ms, which hid the
  duck in the whole mix on Dean's Mac (2026-11-14).
- **Bass**, a new synth: a sine sub under a band-limited saw through a
  resonant low-pass with a short envelope. It rolls on every sixteenth, C
  in odd bars and F in even bars, with octave jumps and a glide down to G,
  and the kick ducks it, so it rolls between the kicks.
- **Organ bass** (Jersey), a new synth after BM-BASS-02's Korg M1 "Organ 2":
  a triangle-saw blend, a square three octaves up and a triangle a fifth
  above, with a fast filter envelope and a touch of chorus kept off the low
  end. It plays a syncopated garage line with octave jumps in the hooks,
  in place of the Detroit bass.
- **Chord stabs**, a new synth: detuned saws and a square with a fast filter
  envelope, one minor seventh shape moved in parallel (chord memory): E
  minor 7 over the C bars and A minor 7 over the F bars, which make Cmaj9
  and Fmaj9 with the song's roots. They fall on off-beat sixteenths, mostly
  in the gaps between the vocal lines, and feed the echo.
- **Strings**, a new synth: three detuned band-limited saws per note,
  spread left and right, with a slow attack, delayed vibrato and a long
  release. They swell into Cmaj9 and Fmaj9, voiced above the voice, in the
  departures, the pre-choruses, the hooks, the breakdown, the peak and the
  dub-out. Two string parts take turns, one chord a bar each, so each
  chord rings out while the next swells in (as a tracker uses two note
  columns), and the common notes E and G hold across the change.
- **The mix** (spec/components/mix.md): a dub echo on a dotted eighth that
  alternates left and right and fades into a 3.2-second hall, and a strip
  on every part. The bass and stabs open up through the builds and close
  down through the dubs, where the stabs and the rim sink deeper into the
  echo. The strings swell in under each horn, and the closed hats rise
  under the second. The kick ducks the parts that hold under it, the
  bass, the organ bass and the strings, with Dean's production duck: 24 dB
  in 2 ms, released over 430 ms (BM-SC-01's exaggerated take). The stabs
  sit on the off-beats and need none. The
  last word of every vocal line is thrown into the echo, the teaser and
  the outro's hook line are filtered thin, and each horn's bend is thrown
  into the echo as it lands.

### BM-LTDJ-05: Remix 1, the whole track
120 BPM, 16th swing 55%, 240 bars (8:00).

| Bars | Section | What plays | Vocals and siren |
|---|---|---|---|
| 1-8 | Intro | kick, closed hats, rim | |
| 9-16 | Intro | the ride joins | at 14, the first two bars of hook 1, filtered thin, cut off and thrown into the echo |
| 17-24 | Build | open hats and the bass join, the bass's filter opening | |
| 25-32 | Build | the clap and dark stabs join | the hook teaser again at 28 |
| 33-36 | Departure | the drums and bass stop; the strings swell and the shaker chugs | horn 1 swells from 34 and bends across the drop |
| 37-52 | Groove A | the whole kit, the bass, the stabs opening up | verse 1, lines at 40, 44 and 48; pre-chorus 1 from 52 |
| 53-60 | Pre-chorus | the strings join; the rim stops | |
| 61-76 | Hook | the shaker and the organ bass take over from the ride, rim and bass | hook 1, lines at 60, 64 and 68; the vamp from 72 |
| 77-92 | Dub | kick, rim, ride, closed hats, bass, stabs deep in the echo | the vamp ends at 81; the bridge line at 84 |
| 93-96 | Break | the stabs and the ride | pre-chorus 2 from 96 |
| 97-108 | Breakdown | strings, stabs, shaker | hook 2, line 1 at 104 |
| 109-112 | Departure II | strings, the closed hats rising | horn 2 swells from 110 and bends across the drop |
| 113-128 | Peak | the whole kit, the bass, stabs wide open, strings | hook 2, lines 2 and 3 at 116 and 120, hook 1 line 1 at 124 |
| 129-144 | Groove B | the kit, the bass, the stabs | verse 2, lines at 130, 134 and 138; pre-chorus 1 again from 144 |
| 145-152 | Pre-chorus 2 | the strings join; the rim stops | |
| 153-168 | Hook 2 | shaker and organ bass again | hook 2, lines at 152, 156 and 160; ad-libs at 164 and 168 |
| 169-176 | Ad-libs | kick, clap, hats, shaker, organ, stabs | the last ad-lib at 172 |
| 177-192 | Dub-out | kick, rim, ride, the bass closing down, stabs in the echo, strings | the tag line at 180 |
| 193-196 | Last train | strings and the shaker's chug | horn 2 swells from 194 and bends across the drop |
| 197-208 | Outro | the kit, the bass, the stabs | hook 1, line 1 at 200, deep in the echo and the hall |
| 209-224 | Outro | kick, hats, ride, rim, bass | |
| 225-240 | DJ outro | kick, closed hats, rim | |

Hear: a Detroit techno track that the song's voice moves through. A dry
909 groove builds with a rolling bass, and a fragment of the hook drifts
past in the echo. Everything drops away for the train's horn, whose bend
lands the groove. From there the vocal lines come one at a time, each on
its own chords, each last word echoing into the gap, with the stabs
answering in between. The hooks lift into Jersey: the organ bass, the
shaker and the swelling strings. The dubs strip back to kick, rim, ride and
echoing stabs, the breakdown floats on strings, and the horn drops the peak
and, at the end, the outro. Nothing of the original's band plays.
- **Why:** Dean's verdict on the first version (ratings 226 to 234): keep
  only the vocals and the train siren, interweave them, and make the rest
  predominantly Detroit techno with sophisticated sound design and a splash
  of Jersey. Placing each line on its own chords keeps the voice in tune
  with parts it was never sung over.
- **Verify:** measure, listen

### BM-LTDJ-01: Remix 1, bars 25-52: build, departure and verse 1
120 BPM, 16th swing 55%. Bars 25-52 of BM-LTDJ-05.

Hear: eight bars of 909 groove with dark, echoing stabs and the hook teaser
thrown into the echo. Then the drums and bass stop, the strings swell and
the train's horn rises over a chugging shaker. Its bend drops the groove on
bar 13 of the block, and verse 1 comes in line by line, each last word
echoing away.
- **Why:** the first thing to judge in version 2: does the new Detroit
  groove carry the voice, and does the horn land the drop?
- **Verify:** measure, listen

### BM-LTDJ-02: Remix 1, bars 53-80: pre-chorus and hook
120 BPM, 16th swing 55%. Bars 53-80 of BM-LTDJ-05.

Hear: the pre-chorus over the groove with the strings swelling in, then the
hook with the Jersey splash: the organ bass and shaker take over, the
strings hold Cmaj9 and Fmaj9, and the stabs answer each hook line. The vamp
runs into the end of the block.
- **Why:** where the vocal is strongest. It tests whether the hook sits on
  the new chords and whether the Jersey parts lift it without taking over.
- **Verify:** measure, listen

### BM-LTDJ-03: Remix 1, bars 97-128: breakdown, the horn again and the peak
120 BPM, 16th swing 55%. Bars 97-128 of BM-LTDJ-05.

Hear: pre-chorus 2 over floating strings and stabs, with no kick, then a
hook line. The second horn rises over rising hats and bends into the peak
on bar 17 of the block, where everything plays at once under three hook
lines.
- **Why:** the breakdown and the second departure are where the track has
  to hold without drums and then hit hardest. The block starts inside a
  vocal line, so it also tests an excerpt picking up a cued line.
- **Verify:** measure, listen

### BM-LTDJ-04: Remix 1, bars 173-204: dub-out, the last train and the outro
120 BPM, 16th swing 55%. Bars 173-204 of BM-LTDJ-05.

Hear: the end of the ad-libs, then the dub-out: kick, rim and ride with the
bass closing down, stabs and the tag line deep in the echo. The last horn
bends into the outro, where the first hook line returns as an echo.
- **Why:** the ending has to wind the track down without losing it. It
  tests the bass's long filter move and the horn's third drop.
- **Verify:** measure, listen

## Love Train remix 1, free: synthesised

Remix 1 for everyone (issue #68): version 2's form, parts and mix, with
every recording and sample made anew, so anyone can render it from a fresh
clone of the repo with no samples, no recordings and no keys. Every sound
is a synth (REQ-BEN-05), so every render carries `licence:clear`
(REQ-MST-04), the one exception to this file's `licence:private`. Dean's
answers on the issue (2026-11-15): CC-BY sounds are fine, everything done
"to the book and as inclusive as possible", and two variants: one with no
third-party API, with both a formant lead and a vocoder lead (this one),
and one with cleared vocal samples from APIs such as Freesound (to come, as
a project of its own).

### What it keeps from version 2
120 BPM with a 16th swing of 55%, 240 bars (8:00), the same sections, the
bass, organ bass, chord stabs and strings note for note, and the whole mix:
the strips, the dub echo and the hall, every filter move and send, and the
kick's duck on the bass, the organ bass and the strings (24 dB in 2 ms,
released over 430 ms).

### What is new
- **Drums**, a 909-style kit made by synths (`mph_kick909`, `mph_clap909`,
  `mph_hat909`, `mph_ride909`, `mph_rim909` and `mph_beads`), playing
  version 2's patterns. The kick's body is down at least 12 dB 150 ms after
  it hits (about 19 dB in SuperCollider), so the duck is heard after every
  hit. The hats, ride and shaker hiss rather than ring: their spectral
  flatness is more than twice the tonal limit (REQ-BEN-05). The ride's hits
  fade before the next one, like a 909's.
- **Vocals**, new wordless soul lines (oh, yeah, la, ooh, a hum; REQ-BEN-19)
  in C major and A minor, over the parts' Cmaj9 and Fmaj9. Each line is a
  phrase (REQ-TRK-08) cued on an even bar and sung from its second beat, as
  the song's lines are, so it stays on its chords, and the last word of
  each line is thrown into the echo. Three voices sing them:
  - the **singer**, a soulful lead (`mph_singer`, a formant voice), sings
    verse 1, the pre-choruses, the hooks, the vamp and the ad-libs, between
    E3 and E4 (G4 at the top of an ad-lib), where the song's voice sits;
  - the **machine**, a vocoder lead (`mph_vocoder`), teases the hook in the
    intro and the build, sings the bridge in the dub, sings verse 2, sings
    the hook with the singer at the peak, a third below, and sings the tag
    in the dub-out. Dean asked for both a formant lead and a vocoder lead,
    so both sing in the one track, as the song's leads would trade lines;
  - the **group** (`mph_singer` in parallel major triads, softer and
    breathier) answers every hook line and the vamp's calls, and holds
    "ooh" chords under the ad-libs. Its triads are F major over the F bars,
    and G or C major over the C bars, which keeps every note in the key.
- **The train's horn**, a synth (`mph_horn`) after the source's horn: an E
  major chord (E3, G#3 and B3), set against the song's harmony as the
  source's is, from the second beat of an even bar. It swells for 4.5
  seconds to its peak on the third beat of its third bar, holds, then
  bends down three semitones over the next bar (the drop) as it fades and
  darkens. Cued at bars 34, 110 and 194, as version 2's horns are, each
  bend lands a section.

| Line | Voice | Cued at bar | Bars | Sings |
|---|---|---|---|---|
| Teaser | machine | 14, 28 | 2 | the hook's first two bars, filtered thin |
| Verse 1, lines 1 to 3 | singer | 40, 44, 48 | 4 each | low and close, for about 13 beats each |
| Pre-chorus, two lines | singer | 52 and 56, 96 and 100, 144 and 148 | 4 each | long notes climbing from A3 to E4, holding D4 into the hook |
| Hook | singer | 60, 68, 104, 116, 124, 152, 160, 200 | 3 | up to C4, a turn, down from E4 and up to C4 again |
| Hook, reaching up | singer | 64, 120, 156 | 3 | the hook, ending on E4 |
| Answer | group | two bars after each hook | 2 | F major, then G and C major |
| Vamp | singer and group | 72, 76, and its end at 80 | 4, 4, 2 | calls, and the group's answers between them |
| Bridge, two lines | machine | 84, 88 | 4 each | straight eighths, a machine's line |
| Hook, a third below | machine | 116, 120, 124 | 2 | the hook's first two bars, under the singer |
| Verse 2, lines 1 to 3 | machine | 130, 134, 138 | 4 each | verse 1's lines |
| Ad-libs 1 to 3 | singer | 164, 168, 172 | 4 each | high and free, with runs, over the group's held chords |
| Tag, two lines | machine | 180, 184 | 4 and 2 | in the dub-out, deep in the echo |
| Horn | horn | 34, 110, 194 | 4 | E major, swelling, then bending across the drop |

Each benchmark has a `supercollider` take and a `renoise` take (Renoise
plays each synth's notes as samples SuperCollider made, REQ-REN-09); a
SunVox take is to come. Nothing in them depends on Dean's library, so two
fresh clones render the same audio (REQ-AUD-05).

### BM-LTFS-05: Remix 1 for everyone, the whole track
120 BPM, 16th swing 55%, 240 bars (8:00). Version 2's sections and parts
(BM-LTDJ-05), with the new kit, vocals and horn.

| Bars | Section | What plays | Vocals and horn |
|---|---|---|---|
| 1-8 | Intro | kick, closed hats, rim | |
| 9-16 | Intro | the ride joins | at 14, the machine's teaser, filtered thin and thrown into the echo |
| 17-24 | Build | open hats and the bass join, the bass's filter opening | |
| 25-32 | Build | the clap and dark stabs join | the teaser again at 28 |
| 33-36 | Departure | the drums and bass stop; the strings swell and the shaker chugs | the horn swells from 34 and bends across the drop |
| 37-52 | Groove A | the whole kit, the bass, the stabs opening up | the singer's verse 1, lines at 40, 44 and 48; the pre-chorus from 52 |
| 53-60 | Pre-chorus | the strings join; the rim stops | |
| 61-76 | Hook | the shaker and the organ bass take over from the ride, rim and bass | the hook at 60, 64 and 68, each answered by the group; the vamp from 72 |
| 77-83 | Dub | kick, rim, ride, closed hats, bass, stabs deep in the echo | the vamp ends at 81 |
| 84-92 | Dub | the same | the machine's bridge at 84 and 88 |
| 93-96 | Break | the stabs and the ride | the pre-chorus again from 96 |
| 97-108 | Breakdown | strings, stabs, shaker | the hook at 104, and the group's answer |
| 109-112 | Departure II | strings, the closed hats rising | the horn swells from 110 and bends across the drop |
| 113-128 | Peak | the whole kit, the bass, stabs wide open, strings | the hook at 116, 120 and 124, the machine a third below the singer, the group answering |
| 129-144 | Groove B | the kit, the bass, the stabs | the machine's verse 2, lines at 130, 134 and 138; the pre-chorus from 144 |
| 145-152 | Pre-chorus 2 | the strings join; the rim stops | |
| 153-168 | Hook 2 | shaker and organ bass again | the hook at 152, 156 and 160, answered; ad-libs at 164 and 168 over the group's chords |
| 169-176 | Ad-libs | kick, clap, hats, shaker, organ, stabs | the last ad-lib at 172 |
| 177-192 | Dub-out | kick, rim, ride, the bass closing down, stabs in the echo, strings | the machine's tag at 180 and 184 |
| 193-196 | Last train | strings and the shaker's chug | the horn swells from 194 and bends across the drop |
| 197-208 | Outro | the kit, the bass, the stabs | the hook at 200, filtered thin and riding the echo, and the group's answer |
| 209-224 | Outro | kick, hats, ride, rim, bass | |
| 225-240 | DJ outro | kick, closed hats, rim | |

Hear: a Detroit techno track sung by a soul singer, a machine and a group,
with nothing recorded. A dry 909 groove builds with a rolling bass, and the
machine teases the hook through the echo. Everything drops away for the
train's horn, whose bend lands the groove. The singer's verse comes in line
by line, each last word echoing into the gap, with the stabs answering in
between. In the hooks the group answers every line, and the organ bass, the
shaker and the swelling strings lift it into Jersey. The machine takes the
bridge in the dub and sings the second verse, and at the peak it sings the
hook with the singer, a third below. The horn drops the peak and, at the
end, the outro.
- **Why:** issue #68: a version of remix 1 anyone can render, learn from
  and share, with the same characteristics as Dean's: the form, the parts,
  the mix, a soulful lead with group answers, lines of two to four bars on
  even bars, a returning hook, ad-libs and the train's horn at the drops.
  Synths make every sound, so the render is the same anywhere and its
  licence is clear.
- **Verify:** measure, listen

### BM-LTFS-01: Remix 1 for everyone, bars 25-52: build, departure and verse 1
120 BPM, 16th swing 55%. Bars 25-52 of BM-LTFS-05.

Hear: eight bars of 909 groove with dark, echoing stabs, and the machine
teasing the hook through the echo. Then the drums and bass stop, the
strings swell and the train's horn rises over a chugging shaker. Its bend
drops the groove on bar 13 of the block, and the singer's first verse comes
in line by line, each last word echoing away.
- **Why:** the first thing to judge: does the synthesised kit carry the
  groove, does the singer sound sung, and does the synthesised horn land
  the drop?
- **Verify:** measure, listen

### BM-LTFS-02: Remix 1 for everyone, bars 53-80: pre-chorus and hook
120 BPM, 16th swing 55%. Bars 53-80 of BM-LTFS-05.

Hear: the pre-chorus climbing in long notes over the groove as the strings
swell in, then the hook with the Jersey splash: the organ bass and the
shaker take over, the strings hold Cmaj9 and Fmaj9, and the group answers
each hook line. The vamp, call and answer, runs into the end of the block.
- **Why:** where the vocal is strongest. It tests whether the new hook is
  one to remember and whether the group's answers sound like a group.
- **Verify:** measure, listen

### BM-LTFS-03: Remix 1 for everyone, bars 97-128: breakdown, the horn again and the peak
120 BPM, 16th swing 55%. Bars 97-128 of BM-LTFS-05.

Hear: the second pre-chorus over floating strings and stabs, with no kick,
then a hook line and the group's answer. The second horn rises over rising
hats and bends into the peak on bar 17 of the block, where everything plays
at once and the machine sings each hook line with the singer, a third
below.
- **Why:** the breakdown has to hold without drums, and the peak is where
  both leads sing together. The block starts inside a phrase, so it also
  tests an excerpt picking up a sung line (REQ-TRK-08).
- **Verify:** measure, listen

### BM-LTFS-04: Remix 1 for everyone, bars 173-204: dub-out, the last train and the outro
120 BPM, 16th swing 55%. Bars 173-204 of BM-LTFS-05.

Hear: the last ad-lib over the group's held chords, then the dub-out: kick,
rim and ride with the bass closing down, and the stabs and the machine's
tag deep in the echo. The last horn bends into the outro, where the hook
returns filtered thin and riding the echo, with the group's answer.
- **Why:** the ending has to wind the track down without losing it, with
  the machine's voice in the dub and the singer's hook as an echo.
- **Verify:** measure, listen
