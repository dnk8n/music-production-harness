# Benchmarks

**Status:** draft (built in step 3; Dean's sign-off unlocks the first track spec)

Each benchmark is a short render (8 to 32 bars) with three parts:

1. **Description:** what Dean should hear, in plain words, written before rendering.
2. **Measurements:** automated checks that the render does what the score says.
3. **Verdict:** Dean listens on the feedback page and records pass/fail plus notes.

A benchmark passes when the measurements pass *and* Dean passes it. Renders
are 48 kHz / 24-bit AIFF via the delivery path, so they also exercise
REQ-AUD-03.

## Shared measurements

### REQ-BEN-01: Timing accuracy
Detected onsets fall within ±1 ms of their scored position (after swing),
for every drum hit in the benchmark.
- **Why:** a quantised groove that drifts sounds sloppy. We need to know the
  pipeline is sample-accurate before blaming the music.
- **Verify:** measure

### REQ-BEN-02: Every benchmark has a written expectation
A benchmark can't render until its description, tempo, length and
measurements are in this file.
- **Why:** writing the expectation first is what makes the listening test honest.
- **Verify:** test

### REQ-BEN-03: Benchmark QA report
Each render writes `report.json` next to the audio: score hash, tool
versions, measurements with pass/fail, loudness, true peak, and the licence marker.
- **Why:** the feedback page and the AI reviewer read the report, not the code.
- **Verify:** test

### REQ-BEN-04: Scores are data checked against this file
Each benchmark's score is a TOML file in `src/mph/bench/scores/`: tempo,
swing, bars, voices with their grids, velocity rules and a humanisation seed.
A test checks every score's tempo, swing and bar-1 grid against the grid
written here, so the score can't drift from the description.
- **Why:** this file stays the source of truth (ADR-0001), and the renderer
  reads plain data, not prose.
- **Verify:** test

### REQ-BEN-05: Kits come from the catalogue, with synth fallback
A voice asks the catalogue for a sound by role and words (e.g. a `kick`
one-shot matching "909"), and takes the best match. For noise-like roles
(hats, shakers, cymbals) it passes over tonal sounds: those whose spectral
flatness above 2 kHz is under 0.05, so they ring at a few sharp pitches
rather than hiss. It takes a tonal one only when nothing else matches, and
the report says how many it passed over. A voice with no match,
or one the score marks as synth, uses a SuperCollider drum voice instead. The
report names every sound used, with its hash and licence, and the score hash
covers those choices.
- **Why:** benchmarks should use Dean's own sounds, but still render on a
  machine without his library (CI, a fresh install). Dean failed seven drum
  benchmarks for one jarring hat (BHG2_Analogue_Hat_17, flatness 0.009,
  sharp peaks at 7.6, 8.9 and 10.1 kHz), where real hats measured 0.11 to
  0.17. Level matching can't fix a whistle, and EQ can't turn one into a
  hiss, so the fix is in the choosing.
- **Verify:** test

### REQ-BEN-06: Render layout
`mph bench render BM-...` writes to `data_dir/renders/<id>/<score hash>/`:
one stem per voice and a `master.wav` (32-bit float at the production rate,
REQ-AUD-02), the delivered `<id>.aiff` (48 kHz / 24-bit through the single
SRC and dither, REQ-AUD-03) and `report.json`. A Renoise render (REQ-BEN-10)
also writes the song, `<id>.xrns`, with its samples inside. Intermediate
files go in `work/`, which is deleted when the render finishes (REQ-RET-03).
- **Why:** the feedback page and the AI reviewer find everything for one
  render in one folder, keyed by exactly what was rendered.
- **Verify:** test

### REQ-BEN-07: Variants of a benchmark
A variant (a *take*) is one way of making a benchmark: an engine (Renoise
or SuperCollider), a sound recipe for each part, and levels. All variants
are equal alternatives: none is the "main" one. Every benchmark has these
standard variants:

| variant | what it is |
|---|---|
| `renoise` | the score as written, rendered in Renoise |
| `supercollider` | the score as written, rendered in SuperCollider (ADR-0009's fallback engine) |
| `synth-kit` | every part a SuperCollider synth voice (not for scores with a break) |

A score with a melodic voice or a sidechain has only `supercollider` as a
standard variant, because it always renders there (REQ-BEN-10, REQ-BEN-12).

A score can add its own under `[variants.<name>]`: a title, an engine,
and per-voice changes to `sound` (role, words, `synth`, or `pick`, the Nth
best catalogue match), `gain_db` and synth `params` (such as a filter
cutoff). A declared variant with no engine uses the engine REQ-BEN-10 picks.
`mph bench render BM-...` renders every variant; one whose engine isn't
installed here is skipped with a note, not counted as a failure.
`--variant NAME` renders just that one, and fails if it can't. Each variant
renders into its own folder, with its own score hash, measurements and
report, which names the variant. A verdict can cover every take of a
benchmark or just one (REQ-FBK-02).
- **Why:** Dean wants to compare different productions of the same part by
  ear (Renoise against SuperCollider, one sound design against another) and
  rate each on its own, without one take being privileged as "main". The
  same mechanism lets a whole track be made in
  SuperCollider when asked, and carries the parameter A/B comparisons
  (filter, sidechain bypass) of the later benchmarks.
- **Verify:** test

### REQ-BEN-09: Kit sounds are level-matched
Before a render, each sound's hit loudness is measured: the K-weighted power
(the ITU-R BS.1770 filter) of its loudest 100 ms, in dB. Its gain is then set
so that it lands on its role's target below, plus any `gain_db` the score
gives that voice. Gains are clamped to -30 dB to +12 dB and listed in the
report. Velocities and the measurements are unchanged, because the
templates get the same gain.

| role | target (dB) |
|---|---|
| kick | -12 |
| snare | -14 |
| clap | -14 |
| break | -14 |
| tom | -16 |
| conga | -17 |
| perc | -18 |
| rim | -20 |
| clave | -20 |
| hat_open | -21 |
| hat | -22 |
| shaker | -24 |

Roles not in the table aim for -18 dB.
- **Why:** catalogue sounds arrive at whatever level their pack chose, so a
  hat can jump out of the kit (Dean, on BHG2_Analogue_Hat_17 in
  BM-DRUM-01). Matching by role gives a balanced starting mix, and the
  table is the one place to change it.
- **Verify:** test

### REQ-BEN-10: Renoise is the render engine
With Renoise and the bridge tool installed (REQ-REN-03), a benchmark renders
through Renoise (ADR-0009). The song has one track per voice. Synth voices
are first rendered once by SuperCollider into one-shot samples, and break
slices become Renoise slice markers. mph renders the song, again for
determinism, then each track alone for its stem. The templates for matching
come from a second small song with each voice's sounds spaced apart. Without
Renoise, or with `--engine supercollider`, the render runs in SuperCollider
alone as before. A score with a melodic voice (glides, per-note filter
envelopes) renders in SuperCollider either way, and asking for Renoise is
refused with a plain reason. So does a score with a sidechain (REQ-BEN-12).
The report names the engine and its version.
- **Why:** what Dean hears is the song he can open and change, while CI and
  machines without Renoise can still render and test every benchmark.
- **Verify:** test

### REQ-BEN-11: Benchmarks belong to a project
Every score names its `project`: the group it belongs to. For the benchmarks
in this file, the project is the section heading it is written under (such
as "Drum patterns" or "Bass"), and a test checks that the two agree. A later
piece of work (a track, a set of experiments) can be its own project. The
listening page groups benchmarks by project (REQ-FBK-01).
- **Why:** the list keeps growing (Dean, 2026-11-10), and a project groups
  what is judged together, so the rest can be folded away.
- **Verify:** test

### REQ-BEN-12: Sidechain ducking
A score can sidechain voices to a trigger voice: `[sidechain]` names the
`trigger` (such as the kick), the `targets`, the `depth_db`, and the
`attack_ms` and `release_ms` of the dip. Every trigger hit in the score
starts the dip at that hit's exact sample, so the ducking follows the
pattern, not a detector listening to the kick (like a volume-shaper plugin
synced to the kick). The gain falls by the depth over the attack, then
rises back along a half-cosine curve over the release. Overlapping dips
add up, but never past the depth. A declared variant can set `sidechain = { depth_db =
0 }`, which bypasses it. Sidechained scores render in SuperCollider for
now (asking for Renoise is refused with a plain reason), where a ducker
after each target's synths applies the gain. To measure it, mph also
renders the targets with the sidechain bypassed: the note timing checks
use that dry render, and the gain is the ratio of the ducked target to the
dry one, in 0.5 ms windows.
- **Why:** pumping is a musical choice, so it should land exactly where the
  pattern says, every time, and stay deterministic (REQ-AUD-05). Measuring
  against the same target without the ducking shows the envelope itself, whatever
  the pad or bass is playing.
- **Verify:** test

## Drum patterns

Grid notation: 16 steps per bar, `x` hit (80% velocity), `X` accent (100%),
`.` rest, `o` open hat (80%). Swing delays every second 16th: at 58%, it lands
58% of the way through its 8th note instead of 50%. Kits come from the catalogue (909/808-style hits from the
Loopmasters techno packs, Photek hits) unless a pattern says "synth".

### BM-DRUM-01: Detroit four-to-the-floor
130 BPM, straight.
```
kick  X . . . X . . . X . . . X . . .
clap  . . . . x . . . . . . . x . . .
chh   . . x . . . x . . . x . . . x .
```
Hear: a driving, even pulse. Hats sit exactly between kicks (the offbeat
"tss"), clap on 2 and 4. Mechanical on purpose, like early Underground
Resistance or Jeff Mills.
- **Why:** the reference groove. If this isn't tight, nothing else will be.
- **Verify:** measure, listen

### BM-DRUM-02: Jersey garage shuffle
122 BPM, 16th swing 58%.
```
kick  X . . . X . . . X . . . X . . .
clap  . . . . x . . . . . . . x . . .
chh   x x x x x x x x x x x x x x x x
ohh   . . o . . . o . . . o . . . o .
```
Hear: the same four-on-the-floor, but every second 16th hat arrives late, so
the hats skip and bounce. Open hats on the offbeat give the Jersey and
Paradise Garage lift (Tony Humphries, Kerri Chandler).
- **Why:** swing is the main thing that separates the Jersey sound from Detroit.
  It tests that swing is applied and measured correctly.
- **Verify:** measure, listen

### BM-DRUM-03: UK garage 2-step
132 BPM, 16th swing 62%.
```
kick  X . . . . . . . . . x . . . . .
snare . . . . X . . . . . . . X . . .
chh   x . x x x . x x x . x x x . x x
```
Hear: no kick on beats 2 and 4. The kick skips, landing on 1 and the "and"
of 3, so the groove limps forward. A heavily swung hat carries it.
- **Why:** checks that the system handles broken kick patterns, not just 4/4.
- **Verify:** measure, listen

### BM-DRUM-04: Electro syncopation
128 BPM, straight, synth (808-style from SuperCollider).
```
kick  X . . x . . x . . . X . . x . .
snare . . . . X . . . . . . . X . . .
chh   x . x . x . x . x . x . x . x .
```
Hear: a funky, robotic kick that dodges the beat (Drexciya, Cybotron). The
snare holds 2 and 4 steady while the kick pushes and pulls around it.
- **Why:** tests synthesised drums (not samples) and syncopation.
- **Verify:** measure, listen

### BM-DRUM-05: Dub techno with ghost notes
124 BPM, straight.
```
kick  X . . . X . . . X . . . X . . .
rim   . . . x . . x . . x . . . . x .
chh   . . x . . . x . . . x . . . x .
```
Rim velocities alternate 100% and 45% (ghost notes).
Hear: a soft, deep kick with a quiet rimshot dancing around it. Some rims are
barely audible, more felt than heard (Basic Channel feel).
- **Why:** tests velocity dynamics. Ghost notes must be quieter, not missing.
- **Verify:** measure, listen

### BM-DRUM-06: Chicago jack
125 BPM, straight.
```
kick  X . . . X . . . X . . . X . . .
clap  . . . . x . . . . . . . x . . .
chh   x x . x x x . x x x . x x x . x
ohh   . . o . . . o . . . o . . . o .
```
Hear: busy, jacking 909 hats with an open hat cutting through on each offbeat.
The open hat is choked (cut short) by the next closed hat.
- **Why:** tests hat choke groups. An open hat ringing over a closed one is a
  classic programming error.
- **Verify:** measure, listen

### BM-DRUM-07: Tribal techno, 3 against 4
128 BPM. Kick in 4/4. Toms repeat every 3 sixteenths, and the tom cycle continues
across bar lines (the grid shows bar 1 only).
```
kick  X . . . X . . . X . . . X . . .
tom   x . . x . . x . . x . . x . . x
chh   . . x . . . x . . . x . . . x .
```
Hear: the toms seem to rotate against the kick, since the pattern only lines
up again every 3 bars. Hypnotic rather than repetitive.
- **Why:** polyrhythm is the base for the African drum layers in track 1.
- **Verify:** measure, listen

### BM-DRUM-08: Afro house clave
122 BPM. Son clave (3-2) over a 4/4 kick, with conga and shaker.
```
kick   X . . . X . . . X . . . X . . .
clave  x . . x . . x . . . x . x . . .
conga  . . x . . x . x . . x . . . x x
shaker x x x x x x x x x x x x x x x x
```
Hear: the clave's 3-2 call-and-response sits over a steady kick. Congas
answer between the clave hits, and the shaker gives constant motion with
natural, uneven velocity (±10% humanisation, seeded).
- **Why:** organic recorded percussion over electronic kick is the heart of
  the track 1 brief. Seeded humanisation tests determinism with variation.
- **Verify:** measure, listen

### BM-DRUM-09: Breakbeat chop
130 BPM. A catalogued break (Photek or Mutant Breaks) sliced at onsets and
re-sequenced: slice order 1 2 3 4 1 2 7 8 / 1 2 3 4 5 5 7 8.
Hear: the original break's feel, with a repeated slice in bar 2 creating a
stutter. No clicks at slice boundaries.
- **Why:** tests onset slicing, re-sequencing and click-free edits (fades of
  1 ms or less at zero crossings).
- **Verify:** measure, listen

### BM-DRUM-10: Speed garage drive
130 BPM, 16th swing 55%.
```
kick  X . . . X . . . X . . . X . . .
snare . . . . x . . . . . . . x . x x
chh   x x x x x x x x x x x x x x x x
ohh   . . o . . . o . . . o . . . o .
```
Snare roll velocity ramps from 60% to 100% over the last 4 steps of every
4th bar.
Hear: four-to-the-floor with a pushy shuffle, and a snare flam that builds
into each phrase.
- **Why:** tests velocity ramps and phrase-level variation (every 4th bar).
- **Verify:** measure, listen

## Bass

### BM-BASS-01: Acid line
130 BPM. A 16-step 303-style line, synthesised in SuperCollider from a
band-limited saw through a resonant low-pass filter and soft saturation, at
the production rate, with aliasing measured (REQ-AUD-04). It renders in
SuperCollider whatever the engine, because its glides and per-note filter
envelopes can't be one-shot samples in Renoise yet. `S` works as on a 303:
the note is held into the next step and glides to its pitch without
retriggering. Before a rest, it just holds for the whole step. Other notes
sound for half a step.
```
note   C2 C2 C3 C2 D#2 C2 G2 C2 C2 A#2 C2 C3 C2 F2 C2 G2
accent .  .  X  .  .   .  X  .  .  .   .  X  .  .  .  X
slide  .  .  .  .  S   .  .  .  .  S   .  .  .  .  .  .
gate   x  x  x  .  x   x  x  x  x  x   .  x  x  x  x  x
```
Hear: the squelch. Accented notes are louder and brighter, with a vocal
"wow". Slid notes glide in pitch without retriggering. Turning cutoff up
opens from muffled to buzzy, and resonance up makes it whistle and scream.
Live controls (laptop keyboard, through Renoise in step 4, standalone in step 3):

| Keys | Control | Range |
|---|---|---|
| `1` / `2` | cutoff down / up | 80 Hz to 8 kHz, log steps |
| `3` / `4` | resonance down / up | 0 to self-oscillation edge |
| `5` / `6` | envelope mod down / up | 0 to 100% |
| `7` / `8` | decay down / up | 50 ms to 2 s |
| `9` / `0` | accent amount down / up | 0 to 100% |

Variants (REQ-BEN-07) set the controls to the ends of their range:
`cutoff-low` (120 Hz), `cutoff-high` (2.5 kHz) and `resonance-high` (0.92).

Measurements: every note that isn't slid into starts within 1 ms of its step.
Accented notes are at least 2 dB louder than the same notes unaccented, and
with the controls as scored also brighter (higher spectral centroid); the
declared variants push the controls to the ends of their range, where an
accent needn't get brighter.
A slid-into note doesn't dip by more than 3 dB. Two short probe renders of
the voice alone check the rest. In one, the score's first slide is held
until it settles, and it must land within 10 cents of its pitch (a
57 ms note is too short and changing to measure pitch that finely). In the
other, a high, open, resonant note (C5, cutoff 8 kHz, resonance 0.9) must
keep the energy between its harmonics (aliasing) below -60 dB.
- **Why:** the 303 is the canonical acid sound and a hard test of filter
  quality, slides and live control.
- **Verify:** measure, listen

## Effects

### BM-FX-01: Delay throw
A single clap at bar 4 is sent to a tempo-synced dotted-1/8 delay with 45%
feedback. Hear: the clap echoes off-beat about 5 times, each quieter and darker
(low-pass in the feedback loop). The echoes stop cleanly, and nothing else is delayed.
- **Why:** tests sends, tempo sync and automation of a send for one hit.
- **Verify:** measure, listen

### BM-FX-02: Filter build
8 bars of BM-DRUM-01. A high-pass filter on the drum bus sweeps from 20 Hz to
1 kHz over bars 1 to 7, then snaps back at bar 8. Hear: the kick thins out and
the beat gets more tense, then the full weight slams back on the downbeat.
- **Why:** tests smooth automation (no zipper noise) and the "drop".
- **Verify:** measure, listen

### BM-FX-03: Reverb wash
A stab with a long hall (4 s decay), with the reverb return automated from
-inf to 0 dB over 2 bars. Hear: the stab grows a tail that swells into a wash,
with no metallic ringing.
- **Why:** tests reverb quality and return automation.
- **Verify:** measure, listen

### BM-FX-04: Beat repeat
Bar 4 of BM-DRUM-02 repeats its last 1/8 note at 1/16, then 1/32. Hear: a
stutter roll that tightens into the next bar, landing exactly on beat 1.
- **Why:** tests buffer FX, and that timing re-locks to the grid.
- **Verify:** measure, listen

## Sidechain

### BM-SC-01: Kick-triggered ducking
130 BPM, straight. BM-DRUM-01's kick, clap and hats, the BM-BASS-01 acid
line, and a sustained pad: a C minor chord (C3, E♭3, G3) held for 15 steps
of every bar, so it's sounding when each kick lands. In a melodic gate row,
`-` holds the note before it for one more step, so the pad's gate is `x`
then fourteen `-` and a rest. The pad and the acid are sidechained to the
kick (REQ-BEN-12).
```
kick  X . . . X . . . X . . . X . . .
clap  . . . . x . . . . . . . x . . .
chh   . . x . . . x . . . x . . . x .
```
Expected envelope on the pad and the acid: ducks 6 dB (±1 dB) within 5 ms
of each kick, and recovers to full level (within 0.5 dB) by the next 8th
note (230 ms at 130 BPM), along a smooth release curve: no step between
0.5 ms windows bigger than 0.5 dB. A `bypass` take sets the depth to 0 dB,
for A/B.
Hear: the pad and bass "breathe" with the kick. They pump, rising between
kicks and dipping as each kick lands. The kick punches through cleanly, with
no low-end smear. At 0 dB depth (bypass), the kick sounds buried, which
proves the effect.
- **Why:** sidechain is the backbone of club mix clarity. The measurement
  checks depth and timing, and the bypass A/B checks Dean's ear.
- **Verify:** measure, listen

## Vocals

### BM-VOX-01: Local vocal fitted
The catalogue search finds a vocal loop from the local library (e.g. Phonk
Hip Hop or Maximum EDM vocals) whose BPM and key suit 124 BPM in A minor.
It's time-stretched only if more than 2% off, and placed on the bar grid over
BM-DRUM-02. Processing: high-pass at 120 Hz, gentle compression (3:1), de-ess,
and a send to BM-FX-01's delay. It's kept at its native sample rate (REQ-AUD-01).
Hear: the vocal sits on the beat, on top of the mix but not detached from it,
with no pitch wobble or stretch artefacts. Consonants are clear.
- **Why:** proves catalogue search, fit and vocal chain on material Dean owns.
- **Verify:** measure, listen

### BM-VOX-02: Spoken word from an archive
A public domain spoken word recording is downloaded from Internet Archive or
LibriVox. Demucs separates it if there's music under it. It's trimmed to
phrases at silences, and three phrases are placed over 16 bars of BM-DRUM-07.
Hear: an old, characterful voice (room tone is fine) delivering clear
phrases, each starting on a downbeat, with no music bleed and no chopped words.
Marked `licence:clear` only if every ingredient is publishable.
- **Why:** track 1 fuses spoken word with techno, and the library has none.
  Tests download, licence tracking, separation and phrase detection.
- **Verify:** measure, listen

## Delivery and professional QA

### BM-MST-01: Mastered delivery
A 2-minute mini-arrangement of BM-DRUM-02, BM-BASS-01, BM-SC-01 and BM-VOX-01,
mastered and delivered. Measurements:
- integrated loudness within ±0.5 LU of `master.target_lufs_integrated` (default -8 LUFS),
- true peak at or below `master.max_true_peak_dbtp` (default -1.0 dBTP, 4x oversampled),
- no clipped samples, DC offset under -60 dBFS,
- low end mono: inter-channel correlation above 0.9 below 120 Hz,
- file is 48 kHz / 24-bit AIFF and lead-in is 10 ms or less (REQ-AUD-06),
- loudness range (LRA) reported, not gated.
Hear: loud and punchy but not squashed. The kick still has transient snap,
and nothing distorts on a big system.
- **Why:** club systems punish phasey sub and clipped masters. These are the
  numbers mastering engineers check.
- **Verify:** measure, listen

### BM-MST-02: Render determinism
BM-MST-01 rendered twice gives bit-identical SuperCollider stems, and the
Renoise master null-tests below -120 dBFS (REQ-AUD-05).
- **Why:** without determinism, A/B feedback is meaningless.
- **Verify:** measure

### BM-MST-03: Delivery path comparison
The same Renoise song delivered two ways: (a) Renoise renders 96 kHz float,
then libsoxr SRC plus TPDF dither (ADR-0003), and (b) Renoise renders 48 kHz /
24-bit directly. Report the null difference and spectra, and Dean does a
blind A/B.
- **Why:** ADR-0003 is a reasoned bet. This benchmark settles it with evidence.
- **Verify:** measure, listen
