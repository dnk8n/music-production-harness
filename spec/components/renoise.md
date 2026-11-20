# Renoise bridge

**Status:** draft (benchmark songs built in step 3, the track template in
step 4; synths, the mixer and ducking for remix 1's second version,
2026-11-15). Target: Renoise 3.5.4 arm64 (licensed), Lua API 6. Decision:
ADR-0009.

### REQ-REN-01: Songs are generated, not hand-built
Python turns score data into a song plan: tempo, lines per beat, tracks,
instruments (samples, slices, tuning), notes with volume and delay, track
volumes, and the renders wanted. The bridge tool builds the song from that
plan through Renoise's Lua API. A Renoise song is a build output, and its
sources are the score and the spec.
- **Why:** regenerable from spec (ADR-0001). Building through the API, not
  hand-written XML, lets Renoise itself guarantee the song is valid. Hand edits
  in Renoise are welcome, but get captured back as score changes or the
  next render replaces them.
- **Verify:** test

### REQ-REN-02: Generated songs open cleanly
Every generated song is saved with its samples inside, opens in Renoise 3.5.x
without warnings, and plays the same as the render made from it.
- **Why:** the song Dean opens must be the one that was measured.
- **Verify:** test

### REQ-REN-03: Scripted render through a job folder
A small Renoise tool (Lua, shipped in `src/mph/renoise/tool/`) watches a job
folder under `data_dir/renoise/jobs/`. mph writes a job (the plan, as a Lua
data table), and the tool starts a new song, builds it, saves the `.xrns`,
renders each requested WAV (32-bit float at the production rate, with
other tracks muted for a stem), saves it again, then writes a result file
with any error. It leaves nothing unsaved behind it (REQ-REN-08). The first render of a freshly
built song starts about 30 samples late in Renoise 3.5.4, so mph asks for a
throwaway warm-up render first and measures only the renders after it. A
song with a mix (REQ-REN-10) renders its stems dry: each stem with the
strip devices, the sends and the master's devices switched off and its duck
on, and each sidechain target once more with its duck off too (REQ-REN-11).
The song is saved with nothing muted and every device as built. If a
device or parameter the plan names isn't there, or never shows the value
asked for, the build fails naming all of them, with the parameters the
device does have. The result file lists the parameters of every kind of
mixer device the song uses, and what each of the song's mixer devices shows
once built, with the optional settings it doesn't have and the other
settings in its preset data. mph keeps it in the render folder, so a
parameter Renoise names or shows differently comes to light in one run.
mph deletes each rendered WAV once it has read it.
mph starts Renoise in the background and waits for the result. If Renoise is
already open when a render command starts, mph refuses and asks the user to
save their work and quit Renoise: it only renders in a Renoise it started
itself, from a fresh song, so a song someone is working on is never touched
and an open session can't change the render. Later renders in the same
command reuse the Renoise mph started. Nobody needs to click anything. `mph renoise install` copies the tool
into Renoise's Tools folder, and doctor checks that it's there.
- **Why:** Renoise has no command-line render. A job folder needs no network
  port or preference changes, survives a Renoise restart, and leaves a
  readable trail when something fails.
- **Verify:** test

### REQ-REN-04: Laptop keyboard performance
The template maps laptop keys to macros (filter, FX sends, mutes, beat
repeat) using Renoise's MIDI/keyboard mapping. The mapping table is data,
so MIDI controllers can be added later without code changes.
- **Why:** live and hybrid performance from day one, controllers later.
- **Verify:** listen

### REQ-REN-05: Free plug-ins first
The template uses Renoise native DSP plus free plug-ins (Surge XT first).
Plug-ins are listed in config, and doctor checks they're installed.
- **Why:** free first, and a render must never silently miss a plug-in.
- **Verify:** test

### REQ-REN-06: Tracks ready for hands-on mixing
Each sound's level-matching gain (REQ-BEN-09) is set on its sample's
volume, so every track's own volume starts at 0 dB, free for mixing by hand.
Every track also has a filter device that starts bypassed, unless its
voice's strip already filters it (REQ-REN-10). Notes
use the volume and delay columns, so velocity and swing are visible and
editable in the pattern. Choked voices get a note-off.
- **Why:** Dean should be able to tame a sound, or move a note, without
  adding anything first.
- **Verify:** test

### REQ-REN-07: Open a render's song in Renoise
The Benchmarks page has an "Open in Renoise" button for each render that has
a song. The local server opens that render's `.xrns` (only one listed in its
`report.json`) with Renoise. It accepts the request only as JSON from the
page's own origin, like a rating (REQ-FBK-05).
- **Why:** a plain web link can't open an app, but the page is served by mph
  on Dean's machine, so mph can open it for him.
- **Verify:** test

### REQ-REN-08: Nothing left to save
Renoise never has to ask whether to save a song mph built, so a render never
waits for a click. The bridge tool saves each song for the last time in an
idle step of its own, after the step that last changed it, and starts the
next song only after that save. After a job's last song, the tool saves the
open song once more as mph's scratch song,
`data_dir/renoise/scratch/mph-scratch.xrns`, and only then writes the
result. So the song Renoise is left with is saved, and it is still on disk
after the render's `work/` folder goes (REQ-RET-03).

