# How mph got here

A short history of the project, so a newcomer (or a fresh agent) knows what
was tried, what Dean decided, and why things are the way they are. The spec
holds the rules with their Why; this page holds the story that connects
them. Pull request numbers link the work.

## How the work was done

Dean set the goals, listened, and rated. The code was written by Claude Code
agents, one cloud thread per piece of work, each on its own branch and pull
request, with a coordinator keeping the order of work. Anything that needed
Dean's Mac (Renoise, Demucs, the Keychain, Dean's samples) ran there over
Remote Control, a Claude Code session on the Mac started from the thread.
Dean merged every pull request himself. What the agents learned was first
kept in the project's memory, then written into the spec, the skills and
these docs, so the repo alone is enough (Dean, 2026-11-15 and 2026-11-21).

## 2026-11-08: the spec, then the catalogue

- **Spec first.** Dean wanted a system whose value is its spec and a
  deterministic toolchain, where every sound traces back to a sample, a synth
  patch or a pattern, and no neural model makes music (spec/vision.md). The
  first PR (#1) laid down the spec, config, Keychain secrets, `mph doctor`
  and the installers, and CI.
- **Benchmarks before tracks.** The first milestone was a set of listening
  benchmarks for Dean's ear (ten drum patterns, an acid line, effects,
  sidechain, vocals), plus professional checks. No track gets a spec until
  they pass.
