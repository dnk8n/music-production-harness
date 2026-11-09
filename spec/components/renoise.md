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
other tracks muted for a stem), then writes a result file with any error. The first render of a freshly
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
page's own origin, like a verdict (REQ-FBK-05).
- **Why:** a plain web link can't open an app, but the page is served by mph
  on Dean's machine, so mph can open it for him.
- **Verify:** test
