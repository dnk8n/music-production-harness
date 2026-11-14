# Benchmarks

**Status:** draft (built in step 3; Dean's sign-off unlocks the first track spec)

Each benchmark is a short render (8 to 32 bars) with three parts:

1. **Description:** what Dean should hear, in plain words, written before rendering.
2. **Measurements:** automated checks that the render does what the score says.
3. **Rating:** Dean listens on the feedback page and gives each take 0 to 5
   stars (3 or more is a pass), resolved or unresolved, and his feedback
   (REQ-FBK-02).

A benchmark passes when the measurements pass *and* Dean passes it. Renders
are 48 kHz / 24-bit AIFF via the delivery path, so they also exercise
REQ-AUD-03.

## Shared measurements

### REQ-BEN-01: Timing accuracy
Detected onsets fall within ±1 ms of their scored position (after swing),
for every drum hit in the benchmark.
A hit is found by matching the voice's own sound, rendered alone, within
±5 ms of where the hit belongs. The match uses the sound from its start
through its loudest point (at least 20 ms, at most 250 ms, and never into
the voice's next note), and it compares shape, not loudness. So a sound that
swells in is found where it starts, not pulled late toward its louder part.
Each sound is rendered alone held as scored, but for at most 0.75 s, so a
long pad's release has died away before the next sound of that voice
starts, 2 s later. The check names the hit that is furthest off.
- **Why:** a quantised groove that drifts sounds sloppy. We need to know the
  pipeline is sample-accurate before blaming the music. On 2026-11-13,
  BM-DRUM-02's fresh take failed at 5 ms with every hit on time. Its kick
  takes 101 ms to reach its loudest. A match on the first 20 ms that
  favoured louder audio slid all 31 later hits to the edge of the search.
- **Verify:** measure, test

### REQ-BEN-02: Every benchmark has a written expectation
A benchmark can't render until its description, tempo, length and
measurements are in this file.
- **Why:** writing the expectation first is what makes the listening test honest.
- **Verify:** test

### REQ-BEN-03: Benchmark QA report
Each render writes `report.json` next to the audio: score hash, tool
versions, measurements with pass/fail, loudness, true peak, and the licence
marker. It also names the checkout it was rendered from, when that is a git
checkout: the commit, the branch, and whether it had uncommitted changes.
- **Why:** the feedback page and the AI reviewer read the report, not the
  code. Threads render from their own branches (REQ-QRY-09), so the commit
  says which code made what Dean heard, and links a rating to the change
  that answered it.
- **Verify:** test

### REQ-BEN-04: Scores are data checked against this file
Each benchmark's score is a TOML file in `src/mph/bench/scores/`: tempo,
swing and feel (REQ-BEN-13), bars, voices with their grids, velocity rules
and a humanisation seed.
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
or one the score marks as synth, uses a SuperCollider drum voice instead. A
voice can instead pin one exact catalogue sound by its hash (REQ-BEN-16). The
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
SRC and dither, REQ-AUD-03) and `report.json`. Benchmarks aren't mastered,
so if the master's true peak is above -1 dBTP, the delivered file is turned
down to it, and `report.json` gives that trim as `delivery_gain_db`. A
Renoise render (REQ-BEN-10)
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
| `synth-kit` | every part a SuperCollider synth voice (not for scores with a break, nor for scores whose every part is a synth already) |

A score with a melodic voice or a sidechain has only `supercollider` as a
standard variant, because it always renders there (REQ-BEN-10, REQ-BEN-12).

A score can add its own under `[variants.<name>]`: a title, an engine,
and per-voice changes to `sound` (role, words, `synth`, `pick`, the Nth
best catalogue match, or `hash`, one exact catalogue sound, with `why` it
was picked: REQ-BEN-16), `gain_db`, synth `params` (such as a filter
cutoff) and a drum voice's `grid`. A score with a sidechain can also change
its `depth_db`, `attack_ms`, `hold_ms` and `release_ms` (REQ-BEN-12), and
any score its `feel` (REQ-BEN-13). A declared variant with no engine uses
the engine REQ-BEN-10 picks.
`mph bench render BM-...` renders every variant that has something new
(REQ-BEN-14); one whose engine isn't installed here is skipped with a note,
not counted as a failure. `--variant NAME` renders just that one, and fails
if it can't. Each variant renders into its own folder, with its own score
hash, measurements and report, which names the variant. A rating can cover
every take of a benchmark or just one (REQ-FBK-02).
No take repeats another: a standard take that would sound the same as one
already made isn't made. A standard take a score stops making keeps its
renders and ratings, and the query channel lists it as retired (REQ-QRY-09).
- **Why:** Dean wants to compare different productions of the same part by
  ear (Renoise against SuperCollider, one sound design against another) and
  rate each on its own, without one take being privileged as "main". The
  same mechanism lets a whole track be made in
  SuperCollider when asked, and carries the parameter A/B comparisons
  (filter, sidechain bypass) of the later benchmarks. BM-DRUM-04's score is
  all synths, so its synth-kit take was its renoise take over again. Dean,
  rating 156 (2026-11-13): it "ends up sounding exactly the same as the
  supercollider/renoise variants. Not much purpose even though I like it."
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
| dunun | -16 |
| conga | -17 |
| djembe | -17 |
| perc | -18 |
| bell | -19 |
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
`attack_ms`, `hold_ms` and `release_ms` of the dip. Every trigger hit in
the score starts the dip at that hit's exact sample, so the ducking follows the
pattern, not a detector listening to the kick (like a volume-shaper plugin
synced to the kick). The gain falls by the depth over the attack, stays
there for the hold (none unless the score sets one), then rises back along
a half-cosine curve over the release. Overlapping dips add up, but never
past the depth. A declared variant can change the depth, attack, hold and
release, such as `sidechain = { depth_db = 24, release_ms = 430 }`.
A depth of 0 dB bypasses it. Sidechained scores render in SuperCollider for
now (asking for Renoise is refused with a plain reason), where a ducker
after each target's synths applies the gain.

To measure it, mph also renders the targets with the sidechain bypassed.
The note timing checks use that dry render, and two kinds of measurement
compare against it:
- **Each target.** The gain is the ratio of the ducked target to the dry
  one, in 0.5 ms windows. At every hit, the target dips by the depth
  (±1 dB) within 5 ms. That's counted from the hit, or from the target's
  first sound when it starts after the hit, like a pad fading in on the
  kick. It is back within 0.5 dB of full level 10 ms after
  the release ends, or as soon as it sounds after that (a bass line can
  be between notes then). On the way back, the gain never changes by more than
  0.5 dB from one window to the next. A dip that the next hit cuts short,
  like one after a kick pushed onto the last eighth of a bar, is followed
  only up to that hit, so its recovery isn't checked. A target with no
  note held under any hit in the render is skipped: an excerpt can leave
  a target out, and a pad may only play where the kick rests.
- **The whole mix.** The ducked mix is compared with the same mix holding
  the dry targets, which is exactly what bypass sounds like. Both are
  K-weighted, as loudness is measured (ITU-R BS.1770). The comparison
  looks at two windows after every hit where a target sounds: under the
  kick (5 to 35 ms) and after it (100 to 200 ms). A whole track's intro,
  with no targets yet, has nothing to pump. Recordings that aren't
  targets, like a remix's vocals, are left out of both mixes: they ride on
  top of the pump, so it's measured in the music under them. After the kick, the median over the hits must
  be at least 2 dB down. That's about twice the smallest change in level
  people notice, so it's heard as pumping. The window under the kick is
  reported alongside it.
- **Why:** pumping is a musical choice, so it should land exactly where the
  pattern says, every time, and stay deterministic (REQ-AUD-05). Measuring
  against the same target without the ducking shows the envelope itself,
  whatever the pad or bass is playing. The envelope can pass while nothing
  is heard, so the mix is measured too. Dean couldn't hear a 6 dB dip
  released over 200 ms (2026-11-11). It changed the mix by about 0.5 dB:
  the kick masks the first 100 ms after each hit, and by then the dip was
  nearly over. A hold keeps the dip at full depth before it swells back:
  within one beat, the release can't grow much longer before the next
  kick, so a hold is how a volume shaper makes the pump last longer. A dip
  can't be heard or measured in silence: on 2026-11-14 a string pad that
  faded in on the kick read 8 ms, with the dip in place from the hit. In
  remix 1's second version the vocals are close to half the mix's
  loudness, so the same pump in the music under them would measure about
  half as much with them in.
- **Verify:** test

### REQ-BEN-13: Triplet grids and feel
A drum voice's grid has 16 steps (sixteenths) or 12 (eighth-note triplets,
three to a beat). Both kinds can play in one score: their beats line up,
and their subdivisions don't. `swing` moves sixteenths only. The score's
`feel` places the triplets. It gives the lengths of a beat's three pulses as
shares of the beat, and is even (`[1, 1, 1]`) unless the score says
otherwise. Each triplet voice gets a feel check: the mean measured position
of its second and third pulses within their beat must be within 0.005 of a
beat of where the feel puts them. A declared variant can change a voice's
`grid` or the score's `feel`, for an A/B. In Renoise, a song with a triplet
voice runs at 12 lines per beat, so sixteenths fall every 3 lines and
triplets every 4. Both sit on whole lines, and only swing and an uneven
feel go in the delay column, where Dean can see and change them.
- **Why:** West African bell patterns, and the drums that play around them,
  are ternary, and techno is binary. Track 1 fuses the two, so a score has
  to hold both in one bar without rounding either into the other. Even
  triplets aren't the whole story either. In measured performances of
  Malian drumming, the three pulses of a beat are consistently unequal,
  long-short-short at about 41:31:28 (Polak and London, 2014). That lean is
  much of what makes an ensemble sound played rather than programmed.
