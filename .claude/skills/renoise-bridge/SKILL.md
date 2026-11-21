---
name: renoise-bridge
description: Change or debug mph's Renoise bridge (the song plan, the mixer plan and the Lua tool that builds, renders and saves songs in Renoise). Covers where things are, the fake Renoise, checking in real Renoise, what Renoise 3.5.4 really calls and shows, and the traps in its API.
---

# Change the Renoise bridge

The bridge turns a score into a Renoise song (spec/components/renoise.md,
REQ-REN-03 and REQ-REN-08 to REQ-REN-12):

- `src/mph/renoise/plan.py` writes the song plan: patterns, notes, sampled
  synths, what to render and where to save.
- `src/mph/renoise/mixplan.py` writes its mixer: devices, send tracks,
  automation and the duck.
- The Lua tool in `src/mph/renoise/tool/com.mph.bridge.xrnx/` builds,
  renders and saves the song inside Renoise: `build.lua` (tracks, notes,
  instruments), `mixer.lua` (devices, automation, render modes), `main.lua`
  (jobs, idle steps, saving).

## The loop

1. Spec first: the requirement and its Why in `spec/components/renoise.md`.
2. Change the plan and the Lua together. The tests run the real Lua under
   Lua 5.1 against `tests/renoise/fake_renoise.lua` (marked `lua`):
   `test_mixplan.py` checks the plan, `test_mixer.py`, `test_plan.py` and
   `test_bridge.py` what the tool builds (through `tests/renoise/harness.lua`),
   and `tests/test_renoise_check.py` the real-Renoise check.
3. The fake is a model of Renoise 3.5.4: its devices are named, ranged and
   shown as the real ones are (below). Change it only to match what real
   Renoise reported, never just to make a test pass.
4. Push, then run `scripts/renoise-check REF` on Dean's Mac with Renoise
   closed (the mac-work skill). It builds three small songs in real Renoise.
   The third has a mix and sampled synths, and the check fails if a planned
   setting doesn't show what was asked, the duck fails REQ-BEN-12's checks, a
   #Send mutes its source or an echo comes back late. Its report lists every
   parameter of every kind of device as Renoise names and shows it, and the
   settings in their preset data. When it fails, fix the plan's names and
   units from that report, and the fake to match. Ask for its output word for
   word: a summary loses the names.
5. A render needs the installed tool to match the code that renders. Close
   Renoise, run `uv run mph renoise install` from that worktree, render, then
   put back the tool Dean's checkout uses.
6. A new file in the tool's folder goes on `bridge.TOOL_FILES`, the list
   `mph renoise install` copies, and a new Lua file also goes on
   `rerender.CODE`, so it counts as a change to the renders (REQ-BEN-14).
   `test_bridge.py` checks both. A file left off the first list stopped
   Renoise loading the tool at all (2026-11-21).

## What Renoise 3.5.4 calls and shows

From renoise-check on Dean's Mac (2026-11-20). Values in quotes are as
Renoise shows them.

- **Digital Filter**: Type ("LowPass" to "HighPass"), Cutoff in kHz to two
  places ("0.03 kHz"), Resonance in % (default 12.5), then "(void)" and
  Inertia. Its model isn't a parameter: it's `<Model>` in the preset data,
  Biquad by default (12 dB an octave); the manual's others are Butterworth
  4n and 8n and Chebyshev 4n and 8n. Renoise takes a new model from the
  preset data, and Butterworth has no resonance: its Resonance knob turns
  into "(void)". So plain filters are Butterworth 4n and resonant ones stay
  Biquad.
- **Gainer**: Gain from "-INF dB" to "12.041 dB" (a linear 0 to 4
  underneath), Panning ("50 L", "Center", "50 R"). By default it glides to
  each new gain, to avoid clicks; `SmoothParameterChanges` in its preset
  data, kept out of its panel, turns that off (Renoise's developers on
  their forum, 2022). The Duck has it off.
- **#Send**: Amount (dB), Panning, Receiver (the send track's number, from
  0). Keeping the source is `<MuteSource>false</MuteSource>` in the preset
  data, which is also the default; `SmoothParameterChanges` and
  `ApplyPostVolume` sit beside it.
- **Multitap**: "Mute Dry" (On means fully wet), then for each tap T1 to T4:
  "Dly Left" and "Dly Right" (ms), "Amount", "L Feedback" and "R Feedback"
  (%), "Ping Pong", "Flt Mode" ("Off", "In + Feed", "Output" and others),
  "Flt Type", "Flt Freq." (kHz), "Line Sync", and with line sync on, the
  time in lines is "L Sync Time" and "R Sync Time". "Sync L DelayTime" is a
  pattern command's target that always reads -1. Only T1 is loud by default.
- **mpReverb 2**: Duration and Predelay (ms), Wet Mix and Dry Mix (%). It
  has no damping.
