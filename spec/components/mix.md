# Mix

**Status:** draft (for Love Train remix 1, version 2, 2026-11-14)

A score can mix its voices the way a desk does: each voice goes through a
channel strip, some of it is sent to shared effects, and the controls move
bar by bar. A score without a mix renders as before, every voice straight
into its stem, and keeps its hash.

The mix is written in the score, next to the parts. A voice's strip is a
`mix` table on the voice, the shared effects are `sends`, and the master has
a strip of its own. Moves are `automate` tables, a list of points for each
control they move:

```toml
[voices.stabs.mix]
highpass = 200
lowpass = 1200
send = { echo = -8, hall = -14 }

[voices.stabs.automate]
lowpass = [[37, 1200], [53, 3000]]  # opens over groove A

[sends.echo]
kind = "delay"
beats = 0.75
feedback = 0.5
lowpass = 2500
highpass = 250
send = { hall = -12 }

[sends.hall]
kind = "reverb"
decay_s = 3.2
damping = 5000
predelay_ms = 25
highpass = 300
```

### REQ-MIX-01: Channel strips
A voice's `mix` table sets its strip: a high-pass and a low-pass filter, each
24 dB per octave and off unless set (`highpass`, `lowpass`, in Hz), the
low-pass filter's `resonance` (0 to 1), a `level` in dB (0 unless set), a
`pan` from -1 (left) to 1 (right), and what it sends to each of the score's
sends (`send`, in dB). Pan is a balance: the centre leaves both channels as
they are, and turning it towards one side turns the other side down. Sends
take the strip's output, after its filters, level and pan.

The strip takes its voice after the sidechain (REQ-BEN-12), so the voice's
sends dip with it. The master strip (`[master]`) takes the sum of every
strip and every send's return, and has the same filters and level. The
render's master is what comes out of it.
- **Why:** the remix's sound design lives in the mix, as it does in a DAW:
  the stabs reach the echo, the vocals reach the hall, and a part is
  filtered to make room for the others. Dean asked for "sophisticated sound
  design" in his ratings of remix 1's first version (ratings 226 to 234).
  Pan as a balance keeps a centred strip at exactly its voice's level, so
  levels matched by role (REQ-BEN-09) still hold.
- **Verify:** test

### REQ-MIX-02: Send effects
Each table in `sends` is a shared effect that strips feed, of one of two
kinds:
- `delay`: an echo every `beats` beats, so it follows the tempo. Each echo
  is `feedback` times the one before (under 1). A low-pass filter in the
  loop (`lowpass`, in Hz) and a high-pass at 80 Hz darken and thin each
  repeat. With `pingpong = true`, the echoes alternate between left and
  right.
- `reverb`: a hall whose tail takes `decay_s` seconds to fall by 60 dB, with
  highs that die away faster above `damping` Hz, after a gap of
  `predelay_ms`. Four allpass filters diffuse the input, then eight delay
  lines feed back through an orthogonal (Hadamard) mix. Each line's length
  drifts by a fraction of a millisecond, so the tail is dense and smooth
  rather than metallic.

Every send returns through a strip of its own, set in the same table:
`highpass`, `lowpass`, `level`, `pan`, and a `send` to a send listed after
it, so dub echoes can fade into the hall. Nothing in a send is random, so a
mix renders the same every time (REQ-AUD-05).
- **Why:** a tempo-synced delay and a smooth hall are the two effects that
  Detroit and dub techno lean on most, and BM-FX-01 and BM-FX-03 describe
  them. SuperCollider's built-in reverbs ring metallically. A feedback delay
  network with an orthogonal mix and slowly moving lines is the standard
  way to get a dense, colourless tail. Only sending to later sends keeps
  feedback between sends impossible.
- **Verify:** test, measure

### REQ-MIX-03: Automation
An `automate` table moves strip controls over the track. On a voice it can
move `highpass`, `lowpass`, `level` and each send, named as in `send`. On a
send's table it moves the return's `highpass`, `lowpass` and `level`, and on
the master its `highpass`, `lowpass` and `level`. Each control gets a list
of points, `[bar, value]`. Bars count from 1, and a fraction of a bar is a
place in it: 43.5 is the third beat of bar 43. Between two points the
control moves in a straight line: filter frequencies evenly in octaves,
levels and sends evenly in dB. Before the first point it holds the first
value, and after the last point the last. Two points at the same bar make
a jump, so a send can open for one word and close again: an echo throw.
A level or send of `-inf`, or anything at -90 dB or lower, is silent, and
a move from silence starts at -90 dB.

A filter that is automated is on, even if the strip doesn't set it. Every
change is smoothed over 5 ms, so a jump doesn't click. An excerpt
(REQ-TRK-03) plays the automation of its own bars: each control starts at
the value the track has at the excerpt's first bar.
- **Why:** filter sweeps, swells and echo throws are how a track moves from
  one section to the next without new parts. A throw on the last word of a
  vocal line is the dub way of weaving a vocal into a groove. Octaves and
  decibels are how ears hear frequency and level, so straight lines in
  them sound even.
- **Verify:** test

### REQ-MIX-04: Rendering a mix
A score with a mix renders in SuperCollider in one pass. Each voice writes
its stem as it enters its strip, so stems stay dry and every check
measures them as before: a filter or a send never moves a hit away from
its template. The sidechain's targets are also written before they are
ducked, for REQ-BEN-12's dip check. The master comes out of the master
strip in the same pass. A second pass writes the master alone, and it must
match the first exactly (REQ-AUD-05).

SuperCollider gets enough working memory for every send's delay lines on
top of the voices' share. Any SuperCollider render, mixed or not, fails
with a plain reason if a synth couldn't start, since a note or a part of
the mix would be missing.

Renoise refuses a score with a mix for now. Its songs will carry the mix
the Renoise way, with send tracks, effect devices and automation in the
patterns, when the bridge can build them (#30, #50).
- **Why:** measuring the dry voices keeps every existing check meaningful.
  One pass for stems and master, instead of a separate bypass render and a
  full second render, keeps an 8-minute track within the Mac's disk (about
  9 GB free on 2026-11-14), and the master is what the determinism check
  needs. On 2026-11-14 a test mix with one echo at 96 kHz used up
  SuperCollider's default 8 MB: most notes never started, the render still
  "succeeded", and only the timing check noticed.
- **Verify:** test, measure
