# Roadmap

Where the work stands and what comes next, as of 2026-11-21. GitHub issues
hold the detail and are the live list; this page keeps the order and the
reasons, so a newcomer can pick up the next thing without asking. Update it
when an item ships or the order changes.

## The plan

| Step | Outcome | Status |
|---|---|---|
| 1 | Spec, config, Keychain secrets, doctor and installers, CI | done |
| 2 | Sample catalogue and search engine | done |
| 3 | Benchmark kit and the listening page | most benchmarks built; effects, vocals and the rest of the north star to go |
| 4 | Renoise bridge and mastering | bridge built for drums and arranged tracks; melodic parts in Renoise (#30) and mastering (#10) to go |
| 5 | The first track, a 13-minute piece in the north star palette | waits for step 3 |
| later | Live performance from the laptop keyboard, then MIDI controllers | after full tracks render |

Benchmarks come before tracks (spec/vision.md): the first track's spec is
written only once Dean has passed the benchmarks it depends on.

## Next, in order

Agreed with Dean on 2026-11-13 and kept since; agents take the next item
unless Dean reorders.

1. **#31** Harden and name the catalogue and benchmark site, with an API for
   exploring samples with an LLM. An ADR comes first, and Dean picks the
   name.
2. **#49** A jungle take of the breakbeat chop (BM-DRUM-09) and an oldskool
   tech house pattern: Dean's ideas 15 and 16.
3. **#7** Vocals: fit a local vocal, and spoken word from the Internet
   Archive (Demucs). Then the north star's **#39** (borrowed conga groove,
   layered kick) and **#40** (Detroit parallel stabs, Jersey organ bass).
4. **#50** Effects benchmarks: delay throw, filter build, reverb wash, beat
   repeat. Then **#30**: Renoise takes for the acid line and the sidechain.
5. **#68** Remix 1 for everyone: a free version with no private sounds, so a
   fresh install can render the remix. Dean's answers (2026-11-15): CC-BY is
   fine, and two variants. Three PRs, in order:
   1. the fully synthesised variant: a 909 kit, a formant lead, a vocoder
      lead, a group and the horn, all synths, as written phrases
      (`love-train-remix-1-free-synthesised`, BM-LTFS-01 to 05; PR open
      2026-11-21, waiting for Dean's ears and the Renoise takes on the
      Mac);
   2. the cleared-samples variant: vocal samples from Freesound and other
      free sources, with a command that fetches them and a track-analysis
      command;
   3. SunVox takes of both.

## Later

- **#65** Read a loop's tempo from its beats over its length (they matched
  the names on every loop checked). It changes kit picks, so it needs its
  own `scripts/kit-diff` on the Mac.
- **#67** Resample samples with libsoxr instead of SuperCollider's cubic
  interpolation? Research, and Dean's call.
- **#10** Mastering and delivery: one conversion and dither, loudness and
  true-peak checks.
- **#41** The sermon and talking drum benchmarks (blocked on #7 and #30).
- **#9** Renoise plug-ins with an install check, and other ways to drive
  Renoise.
- **#8** Unresolved ratings as GitHub issues, and an advisory AI reviewer.
  Maybe not worth building now that agents read ratings through the
  `feedback` tool.
- **#51** Live performance. A late phase on purpose (Dean, 2026-11-10): only
  after full tracks render, when the live setup is designed.
- **#52** Search ranking when no tempo is given, and one-shots tagged as
  loops. Dean hasn't decided; don't build it unasked.
- **#63** A bug: the derivatives page on the site doesn't open.

## Remixes

Dean's list for Love Train, one at a time, each finished before the next:
Detroit techno with Jersey garage (remix 1, done for now), Afro with acid
techno, jungle with djembe, clean Detroit, dub techno and acid tech. The
research in [research/love-train-remix-practice.md](research/love-train-remix-practice.md)
has a plan for each, and spec/tracks/love-train.md has Dean's rules.