Before a job starts its first song, the tool saves the song that's open if
it is mph's. A song the tool built itself is saved as the scratch song. A
song opened from mph's data folder, such as a render's song opened with
"Open in Renoise" (REQ-REN-07), is saved as a copy,
`data_dir/renoise/scratch/<UTC time> <name>.xrns`. Changes made to it by hand
are kept there, the render's own `.xrns` stays as it was measured
(REQ-REN-02), and mph says where the copy is. mph never saves a song that
isn't its own: Renoise asks about that one as it always has, because the
question protects someone's work. `mph prune` lists scratch songs once
they are an hour old (REQ-RET-05).
- **Why:** Renoise asks to save a changed song before it closes it: when mph
  starts its next song, when "Open in Renoise" loads another song, and when
  Renoise quits. The question holds up a render until someone clicks, and
  made quitting Renoise a chore (Dean, 2026-11-13). The tool did save each
  song after its renders, yet Renoise still asked: that save ran in the same
  step as the song's last change, and the song left open was the measuring
  song in `work/`, which is deleted when the render finishes. Scratch songs
  are throwaway, like any hand edit to a song mph made (REQ-REN-01). They
  are called scratch songs because a stash in mph is a sound Dean chose to
  keep.
- **Verify:** test, with a fake Renoise that counts a step's changes as unsaved even after a save in that step, and asks before closing a changed song; inspect on the Mac: two renders in one Renoise, then Renoise quits without asking

### REQ-REN-09: Synth parts as sampled instruments
A melodic voice (REQ-BEN-17, REQ-BEN-18) plays from a Renoise instrument
sampled from its SuperCollider synth, the way a sampler holds a synth patch:
- SuperCollider renders one sample for each sound the voice makes: each
  pitch it plays, plain and accented, with a chord sampled whole at its root
  (chord memory). Each is held for the longest note of its pitch (notes
  tied together count as one) plus the synth's release, at the production
  rate, full velocity and unit gain.
- Each sample sits on its own key, and the keys between sampled notes play
  the nearest one transposed, so any note Dean types sounds. A voice with
  accents has a second instrument, named after it with "accent", that holds
  its accented samples: an accented note plays that instrument. Velocity
  stays in the volume column for both.
- An AHDSR on volume ends notes as the synth does: a note-off at the end of
  a note's gate releases it over the synth's release time, and a new note
  in the column releases the one before it.
- A song with a melodic voice runs at 8 lines per beat, so a line is half
  a sixteenth and a plain note's gate (half a step, BM-BASS-01's rule) ends
  on a line of its own, where its note-off goes. A note and its note-off
  can't share a line. A triplet song's 12 lines per beat already allow it
  (REQ-BEN-13). A song with a duck may run at a multiple of these
  (REQ-REN-11).
- A tied note (a slide) doesn't start a new note: the next note carries a
  glide command (0Gxx) in the effect column, fast enough to cover the
  interval in the synth's glide time, and the sample that is playing bends
  to the new pitch.
- Each sample's volume holds its voice's level-matching gain (REQ-BEN-09,
  REQ-REN-06).