- **Verify:** test, measure

### REQ-BEN-14: A pass is a pass
`mph bench render` (named benchmarks or `--all`) renders a take only when a
render could come out different and its rating (REQ-FBK-06) doesn't hold it:
- it has never been rendered;
- or it isn't resolved, and since its newest render something that shapes
  it has changed: the take's score, the engine, the audio settings, or the
  code that renders and measures it (the render and measurement modules,
  the SuperCollider synth definitions and the Renoise bridge), which each
  report records as one digest, `inputs`; or, for a take that chooses its
  sounds afresh (REQ-BEN-15), the sounds it would choose now. Comments,
  docstrings and formatting don't count as changes.

A resolved take is skipped whatever changed. The others are skipped when
nothing changed, and the output says why each was: resolved, unresolved and
waiting for a change, or waiting for a rating. A failed measurement or new
feedback doesn't render a take again on its own, because the render would
come out the same. `--force-rerender` renders the takes asked for anyway. A
report written before `inputs` existed has none, so its take renders once
more unless it is resolved. A take whose audio was pruned is still skipped,
and the page renders it again on demand (REQ-RET-06).
- **Why:** Dean, 2026-11-12: "A pass is a pass". A benchmark he has passed
  is stable, and `mph bench render --all` must leave it alone. Dean,
  2026-11-13: a resolved take renders again only when forced, and an
  unresolved one when something changed; otherwise it "just NOPs out".
  Renders are deterministic (REQ-AUD-05), so rendering with nothing changed
  only spends minutes making the same file. Kit choice isn't in `inputs`,
  so for a take that chooses afresh mph compares the sounds it would pick
  now with the ones it used: a new sample or a better search still reaches
  a failed take.