- **The catalogue.** A read-only scanner with name hints and audio analysis
  (#17), recipes and a rebuildable cache (#18), a sample browser (#19), and
  downloads from Freesound, the Internet Archive and LibriVox with their
  licences (#20). Sources are never written to; everything derived lives in
  the data folder and can be made again.
- **Credentials.** A bare "password" prompt confused Dean, so every
  credential now asks for each field and says where to find it (REQ-SEC-05).
- **Audio.** Dean's rule: convert once. Samples stay at their own rate,
  synths may render higher, and the only conversion is to 48 kHz / 24-bit
  AIFF at delivery (ADR-0003).

## 2026-11-08 to 11-10: drums, Renoise and the listening page

- **Drum benchmarks** rendered in SuperCollider, measured and delivered,
  with a page to judge them (#21).
- **Renoise holds the patterns.** Pure SuperCollider renders felt closed to
  Dean, who wants to open a song and change it by hand. Each benchmark
  became a small Renoise song with one track per voice, built by a Lua tool
  from a JSON plan and rendered unattended (#22, ADR-0009). Dean's first
  real Renoise render passed by ear. mph refuses to use a Renoise it didn't
  start, so it can never touch a song Dean has open (#23).
- **Takes.** Dean asked for each benchmark made several ways, each rated on
  its own (#23). There is no "main" take: all are equal, and benchmarks are
  grouped into projects that fold away (#28).
- **The page.** Takes play from the top and loudness-matched, because a
  louder take sounds better whatever else differs (REQ-FBK-01).
- **The acid line** (BM-BASS-01, #24). One hat, `BHG2_Analogue_Hat_17`, made
  seven patterns sound wrong to Dean: it rings at a few pitches instead of
  hissing, so kits now pass over tonal hats and cymbals (REQ-BEN-05).
- **Keep only fresh renders** (#25, ADR-0010). Renders are deterministic, so
  mph keeps the newest of each take and makes older ones again on demand.
  96 kHz float renders had filled tens of gigabytes in days.
- **Checks.** Two PRs that each passed CI broke main together (#26), and
  Linux CI hid Mac-only gaps. Since #27: pre-commit hooks that match CI,
  pinned CI runners, and `scripts/mac-check` for every test on the Mac,
  where a skipped test fails the check.

## 2026-11-11 to 11-12: sidechain, the query channel, the north star

- **Sidechain** (BM-SC-01, #29). Dean couldn't hear a 6 dB, 200 ms duck, and
  the whole-mix measurement agreed. The release turned out to matter more
  than the depth, because Dean's kick masks the first 100 ms. Dean's pick
  for production is the `exaggerated` take (24 dB, 430 ms), and an
  over-the-top take (36 dB with a hold, #43) is for short moments around
  build-ups and drops.
- **The query channel** (#32, ADR-0011). One command, `mph mcp install`,
  gives Claude read-only tools for the catalogue, benchmarks, renders,
  ratings and health, so a cloud thread can ask about Dean's library through
  a session on the Mac without being able to change anything.
- **One take per software.** Dean's rule: every benchmark project gets a take
  for each piece of software, built the way that software's users would do
  it, not copied from the other (issue #30).
- **The north star** (#38): benchmarks from the palette of Dean's first
  track (spoken word, Detroit techno, a Jersey garage twist, West African
  drums mixing machines with recordings), starting with a bell pattern on a
  triplet grid and the uneven feel of jembe drumming.
- **Labels.** File and folder names give labels, but genre and artist names
  don't give a role (#42, #45). A names change relabels thousands of sounds
  at the next scan, so each one is checked on the Mac with
  `scripts/kit-diff` before it merges.
- **A pass is a pass** (#44). A take Dean passed never renders again unless
  something it depends on changed, and it keeps the sounds Dean heard.

## 2026-11-13: stars, fresh takes, scratch songs

- **Renoise asked to save** after renders; it doesn't any more (#46,
  REQ-REN-08).
- **Stars replace verdicts** (#47, ADR-0012). Dean rates each take 0 to 5
  stars (3 or more is a pass) and marks it resolved or unresolved. Resolved
  means done, or not worth more work; unresolved is the agents' to-do list,
  read through the `feedback` tool. Dean adds ideas for projects,
  benchmarks and takes on the same page. Of 46 takes rated that day, 45
  were resolved.
- **Fresh takes** (#53, #54). Dean asked for a fresh take of every
  benchmark, with sounds picked using the newest tools. An agent picked them
  through the query channel and pinned each by its hash, with the reason,
  so the render is repeatable. A kick that swells in was timed late, so
  hits are now found by their shape.

## 2026-11-14 to 11-21: Love Train, remix 1

- **The source.** The catalogue learned whole songs and their stems (#56):
  Love Train sits on an exact 120 BPM grid from 0 s, and Demucs on the CPU
  gives the same stems byte for byte. The catalogue also reads what files
  say about themselves: tags, loop tempos and sampler notes, trusted only
  where they fit the file (#60).
- **Version 1** (#58): arranged scores, clips of a recording, and rating a
  track in blocks. It was a re-edit: a new intro, then the source's own
  bars. Every measurement passed and Dean gave every block 0 stars. Dean's
  rule since: a remix keeps only the vocals and a signature sound, and
  builds the rest new, with real sound design.
- **Disk.** Test temp folders and an unfinished render filled the Mac's disk
  (#61); passing tests now delete their folders (#62).
- **Version 2** (#66): the vocals and the train horn woven through new
  Detroit parts, with a mix of sends, automation and a sidechain. Dean:
  "great for now". Its Renoise version builds and renders unattended, after
  fixes for Renoise's "may be busy" dialog and LuaJIT's limit of 65,536
  constants in one function. Merged 2026-11-21.

## 2026-11-21: everything into the repo

Dean asked for the repo to hold everything needed to reach the same state on
another Mac: Dean's projects as examples that can be turned off, placeholder
sounds from free sources, and all the context that had lived only in the
agents' memory and chats. That is this page, the rest of `docs/`, and
`examples/` (spec/components/projects.md).

## 2026-11-21: remix 1 for everyone

The first variant of issue #68: remix 1 with nothing from the song and no
samples, so a fresh clone renders it the same anywhere and every render is
`licence:clear`. Version 2's form, parts and mix stay; a 909 kit, a soul
singer (formant), a machine (vocoder), a group and the train's horn are new
synths, singing wordless lines written as phrases (REQ-TRK-08) with a
syllable per note (REQ-BEN-19). Two things learned on the way, now in the
spec: a legato note can't be timed against its template, because the note
before still sounds over its start (REQ-BEN-01); and the first singing
synths were so quiet that level matching held them at +12 dB, 17 dB under
their target, until they were made louder at the source (REQ-BEN-09). On
the way, measuring a whole track's hi-hat chokes went from 17 minutes to
under a second, so the whole track renders in 5 minutes in a cloud copy.

## 2026-11-22: machine soul

Dean rated the free remix's first block 1 star: the horn's chord was
dissonant and "too video game like", and the voices "lethargic and low
energy". Dean asked to get that block right in both engines first, chose
machine soul for the vocals (a talk box and a vocoder with gospel group
shouts, owning that they are machines), asked for the Renoise take to be
built as a Renoise programmer would, without SuperCollider, and for a way
to fit a synth to a given sound, running on Dean's own Mac. The horn was
rebuilt as an air horn sounds, in the key, and the vocals as machine soul.
On the way, the whole-mix pump check learned to leave out synth vocals as
it left out a remix's recorded ones (REQ-BEN-12): sung lines ride on top.
And Renoise turned out to clip a float sample above full scale as it loads
it, which had flattened the vocals' accents in the Renoise take: a voice's
hot samples are now lowered to full scale together, and the gain goes back
on the sample's volume (REQ-REN-09).

## 2026-11-22: fitting synths to sounds

For that fit, the matcher (`mph match`, ADR-0014) searches a synth's knobs
by differential evolution: each candidate renders in SuperCollider as the
track would play it, and five classic views of the sound score it against
the reference. Reinforcement learning, a trained network, a differentiable
synth and a local LLM turning knobs were each weighed and set aside, as
none of them is better at one sound and each would choose the sound
itself. Known answers come first: with the real horn, a search found six
hidden knobs to within 0.1% in a minute. The first match fits the free
remix's horn to the song's own, which stays on Dean's Mac; only the knobs
Dean picks reach the score.

## Outside the repo

Some things are kept on Dean's Mac, on purpose:

- Dean's catalogue (`catalogue.sqlite`) with every rating, note and idea.
- Renders, which can be made again from the scores.
- Three throwaway Renoise songs from 2026-11-09, made before mph could build
  songs, as a progress check: a 13-minute sketch, a second version with
  vocal chops and sound design, and a 3:27 radio cut. They were written
  straight as Renoise XML and are binary, so they stay on the Mac, in
  `/Users/dean/dj/mph-snapshots/`.