- **Why:** Dean's rule (2026-11-11): each take is built the way its
  software's users would build it. Renoise users play synth sounds from
  sampled instruments, which Renoise makes itself from a plug-in ("render
  plug-in to instrument"), and Detroit's chord memory is a chord sampled
  once and played from one key (#40). The notes stay notes, so Dean can
  move, re-pitch and lengthen them in the pattern. A sampler bends a glided
  note's sample rather than keep the synth running, and a glided note keeps
  the first note's accent. A native Renoise patch for the acid line is
  #30's job. At 4 lines per beat a plain note's note-off would fall on
  its own note's line and be lost, so every note would ring on until the
  next one; Renoise users raise the lines per beat for short gates.
- **Verify:** test, with the fake Renoise; measure, with the checks the
  SuperCollider take passes

### REQ-REN-10: The mix as a Renoise mixer
A score's mix (spec/components/mix.md) is built from Renoise's own devices:
- On each voice's track, after its duck (REQ-REN-11): a Digital Filter for
  each strip filter it uses (high-pass, then low-pass), a Gainer for the
  strip's level and pan, then a #Send for each send, routed to that send's
  track and keeping the source. A plain filter is Butterworth 4n, 24 dB an
  octave like the SuperCollider strip's filters. A resonant one keeps
  Renoise's Biquad, the model with a resonance knob, turned the strip's
  share of the way.
- Each send is a send track after the master, holding its effect, fully
  wet, then its return strip: the same filters, Gainer and #Sends, to sends
  after it. A delay is a Multitap's first tap with its dry signal muted,
  line-synced to the echo time, with the feedback, ping-pong when asked,
  and its low-pass on the input and in the feedback path, so the first echo
  is filtered once and each repeat once more. A reverb is mpReverb 2, all
  wet, with the decay as its duration and the predelay.
- The master's strip is on the master track.
- Automation (REQ-MIX-03) becomes automation in the patterns, on the
  parameter each lane moves: a point at each of the lane's points and one
  every beat along a move, so filters move evenly in octaves and levels in
  dB whatever scale Renoise gives the parameter. A jump is two points 1/256
  of a line apart. Each pattern starts and ends on the lane's value there.
- Values are given as Renoise shows them (Hz, dB, ms, %, lines). The bridge
  finds the value that shows each one, from a table of what each
  parameter shows across its range, so Renoise's own scales needn't be
  known. A number is read only in its own unit, so a level is never taken
  from a percentage. A setting that isn't a parameter, such as a #Send's
  mode or a filter's model, is set in the device's preset data, by its XML
  tag. An optional setting may be missing, and an optional preset setting
  may keep Renoise's own value if Renoise won't take the one asked for; the
  result file shows what it kept.
- **Why:** Dean's rule (2026-11-11). Renoise users mix with send tracks,
  native devices and automation drawn in the patterns, where Dean can see
  and change it. The Multitap is Renoise's echo with ping-pong and a filter
  in its feedback path, so each echo is darker than the one before, as in
  the SuperCollider take. Renoise doesn't document its parameters' scales,
  and what it shows is the unit the score is written in. The first run in
  real Renoise (3.5.4, 2026-11-20) found what the names had guessed wrong:
  the Multitap's dry switch is "Mute Dry"; with line sync on, a tap's time
  in lines is its "Sync Time", while its "Sync DelayTime" is a pattern
  command's target that always reads -1; feedback, resonance and the
  reverb's wet and dry mix are shown in %; the #Send's "keep source" mode
  isn't a parameter, and nor is the filter's model, which is in its preset
  data (Biquad, 12 dB an octave, unless set); and the reverb has no damping,
  so it keeps Renoise's own. Read as dB, the reverb's "0.000 %" would have
  silenced the hall without an error. Switched to Butterworth 4n, the filter
  took the model and blanked its resonance knob to "(void)": Butterworth
  has no resonance, so a resonant filter stays Biquad, as a Renoise user's
  would.
- **Verify:** test, with the fake Renoise; inspect on the Mac with
  `scripts/renoise-check`; listen

### REQ-REN-11: The duck as Gainer automation
A sidechain (REQ-BEN-12) is a Gainer named Duck, first on each target's
track, whose gain is automated at every trigger hit: down to the depth
over the attack, held for the hold, and back along the release's
half-cosine curve, with a point at least every 32nd of the release. Dips
that overlap add up but never pass the depth, as in SuperCollider
(REQ-BEN-12). The Duck's smoothing is off (`SmoothParameterChanges` in its
preset data), so it steps to each new gain at the tick that reads it. The
whole curve is drawn one tick early, so its first step down lands on the
hit's own tick, and the Duck's own gain starts where its automation does,
so a hit on the song's first line doesn't step down. Each point is placed on the
nearest 1/256 of a line with the gain the curve has at its own time, so the
floor of a 2 ms attack isn't missed. Every song runs at 16 ticks a line,
Renoise's most. A song with a duck doubles its lines per beat until the
release moves at most 0.35 dB in a tick, as long as a four-bar pattern
still fits in Renoise's 512 lines: at 120 BPM, the remix's duck (24 dB,
back over 430 ms) gets 16 lines a beat, where a tick is about 2 ms.
- **Why:** Renoise has no sidechain that follows the pattern, and an
  automated Gainer is how Renoise users draw an exact pump (#30). Dean can
  see and edit the curve in the automation editor. Renoise reads automation
  once a tick, and by default its Gainer glides to each new value, to avoid
  clicks. In real Renoise (3.5.4, 2026-11-20), the remix's duck (24 dB in
  2 ms), drawn about 20 ms early on a steady tone, began to fall 15 ms
  before the kick, was 19.7 dB down at the kick and 22.3 dB down 10 ms
  after it, and never reached its depth, where REQ-BEN-12 wants the full
  depth within 5 ms. Renoise's developers say the Gainer's gliding is a
  setting kept out of its panel, `SmoothParameterChanges`, and that with
  it off the delay is gone (Renoise forum, 2022). Off, the Gainer steps
  once a tick instead, so the ticks must be short: at 8 lines a beat and
  120 BPM a tick is 3.9 ms, and the remix's release would step up to
  0.46 dB at a time, next to REQ-BEN-12's limit of 0.5 dB. Twice the lines
  halves that, and Renoise users raise the lines per beat for finer
  automation. Drawn one tick early, a dip's first step down lands on the
  kick's tick, as SuperCollider's dip starts on the kick.
- **Verify:** test; measure, with REQ-BEN-12's checks in `scripts/renoise-check`
  and on the Renoise take

### REQ-REN-12: Track headroom at 0 dB
Every song the bridge builds sets Renoise's global track headroom to 0 dB,
so a take's levels are the ones REQ-BEN-09 set.
- **Why:** a new song's headroom is -6 dB, so every Renoise take came out
  6 dB quieter than the SuperCollider take of the same score (#35).
- **Verify:** test