- **Verify:** test

### REQ-BEN-15: A passed take keeps its sounds
When a take whose standing rating is a pass (3 stars or more, REQ-FBK-02)
renders again, each voice keeps
the sound that render used if the voice still asks for the same thing (its
role, words, synth, pick and pinned hash, which every report now records)
and the sound is still on disk. A voice is chosen afresh (REQ-BEN-05) only when its
request changed or its sound is gone. A report from before requests were
recorded keeps its sounds. The report says which sounds were kept.
Failed (0 to 2 stars) and unrated takes choose afresh.
- **Why:** catalogue search keeps getting better (ranking, labels,
  analysis), and any improvement can change which sound wins. A pass is for
  the sounds Dean heard, so a better search must not swap them out from
  under his rating. The same sounds and the same inputs give the same
  score hash, so his rating still stands after a render. To try another
  sound, change the voice's request in the score, which is a change he can
  see and judge.
- **Verify:** test

### REQ-BEN-16: Fresh takes: sounds an agent picked
A voice's sound can pin one catalogue sound by its hash, with why it was
picked: `sound = { role = "kick", hash = "<its 64 hex digits>", why = "..." }`.
The render plays exactly that sound, and the report and the listening page
give the reason. If the sound isn't in the catalogue here, isn't on disk,
can't be played as it is, or rings rather than hisses where a noise-like
role wants a hiss (REQ-BEN-05), the voice searches by its role and words as
usual (or falls back to a synth), and the report says what happened to the
pick. A pinned sound names its role, for its level (REQ-BEN-09) and that
search, and gives its reason. A sound pins a hash or asks for a synth, not
both, and a synth can carry a `why` too. The `synth-kit` take ignores pins.
A score's hash covers its pins, not their reasons.

