# Sound matching

**Status:** built

The matcher fits a synth's knobs to a reference sound, such as a recorded
train horn. It renders candidates, scores each one against the reference,
and searches for the knob values that come closest (ADR-0014). The score
owns the notes and their timing, and the match owns the tone: a horn can be
fitted to a real horn at the real horn's own pitch, then play whatever chord
the track needs. Only knobs reach a render. A person pins the ones they
like in the score's `params`, so a fresh clone renders the same track
without the reference or the search (REQ-AUD-05).
Implementation: `src/mph/match/`.

### REQ-MAT-01: A match file
A match is a TOML file in a `matches/` folder of a project (REQ-PRJ-01). It
names:
- `synth`, the SynthDef it fits, and the score voice it is for (`score` and
  `voice`), whose own `params` are the baseline (REQ-MAT-06);
- `[reference]`: the sound, by its catalogue hash (REQ-BEN-16) or a `path`
  next to the match file, the span it fits (`start_s` and `end_s`, in
  seconds), what it is (`what`) and its licence (`licence`);
- `[play]`: what each candidate plays: `notes` (note names, or Hz, one
  synth each), when the notes start (`at_s`, from the span's start) and
  how long they hold (`length_s`);
- `[knobs]`: each knob the search turns, with its range as `[low, high]`,
  or `[low, high, "log"]` for a range searched in ratios, such as a
  frequency's;
- `[params]`: knobs held at a value, and `keep`: knobs the score keeps when
  a match is pinned, such as a fall set to stay in the key;
- `budget` (renders, 3,000 unless it says), `seed` (1), `tail_s` (3 s of
  room for each candidate's release) and `[weights]` for the views
  (REQ-MAT-03, each 1 unless it says).

A knob or param must be one of the synth's own controls, and not one a
render sets (`out`, `amp`, `gate`, `freq`, `acc`, `seed`, `rid`, `part`).
`mph match analyse FILE` describes the reference's span, to help write the
file: its level and pitch every quarter of a second, and its strongest
partials where it is loudest.
- **Why:** a match's inputs are text a person can read and review, and the
  search can run again from them wherever the reference is. What comes out
  is named knobs a person could turn.
- **Verify:** test

### REQ-MAT-02: Candidates render as the track renders them
Each candidate renders in SuperCollider (scsynth, non-realtime) at the
reference's own sample rate, so neither is resampled (REQ-AUD-01). Each
note is a synth of its own, with its `part` and a random generator of its
own, seeded the same for every candidate, so they differ only in their
knobs. A generation of candidates renders in a few scsynth runs at once
(`--workers`, the computer's cores less one unless it says), each candidate
in a slot of its own: the reference's span, then `tail_s` of silence for
its release.
- **Why:** a candidate must sound as it would in the track, or the fit is
  to another instrument. Rendering many candidates in one run skips
  scsynth's start-up: on Dean's Mac a candidate alone took 1.5 s, 1.3 s of
  it starting up, and 58 ms in a run of 64 (2026-11-22).
- **Verify:** test

### REQ-MAT-03: Close is one number
The distance between the reference and a candidate adds up five views of
both, in mono, each a classic measurement (ADR-0008):

| View | What it hears | How it is measured |
|---|---|---|
| `spectrum` | tone colour and attack | log-magnitude spectra with windows of 256, 1,024 and 4,096 samples (about 5, 21 and 85 ms) |
| `mel` | overall colour, weighted as ears weigh it | 96 mel bands |
| `loudness` | swell, attack and decay | the level every 512 samples, in dB |
| `pitch` | bends, vibrato and scoops | YIN, in cents, where both sounds are within 30 dB of their loudest, searched from a fifth under the lowest note to an octave over the highest |
| `noise` | breath, hiss, the rush of air | spectral flatness, where the reference is within 40 dB of its loudest |

Before comparing, the candidate is scaled to the reference's RMS level, so
the distance is about the sound, not its level. The spectra, the mel bands
and the level hear down to 60 dB under the sound's own level (its RMS over
the span), and anything quieter counts as silence; once scaled, both
sounds share that floor. Each view's distance is divided by its median
over the search's first generation, which is random, so each counts about
as much as the others, and then multiplied by its weight.
- **Why:** each view hears something the others miss, and dividing by its
  spread stops a view with big numbers from drowning the rest. YIN rather
  than pYIN, because pYIN's smoothing takes about a second for a
  10-second sound, and a search scores thousands. The floor, because a
  separated stem's faint residue and a synth's digital silence sound the
  same in a mix: without it, two renders of one patch whose controls
  differed only by rounding were 0.046 apart in the spectrum view, two
  thirds as far as a real change of tone (`brass` from 900 to 950 Hz,
  0.067); with it they are 0 apart (2026-11-22).
- **Verify:** test

### REQ-MAT-04: The search
Differential evolution (scipy) searches the knobs, each scaled to 0..1 (a
log knob on a log scale), with a population of 6 for each knob, seeded.
The first generation is spread out by a Latin hypercube, with the
baseline's knobs as one of its members. Each generation renders at once.
The search stops after `budget` renders, or when the best distance has
improved by less than 1% over 15 generations. Every 10 generations it
prints the closest distance so far, the renders and the minutes.
- **Why:** measured on 2026-11-22, this search found every knob of a hidden
  horn patch within 5% of its range in 2,928 renders (ADR-0014).
- **Verify:** test

### REQ-MAT-05: Known answers first
`mph match check FILE` hides a patch (random knobs in the match's ranges,
from its own seed), renders it as the reference, and searches for it with
the match's settings. It passes when the patch it finds is closer to the
hidden one than a tenth of the first generation's median distance. It also
reports how far off each knob is, as a share of its range: a knob more
than 5% off is one this sound barely shows. A match against a recording is
trusted only once its check has passed. The check renders at the
reference's rate, or at the production rate when the reference isn't on
this computer (REQ-AUD-01), and deletes its renders when it finishes.
- **Why:** if the search can't find a patch of its own synth, its distance
  to a recording means nothing. And a knob it can't hear is worth knowing
  about, because pinning it is guesswork.
- **Verify:** test

### REQ-MAT-06: What a run leaves
`mph match run FILE` searches against the reference and writes a folder
under `data_dir/matches/<project>/<match>/`, named by the time it started:
- `report.json`: the reference's hash or path, span and licence, the knobs
  found, the distance in each view, the baseline's, the renders, the
  generations, the seconds it took, the seed and the checkout it ran from;
- `reference.wav`, the span it fitted;
- `baseline.wav`, the voice as the score has it now, the hand-made patch;
- `candidate-1.wav` to `candidate-3.wav`: the three closest candidates that
  are at least 0.1 apart (the root mean square of their knobs' differences,
  each scaled to its range), and closer than the baseline.

The baseline and the candidates are scaled to the reference's level, so
they can be heard side by side.

It prints the closest candidate's knobs, less `keep`, written as the
voice's `params`, and says how much closer than the baseline it is. A run
that can't beat the baseline says so, and leaves no candidate to pin. When
a run finishes, older runs of the same match keep only their
`report.json`. Nothing it writes goes into the repo, and the reference is
read, never copied into a render or a project.
- **Why:** Dean's ears decide, and the number only shortlists: Dean hears
  each candidate next to the reference and the hand-made patch. The
  baseline is the bar a match must clear, closer than the patch made by
  hand. The reference may be private (the song's own horn), so only its
  hash leaves the Mac. Old runs go as old renders do (REQ-RET-01).
- **Verify:** test

### REQ-MAT-07: Pinning
A match's knobs reach the track only when a person pins them in the score:
the voice's `params`, with a comment that names the match file, the run
and its distance against the baseline's. Renders then depend only on those
numbers.
- **Why:** the patch is the record, not the search. A seeded search repeats
  on one machine, but tiny differences in floating point between machines
  can lead it elsewhere, and the reference may be on one Mac only.
- **Verify:** inspect
