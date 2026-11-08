# Renoise bridge

**Status:** draft (built in step 4). Target: Renoise 3.5.4 arm64 (licensed).

### REQ-REN-01: Songs are generated, not hand-built
Python writes `.xrns` files (a zip with `Song.xml` plus sample files) from
score data: tracks, patterns, instruments (samples and plug-ins), send
tracks, automation and mappings. A Renoise song is a build output, and its
sources are the score and the spec.
- **Why:** regenerable from spec. Hand edits in Renoise are allowed, but get
  captured back as score changes or they will be overwritten.
- **Verify:** test

### REQ-REN-02: Generated songs open cleanly
Every generated song opens in Renoise 3.5.x without warnings, and
round-trips (open, save, diff `Song.xml`) with only cosmetic changes.
- **Why:** catches schema drift early.
- **Verify:** test

### REQ-REN-03: Scripted render
A small Renoise tool (Lua, shipped in `renoise/tool/`) accepts render
commands over OSC: open song, render to path with given rate and bit depth,
then report done. The window can be open but needs no interaction.
- **Why:** Renoise has no command-line render. Lua plus OSC is the supported
  automation path, which makes Renoise the last tool in the chain.
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