A benchmark's `fresh-take` is the score as written, with sounds an agent
picked by exploring the catalogue through the query channel (REQ-QRY-12)
rather than the first hit of the fixed search the other takes use. It
differs from the benchmark's other takes in at least one voice. Where a
synth suits a voice better (Dean preferred the synth shaker on BM-AFRO-01),
the fresh take asks for it, with why.
- **Why:** Dean's ideas 1 to 14 (2026-11-13), one per benchmark: "Exactly
  as before, but use the latest functionalities like MCP server, etc to
  make choices". A passed take keeps its sounds (REQ-BEN-15), so a better
  search never reaches the takes he has heard, and a fresh search alone
  changes little (no take would pick other sounds on his catalogue). Pinning
  by hash keeps renders deterministic (REQ-AUD-05): the choice is made once,
  saved in the score with its reason, and Dean hears it next to the takes he
  has rated. Issue #31 automates the picking later.
- **Verify:** test, listen

### REQ-BEN-17: Patterns longer than a bar
A voice's rows can hold several bars, separated by `|`: a drum grid, and a
melodic voice's `notes`, `gate`, `accent` and `slide`. Every bar of a row
has the same number of steps (16, or 12 for a triplet grid), and all of a
voice's rows have the same number of bars. The pattern repeats from its
first bar, counted from the score's bar 1, so bar *n* plays the pattern's
bar ((*n* - 1) mod its length) + 1, and an excerpt plays what its track
plays there (REQ-TRK-03). A one-bar row reads as before, with no `|`.
- **Why:** Love Train's harmony changes every bar (Cmaj7, then Fmaj7), so a
  bass line or a chord part needs at least two bars, and drum parts get
  their fills and turnarounds every four or eight bars. Counting from the
  track's bar 1 keeps a pattern in step with the harmony wherever a block
  starts.
- **Verify:** test