- **AHDSR** (an instrument's volume modulation): Attack, Hold, Decay and
  Release from 0 ms to 60 s, Sustain 0 to 1.
- **Automation timing**: Renoise reads automation once a tick (16 ticks a
  line in mph's songs, Renoise's most). With the Gainer's smoothing on, the
  remix's 24 dB, 2 ms duck, drawn 20 ms early, fell over about 25 ms and
  never reached its depth (2026-11-20). With it off, the Duck steps once a
  tick: it's drawn one tick early, and a song with a duck gets enough lines
  a beat that its release steps at most 0.35 dB a tick (`plan.lpb`,
  `mixplan.release_step_db`, REQ-REN-11). renoise-check prints the duck's
  real shape around a kick on a steady tone ("the duck on the steady tone,
  around a kick (ms: dB): ..."), how many samples apart each ducked render
  and its dry render are ("... stem runs N samples behind ... dry"), and
  each part's first 2 ms. Read the shape before changing the curve. A note
  that starts from silence with the song can read strangely in its first
  half-millisecond: check the dry level there before blaming the duck.
- **Overlapping dips**: where a kick comes before the last dip has come
  back (the remix's kick pushed onto the last eighth), the deeper dip wins
  in Renoise. Added up, as SuperCollider does, the two held the depth for
  90 ms past the next kick and then came back about five times as steeply
  as one release, which Renoise's ticks turned into 0.8 dB steps; the
  remix's four blocks failed REQ-BEN-12's steps (2026-11-20). A take's
  "biggest step on the way back" check says where its step is, the level
  either side and the dry part's own level there: a big change in the dry
  level means a note starting, not the duck. A fall just before a hit is
  that hit's own dip, drawn a tick early, so the check ends each way back
  at its highest point (REQ-BEN-12). Before it did, the remix's takes
  failed at 3.7 dB, half a millisecond before each kick that came 250 ms
  after the last (2026-11-21).

## Traps in the API

- Give values as Renoise shows them (Hz, dB, ms, %, lines). The tool finds
  the raw value from a table of what the parameter shows across its range,
  so Renoise's own scales needn't be known. Read a number only in its own
  unit: "0.000 %" read as a level would have silenced the reverb.
- A setting that isn't a parameter lives in `device.active_preset_data`, an
  XML string. Rewrite its tag, assign the whole string back, then read it
  again to see whether Renoise took it. Set preset data before parameters:
  loading it can change them.
- `insert_track_at` at the master's index makes a send track. Send tracks
  come after the master.
- A track's first device is its mixer and can't be deleted. Planned devices
  go after it, so their places in the plan count from 2 in Renoise.
- Automation points sit on 1/256 of a line, with values from 0 to 1 of the
  parameter's range; use the "lines" play mode. Each pattern holds its own
  automation, so a move that crosses patterns needs a point at each
  pattern's start and end. If Renoise refuses points after a pattern's last
  line starts, the tool keeps them to that line, and renoise-check says so.
- The first render of a freshly built song starts about 30 samples late, so
  render a throwaway warm-up first and measure only the renders after it.
- The first render after switching render modes can start quieter on a track
  whose devices were just switched off: the bass's stem, rendered straight
  after the mix, came out about 6 dB quieter in its first 0.6 ms
  (2026-11-20). A take renders the mix, then the stems in the score's order
  (the kick's first in every score so far), then the dry renders; keep
  renoise-check in that order, and don't make a measured part the first
  render after a switch.
- Renoise asks whether to stop a script that holds it for too long ("may
  be busy or may have stopped responding"), and the render then waits for
  a click. So the build runs in a coroutine (`main.lua`) and calls
  `slice.pause()`, which yields once a slice has run 0.2 s (REQ-REN-03). A
  new loop in the build should call it too, but never inside a `pcall`:
  Lua 5.1 can't yield across one. The harness's `slice=0` makes the build
  pause at every chance.
- Save each song in an idle step of its own, after the step that last
  changed it (REQ-REN-08). Otherwise Renoise asks whether to save, and a
  render waits for a click nobody makes.
- Melodic parts need 8 lines a beat, so a note's gate (its note-off) lands
  where SuperCollider's does; triplet grids need 12; a song with a steep
  duck doubles that, up to what a four-bar pattern of 512 lines holds.
- A new song's track headroom is -6 dB: set it to 0 dB (REQ-REN-12), or
  every Renoise take comes out 6 dB quieter than its SuperCollider take.

## Dean's rule for takes

Every benchmark gets a take per software, even one not used in practice,
because the takes are system tests (2026-11-11). Build each the way that
software's users would: in Renoise, send tracks, native devices and
automation drawn in the patterns, where Dean can see and edit it. Where
Renoise lacks something SuperCollider does, reach the same intent the way
Renoise users do, rather than copying SuperCollider's method.
