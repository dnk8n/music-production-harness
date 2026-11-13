# Renoise bridge

**Status:** draft (benchmark songs built in step 3, the track template in
step 4). Target: Renoise 3.5.4 arm64 (licensed), Lua API 6. Decision: ADR-0009.

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
throwaway warm-up render first and measures only the renders after it.
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
Every track also has a filter device that starts bypassed. Notes
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