### REQ-BEN-18: Chords
A melodic voice can play a chord on every note: `chord = [0, 3, 7, 10]`
lists the chord's notes in semitones above each note of `notes`, so one
shape moves with the line. In SuperCollider every note of the chord is a
synth voice of its own, started, slid and released together, and an accent
accents the whole chord. The voice's level (REQ-BEN-09) is measured on the
whole chord, and the aliasing probe (BM-BASS-01) plays its first note alone.
- **Why:** Detroit stabs and strings move one chord shape in parallel, the
  "chord memory" BM-KEYS-01 describes, and remix 1's stabs and strings play
  Love Train's two chords that way. One synth per note is how SuperCollider
  users play chords, and it keeps each note's oscillators band-limited.
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
The snare is a garage one from the catalogue (words `garage`), as in BM-DRUM-10.
- **Why:** checks that the system handles broken kick patterns, not just 4/4.
  The garage snare: see BM-DRUM-10.
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
Its score is all synths, so it has no synth-kit take (REQ-BEN-07); its
fresh take (REQ-BEN-16) plays sampled 808-style drums as the contrast.
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
Measured: from 8 ms after each closed hat that chokes it, until the open hat
plays again (50 ms at most), the open hat stays at least 60 dB below its
peak. The same check runs wherever a score chokes a voice (BM-DRUM-02, 10).
- **Why:** tests hat choke groups. An open hat ringing over a closed one is a
  classic programming error. A choke fades out rather than clicking off:
  SuperCollider's release takes 5 ms, and Renoise's note-off fade measured
  6.6 to 7.2 ms in the renders of 2026-11-10. With the check starting at
  6 ms, four Renoise-made takes failed (BM-DRUM-02 and 10 synth-kit, BM-DRUM-06
  renoise and synth-kit) while Dean passed all four by ear. 8 ms covers both
  engines. A hat that keeps ringing still fails.
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
Its fresh take (REQ-BEN-16) chops a 125 BPM funk break from another pack
instead, which plays only 4% faster at 130 BPM.
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
The snare is a garage one from the catalogue (words `garage`), as in BM-DRUM-03.
- **Why:** tests velocity ramps and phrase-level variation (every 4th bar).
  Dean failed the Renoise and SuperCollider takes of BM-DRUM-03 and
  BM-DRUM-10 because "bass is distorted" (2026-11-10), and passed their
  synth-kit takes. The one catalogue sound those four takes shared that no
  passing benchmark used was the snare. It was IB_Snare_13, from a dubstep
  pack, picked only because its pack's name sorts first among equally good
  snares. 43% of its energy is below 400 Hz (20% below 150 Hz, where the
  kick is), against 0.1% for the clap in the benchmarks Dean passed. The
  kick and closed hat were the same sounds as in BM-DRUM-01, 02 and 06, and
  the open hat as in 02 and 06, all of which he passed. Both patterns are
  garage, so the snare comes from a garage pack, like the kick and hats:
  its layered snare-clap has 1.6% of its energy below 400 Hz.
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
kick (REQ-BEN-12): 12 dB deep, with a 2 ms attack and a 400 ms release.
```
kick  X . . . X . . . X . . . X . . .
clap  . . . . x . . . . . . . x . . .
chh   . . x . . . x . . . x . . . x .
```
Expected envelope on the pad and the acid:
- It ducks 12 dB (±1 dB) within 5 ms of each kick.
- It swells back along a smooth curve, with no step between 0.5 ms
  windows bigger than 0.5 dB.
- It is within 0.5 dB of full level by 412 ms (the end of the release,
  plus 10 ms), before the next kick at 462 ms.
- In the whole mix, from 100 to 200 ms after each kick, it is at least
  2 dB quieter than bypass.

Three more takes go either side of it, each measured the same way against
its own depth, hold and release:
- `exaggerated` is the same duck at 24 dB with a 430 ms release, so the
  pump is impossible to miss. It's Dean's pick for the production sound.
- `over-the-top` goes too far on purpose, to show what ducking does. It
  drops 36 dB, holds there for 150 ms, then swells back over 290 ms, so the
  pad and bass vanish as each kick lands and stay gone for a third of the
  beat.
- `bypass` sets the depth to 0 dB, for A/B.

Hear: the pad and bass pump with the kick. They dip as each kick lands and
swell back up before the next one, and the kick punches through cleanly.
The exaggerated take makes the pump obvious, and over-the-top overdoes it:
the pad and bass drop out under each kick and only swell back later in the
beat. In bypass, the pad and bass hold steady under the kick.
- **Why:** sidechain is the backbone of club mix clarity, and in house the
  pump is a sound in its own right. The measurements check depth, timing
  and that the mix audibly changes, and the takes let Dean hear it from
  none to far too much. Dean failed the first version (6 dB, 200 ms
  release) on 2026-11-11: "Cannot hear a real difference". The mix
  measurement agreed: it changed the mix by about 0.5 dB. Later that day
  he passed all three takes, picked `exaggerated` for production, and
  asked for "one more extreme level, over the top just to give the
  listener a real idea about what side chain compression really is, for
  contrast". Depth and release alone barely go past `exaggerated` within
  one beat, so `over-the-top` holds the dip as well.
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

## North star

The palette of track 1, the north star of Dean's brief: spoken word over
Detroit techno, with a Jersey garage twist (Tony Humphries, Kerri Chandler)
and African drums that mix machines with recordings. Each benchmark tests
one capability the track needs that the benchmarks above don't cover, with
a pattern from that music. The meeting has a precedent: Jeff Mills played a
TR-909 live against Tony Allen's Afrobeat kit on *Tomorrow Comes the
Harvest* (2018).
Every benchmark here gets a take for each piece of software, made the way
that software's users would make it (Dean, 2026-11-11, issue #30).

