# Tracks

**Status:** draft (arranged scores for the Love Train remixes, 2026-11-14;
stems' tempo, a track's rate and cues inside sections for remix 1, version 2)

A track is a long score in a project of its own, such as a remix. It is the
same TOML as a benchmark (REQ-BEN-04), with three additions. Sections say
which voices play in which bars. A clip voice plays whole bars of a
recording, such as a full track or one of its stems (REQ-CAT-10,
REQ-CAT-11). And an excerpt is a block of a track's bars that renders and is
rated on its own, so Dean can judge a track in pieces before it is finished.

Tracks render the way benchmarks do (REQ-BEN-06, REQ-BEN-10): a Renoise song
or a SuperCollider pass for each take, stems, the checks, and the 48 kHz /
24-bit delivery. Renoise gets time in proportion to the audio it renders, so
an 8-minute track has as long as it needs. Their entries live in `spec/tracks/`, one file per source
track and a heading per project, written like benchmarks: what to hear, why,
and how it is verified.

### REQ-TRK-01: Sections
A score can be arranged in `sections`, each with a name, its first and last
bar, and the voices that `play` in it:
`{ name = "Departure", bars = [29, 32], play = ["source:train"] }`. Sections
follow each other from bar 1 to the score's last bar, with no gaps and no
overlaps, and a section may play nothing. A drum or melodic voice plays its
pattern in the bars of the sections that list it and rests in the others.
Its pattern runs on underneath, so when the voice comes back it plays what
it would have played there, with the same velocities and humanising. A score
with no sections plays every voice in every bar, as before, and keeps its
hash.
- **Why:** an 8-minute remix is built from sections (Dean, 2026-11-13: "a
  Renoise project file per remix", "about 8 minutes"), and voices come and go
  between them: remix 1's DJ intro is kick and hats only, and every new part
  stops for the train passage. A benchmark plays every voice in every bar,
  which can't say that.
- **Verify:** test

### REQ-TRK-02: Clips of a recording
A clip voice plays bars of one recording, pinned by its hash (REQ-BEN-16), and
names them in `clips`: `clips = { train = [9, 12], groove = [13, 28] }` names
the recording's bars 9 to 12 and 13 to 28. The recording's bar 1 starts at
its first sample, unless the voice says where it starts (`bar_one_s`). A
section cues a clip by naming it after the voice (`"source:train"`), and the
clip starts on the section's first bar. A section that lists the voice on its
own lets the clip that is playing carry on. A clip stops at its own last bar,
when a section cues another clip, when a section leaves the voice out, or at
the end of the score. Nothing else stops it: no voice can choke a clip voice.

A clip is never stretched or pitched. The recording plays at its own speed, so
the score must be at the recording's tempo as the catalogue measured it
(REQ-CAT-10), to within 0.01 BPM, or the render stops and says why. A clip
plays at the recording's own level, plus the voice's `gain_db`, with none of
the level matching that kit sounds get (REQ-BEN-09). A clip voice needs its
recording: it never searches the catalogue for another sound or falls back
to a synth. So a score with clips gets no `synth-kit` take (REQ-BEN-07).

In Renoise, each clip is cut from the recording, sample for sample at its own
rate, into a sample of its own in the voice's instrument, on its own key from
C-4 up. So the pattern shows one note for each cue. Autoseek is on, so
starting playback in the middle of a clip plays it from the right place, and
Autofade keeps the cuts free of clicks. In SuperCollider, a cue plays the
recording from the clip's first frame.

Every render checks each cue against the recording, near its start and near
its end: at the loudest half second in the cue's first quarter and in its
last quarter, so a vocal line that starts on beat 2 is checked where it
sings, not in the silence before it. The clip's stem must line up with the
recording's bars to within 1 ms, and cancel against them to -20 dB or
lower. So a clip that starts late, drifts, plays the wrong bars or plays at
another speed fails.
- **Why:** a remix is built from the source's own bars (2026-11-13: "take a
  few loops, stem separate them, infuse it with other sounds"). The research
  for remix 1 recommends a re-edit built from source bars before anything
  else, to test cutting, long samples, rendering and delivery. At 120 BPM
  every bar of Love Train starts on an exact 2-second line, so whole bars cut
  cleanly and nothing needs stretching. Cutting each clip once, without
  converting it, follows REQ-AUD-01 and REQ-AUD-02. Renoise's sample offset
  effect reaches only 256 points in a sample, 1.3 s apart in Love Train, and
  slicing every bar would need a note in every bar. Checking against the
  recording itself, not a template cut the same way, catches a wrong bar as
  well as a late one.
- **Verify:** test, measure

### REQ-TRK-03: Excerpts
A score file can be an excerpt of a track: `excerpt = { of = "BM-LTDJ-05",
bars = [25, 48] }`, with its own id, title, project and "Hear:" text. It plays
exactly those bars of the track, with the same voices, sounds and takes. Its
sections are the track's, cut to its bars. Each pattern plays what the track
plays there. A clip already playing at its first bar is cued again from the
bar it has reached. It keeps only the clips it plays, so its Renoise song
holds just those. It renders, is measured and is rated like any benchmark,
as a song of its own. The track's sections and clips outside its bars don't
shape its hash, so changing them doesn't make it render again.
- **Why:** Dean asked for "some small sections for early feedback, then the
  final track separate" (2026-11-13). An excerpt is cut from the track's score
  rather than copied, so what Dean rates is what the track plays there, and a
  change to those bars of the track reaches the excerpt. A block of 24 to 32
  bars lasts about a minute, like a benchmark, and its Renoise song is small
  enough to open and work on.
- **Verify:** test

### REQ-TRK-04: Sections in Renoise
In a track's Renoise song, every section starts a new pattern, and the
pattern sequencer shows the section's name there. A section longer than 4
bars runs over patterns of up to 4 bars. A clip that stops before its own
last bar gets a note-off where it stops.
- **Why:** Dean works on these songs by hand (2026-11-08: "individual
  human-editable tracks"). Named sections in the sequencer are how Renoise
  users find their way around an arrangement, and they let Dean move, copy
  or repeat a section as one block.
- **Verify:** test; inspect: open a track's song in Renoise

### REQ-TRK-05: Stems keep their recording's tempo
A stem separated from a recording (REQ-CAT-11) plays at the recording's
speed, so a clip voice that pins a stem places its clips by the tempo the
catalogue measured for the recording it came from, when the stem has no
tempo of its own. Bar 1 is where it is in the recording.
- **Why:** remix 1's second version plays the vocals and the train's horn
  from their stems, not from the mix. Demucs keeps every sample in place,
  so a stem has its recording's grid, but the catalogue measures tempo for
  sources, not for derivatives.
- **Verify:** test

### REQ-TRK-06: A track's sample rate
A track can render at a rate of its own, `rate = 48000`, in place of the
production rate (REQ-AUD-02), but never below the delivery rate. Its stems,
master and checks use that rate, and its delivery needs no conversion.
- **Why:** an 8-minute track with 14 voices writes about 2.6 GB of stems at
  48 kHz and twice that at 96 kHz, and Dean's Mac had about 9 GB free
  (2026-11-14). The new synths are band-limited (REQ-AUD-04), so they don't
  need the higher rate to keep aliasing down, and the clips play from
  recordings at 44.1 kHz.
- **Verify:** test

### REQ-TRK-07: Cues inside a section
A clip voice can cue clips at the start of any bar, not only where a
section starts: `cues = [[40, "verse-1"], [44, "verse-2"]]`. The section
holding that bar must list the voice, and nothing else may cue it in the
same bar. A clip cued this way stops like any other: at its last bar, at
the next cue, in a section that leaves the voice out, or at the end. A
section can list the voice on its own while nothing plays, as long as one
of the voice's cues falls inside it. An excerpt keeps the cues in its bars.
- **Why:** a singer starts a line where it fits the song, not where the
  arrangement changes. Love Train's lines start on beat 2 of even bars, as
  pickups into phrases that start on odd bars. Cueing them inside sections
  keeps the sections, and the Renoise patterns made from them
  (REQ-TRK-04), lined up with the phrases.
- **Verify:** test