Grids here may have 12 steps instead of 16: eighth-note triplets, three to a
beat (REQ-BEN-13). Beats fall on steps 1, 4, 7 and 10 of a 12-step grid, and
on 1, 5, 9 and 13 of a 16-step one. The library has no agogo, shekere or
talking drum, and its djembes are all loops (as of 2026-11-11). So the bell,
djembe and dunun voices are SuperCollider synths until one-shots arrive,
for example with `mph catalogue fetch freesound agogo --clear-only`.

Sources: the standard bell pattern
([Bell pattern](https://en.wikipedia.org/wiki/Bell_pattern)); the timing of
Malian drumming (Polak and London,
[Timing and Meter in Mande Drumming from Mali](https://mtosmt.org/issues/mto.14.20.1/mto.14.20.1.polak-london.html),
Music Theory Online 20.1, 2014); the talking drum
([When Music Speaks](https://www.frontiersin.org/articles/10.3389/fcomm.2021.652690/text),
Frontiers in Communication, 2021); parallel chords
([Attack Magazine](https://www.attackmagazine.com/technique/passing-notes/parallel-chords/));
the M1 organ bass
([MusicRadar](https://www.musicradar.com/tuition/tech/how-to-create-a-korg-m1-style-organ-bass-sound-642650));
the Jersey sound
([Gray Area](https://grayarea.co/magazine/garage-house-and-the-new-jersey-sound));
Mills and Allen
([Rolling Stone](https://www.rollingstone.com/music/music-features/producer-jeff-mills-talks-tony-allen-afrobeat-album-731357/)).

### BM-AFRO-01: Bell timeline over the four
122 BPM, straight. The West African standard bell pattern (seven strokes in
twelve triplet pulses) and a triplet shaker, over a four-to-the-floor kick
and offbeat hats.
```
kick   X . . . X . . . X . . . X . . .
chh    . . x . . . x . . . x . . . x .
bell   X . x . x x . x . x . x
shaker X x x X x x X x x X x x
```
The bell is an agogo or gankogui from the catalogue (role `bell`), else a
synth bell. A `flattened` take moves the bell and shaker onto sixteenths:
the bell plays the pattern's usual 4/4 form,
`X . . x . . x x . . x . x . . x`, and the shaker plays every sixteenth.
In Renoise, the song runs at 12 lines per beat (REQ-BEN-13), so every
triplet stroke sits on a line of its own. SuperCollider schedules each
stroke at its exact sample.
Hear: two clocks at once. The kick and hats keep Detroit's straight four,
while the bell's lopsided seven-stroke cycle and the shaker roll in threes
over them. The bell meets the kick only on beats 1 and 4. Its other strokes
fall between, on triplet positions a sixteenth grid can't reach, so the
groove lilts instead of driving. In the flattened take the lilt goes and
the groove squares up.
- **Why:** the bell pattern is the timeline of West African drumming, the
  part every other drum keys to, and it's ternary: three pulses to a beat.
  Techno is binary. Track 1 fuses the two, so the system must hold both in
  one bar without rounding either into the other. BM-DRUM-08's clave lives
  on the sixteenth grid. This is the ternary half.
- **Verify:** measure, listen

### BM-AFRO-02: Jembe feel
122 BPM. A 12/8 ensemble over a straight four-to-the-floor kick:
BM-AFRO-01's bell and shaker, djembe tones and slaps answering each other on
the short pulses, and a dunun that leads into beats 3, 4 and 1. Every triplet voice
plays with the jembe feel: each beat's three pulses last 41%, 31% and 28%
of it (long, short, short), the average Polak and London measured in
Malian drumming (REQ-BEN-13).
```
kick   X . . . X . . . X . . . X . . .
bell   X . x . x x . x . x . x
shaker X x x X x x X x x X x x
tone   . x x . . . . x x . . .
slap   . . . . x x . . . . x x
dunun  . . . . . X . . x . . x
```
Shaker velocities are humanised by ±8% (seeded). Tones and slaps are
strokes of one djembe: catalogue one-shots (role `djembe`, matching "tone"
or "slap"), else the synth djembe with its `stroke` set. The dunun is a
catalogue one-shot (role `dunun`), else a synth. An `even` take plays
the same parts in equal thirds. In Renoise, the feel is in the delay column
of a 12-lines-per-beat song. SuperCollider schedules each stroke at its
exact sample.
Hear: next to BM-AFRO-01's even triplets, the ensemble leans. The first
pulse of each beat is held a little long and the next two hurry in, so the
tones and slaps tumble toward the next kick. It sounds played rather than
programmed, while every kick stays exactly on the grid. In the even take
the parts sit squarely in thirds, and the lean goes.
- **Why:** African drumming's timing isn't even triplets. Measured
  performances split each beat unequally and consistently (Polak and
  London, 2014), and that feel is much of what separates a played ensemble
  from a programmed one. Track 1's organic drums need it, and the kick has
  to stay locked while they lean.
- **Verify:** measure, listen

### BM-AFRO-03: Borrowed groove
122 BPM. A recorded conga loop from the catalogue (role `conga`, a loop
near 122 BPM: in Dean's library, LUS_122_Congas_08) plays over BM-DRUM-01's
kick, with closed hats on every sixteenth and a shaker on the offbeat
eighths. mph finds the loop's onsets and measures how far each sixteenth of
the loop sits from the grid, on average: its groove. It then moves the hats
and shaker by the same amounts. The kick stays on the grid. Renoise has no
groove extraction, so its take writes the borrowed offsets into the delay
column, where Dean can see and edit them. SuperCollider schedules each hit
at its borrowed time. A `grid` take leaves the hats and shaker on the grid.
Measurements: the borrowed groove is reported (an offset in ms for each
sixteenth). Every programmed hit lands within 1 ms of its borrowed position,
and within 3 ms of the loop's own onset on the same sixteenth, where it has
one.
Hear: the hats and shaker sit in the congas' pocket. There are no flams
between the recorded hands and the programmed parts, and the hats pick up
the loop's push and drag. In the `grid` take they flam against the congas.
- **Why:** the brief combines electronic sounds with organic recordings.
  Quantising a recording kills its feel, and leaving the machine on the grid
  makes the two flam. Producers fix this by borrowing the recording's groove
  (MPC and DAW groove templates).
- **Verify:** measure, listen

### BM-AFRO-04: Talking drum answers
A talking drum (the Yorùbá gángan or dùndún) answers each spoken phrase of
BM-VOX-03 with a short figure of rising and falling glides, the way the
drum imitates the tones of Yorùbá speech. Each stroke's glide (its start and
end pitch) is scored. SuperCollider gives the drum voice a pitch envelope.
Renoise uses pitch-slide commands on the note, or the instrument's pitch
envelope. Measurements: each stroke's pitch at its start and end is within
50 cents of the score, and no stroke starts while the voice is speaking.
Hear: the drum seems to talk back. Its glides follow the rise and fall of
the voice, and it never plays over a phrase.
- **Why:** the talking drum is speech made into rhythm, so it is where the
  brief's spoken word and African drums meet. It needs pitch movement within
  a hit, which no benchmark has yet.
- **Verify:** measure, listen

### BM-MIX-01: Layered kick
BM-DRUM-01's kick, with each 909-style kick layered with a djembe bass
stroke (or a dunun). The two are lined up so their low ends add. mph slides
the organic hit, by under 5 ms, to where its low band best correlates with
the kick's, and flips its polarity if that correlation is negative. In
Renoise, both samples sit in one instrument on the same key, the djembe's
sample start offset to align it. In SuperCollider, both buffers play on each
hit. A `raw` take stacks them unaligned.
Measurements: the layered hit's energy below 150 Hz is at least that of the
louder layer alone, so nothing cancels, and the two onsets align within
0.5 ms.
Hear: one kick with skin and wood on top of the 909's punch. It is bigger,
not doubled or flammy. The `raw` take sounds thinner and phasey.
- **Why:** layering is how producers blend organic recordings with
  electronic hits, the brief's core sound. Two low ends out of phase cancel,
  which is easy to get wrong by ear and easy to measure.
- **Verify:** measure, listen

### BM-KEYS-01: Detroit parallel stabs
124 BPM, over BM-DRUM-01. One chord shape (a minor ninth: root, minor
third, fifth, minor seventh and ninth) moved up and down unchanged, the
"chord memory" of Detroit techno and Jersey deep house (Underground
Resistance's "Timeline", Kerri Chandler's parallel minor sevenths in "Bar A
Thym"). Short stabs fall on off-beat sixteenths, and the last stab of every
second bar is thrown into a dotted-eighth delay. Renoise does it the sampler
way: one chord sample (the voicing rendered once by SuperCollider, or a
chord one-shot from the catalogue) played at each root, so higher chords are
also shorter, as on an old sampler. SuperCollider plays every note of the
shape as its own synth voice.
Measurements: every stab lands within 1 ms, and each stab's strongest pitch
classes are the five notes of its chord.
Hear: stabs that hang in the air and shift together, dark and hopeful at
once, with the echo answering at the end of each phrase.
- **Why:** the parallel minor chord is Detroit's harmonic signature, and it
  runs through Jersey deep house too. It tests chords (several notes on one
  step) and transposed samples, which no benchmark has yet.
- **Verify:** measure, listen

### BM-BASS-02: Jersey organ bass
122 BPM, 16th swing 58%, over BM-DRUM-02. The garage organ bass of the
Korg M1's "Organ 2" preset: a triangle-saw blend at the lowest octave, a
square three octaves up and a triangle a fifth above, with a fast filter
envelope and a touch of chorus. It plays a syncopated line with octave
jumps. In Renoise, the organ is sampled once per octave from SuperCollider
and played as a multi-sample instrument. SuperCollider plays the synth.
Measurements: every note lands within 1 ms and within 10 cents of its pitch,
and the low end stays mono (inter-channel correlation above 0.9 below
120 Hz) despite the chorus.
Hear: round, woody and percussive, with the honk of early-90s New Jersey
and New York garage.
- **Why:** the organ bass is the Jersey sound's signature bass and the
  "twist" in the brief. It tests a pitched instrument whose timbre changes
  across its range.
- **Verify:** measure, listen

### BM-VOX-03: Sermon in the gaps
Phrases of a public domain spoken word reading are cut at silences with
BM-VOX-02's pipeline. LibriVox readings, such as Paul Laurence Dunbar's
poems, keep the render `licence:clear`. The phrases are placed on downbeats
in the bars where BM-KEYS-01's stabs rest. While the voice speaks, the music
dips 3 dB, ducked by the voice the way BM-SC-01 ducks by the kick. The last
word of each phrase is thrown into a dotted-eighth dub delay.
Measurements: every phrase starts within 5 ms of its downbeat, the music is
3 dB (±0.5 dB) lower while the voice speaks, and no phrase overlaps a stab.
Hear: the voice preaches over the groove like a sermon over a congregation.
Every word is clear. The band leans back when it speaks and fills the space
when it pauses, and the echo carries the last word into the next bar.
- **Why:** spoken word is the lead of track 1, and the library has none.
  Call and response between preacher and congregation runs through gospel,
  where the Jersey sound comes from. This builds on BM-VOX-02 (download,
  separation, phrases) and BM-SC-01 (ducking).
- **Verify:** measure, listen

## Delivery and professional QA

### BM-MST-01: Mastered delivery
A 2-minute mini-arrangement of BM-DRUM-02, BM-BASS-01, BM-SC-01 and BM-VOX-01,
mastered and delivered. BM-SC-01's part ducks as its `exaggerated` take
does (24 dB, 430 ms release), Dean's pick for the production sound.
Measurements:
- integrated loudness within ±0.5 LU of `master.target_lufs_integrated` (default -8 LUFS),
- true peak at or below `master.max_true_peak_dbtp` (default -1.0 dBTP, 4x oversampled),
- no clipped samples, DC offset under -60 dBFS,
- low end mono: inter-channel correlation above 0.9 below 120 Hz,
- file is 48 kHz / 24-bit AIFF and lead-in is 10 ms or less (REQ-AUD-06),
- loudness range (LRA) reported, not gated.
Hear: loud and punchy but not squashed. The kick still has transient snap,
and nothing distorts on a big system.
- **Why:** club systems punish phasey sub and clipped masters. These are the
  numbers mastering engineers check. Dean chose the exaggerated duck for
  production on 2026-11-11.
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
