# Feedback loop

**Status:** draft (built in step 3)

A local web page where Dean judges renders by ear, and a loop in which
agents turn his ratings and ideas into spec or code changes. No code reading
needed.

### REQ-FBK-01: Local listening page
`mph bench serve` opens a page on `localhost` listing benchmarks. Each shows
its description, measurements (pass/fail with numbers), a player, and an
A/B toggle where the benchmark defines one (e.g. sidechain bypass). It is the
Benchmarks tab of the sample browser (REQ-BRW-01), so sounds and renders are
in one place. Each take starts from the top when played. A "Keep the
playhead" switch (off by default) makes switching takes keep the moment
instead, to compare one spot. A "Match loudness" switch (on by default)
plays every take at the loudness of the quietest rendered take on the page
(integrated LUFS, ITU-R BS.1770), by turning the player down, never by
changing the files; a part played on its own gets its render's setting.
The loudness is that of the file the page plays: the master's, plus the
delivery trim a render needed to stay under the ceiling (REQ-BEN-06).
Each sound the render used can be played
inline: the catalogue sample on its own, and that voice's stem from the render.
Every rendered variant of a benchmark (REQ-BEN-07) is a take, shown as an
equal alternative: a row of takes to play and switch between, and a list
that expands to show each take's measurements, sounds, the commit it was
rendered from (REQ-BEN-03) and its own rating form. Each take shows its
rating as stars, whether it is resolved, and whether it was rendered again
since it was rated (REQ-FBK-06). The benchmark's own rating form rates every
take at once (REQ-FBK-02).
A take whose audio was pruned, or that was never rendered, can be rendered
from the page (REQ-RET-06).
Benchmarks are grouped under their project (REQ-BEN-11), projects sorted
by name. Each project can be collapsed to its heading, which still shows how
many of its takes are resolved, unresolved and still to rate. Each benchmark
folds to its heading and badges too, and starts folded. A fold opens or
closes only when clicked: saving a rating or an idea, or finishing a render,
redraws the page with every fold and the scroll position as they were, and a
reload keeps them too.
- **Why:** Dean needs to judge output without looking at code. A louder take
  tends to sound better whatever else differs, so fair comparison needs
  matched loudness. Before the trim was counted, BM-SC-01's bypass take
  played about 1.2 dB too quiet in Dean's A/B (2026-11-11).
- **Verify:** test

### REQ-FBK-02: Ratings are recorded
A rating gives a take 0 to 5 stars: 3, 4 or 5 is a pass, and 0, 1 or 2 is a
fail. It can say whether the take is resolved or unresolved (REQ-FBK-06),
and carry free-text feedback with optional times like "0:42 hats too loud".
It is stored in the catalogue DB with the render's score hash, the take, the
render's `inputs` (REQ-BEN-14) and when it was given, so it points at
exactly what was heard. A rating is for one take, or for the whole
benchmark: a whole-benchmark rating is stored once for every take rendered
at the time, marked as such. A take's own rating always wins over a
whole-benchmark one, so 4 stars for the benchmark covers every take except
one rated on its own, before or after. A rating given on a `main` render,
from before takes were equal, counts for the whole benchmark: every take
rendered by then.
0 stars with neither resolved nor unresolved is no rating. Saved on a take
that has a rating of its own, it clears that rating, so the take is unrated
again, or back under the whole-benchmark rating it had. Otherwise there is
nothing to save, and the page says so.
- **Why:** stars say how good a take is, not only whether it passes, so
  takes that all pass can still be ranked against each other (Dean,
  2026-11-13). Ratings tie back to the exact render they judged. Dean rates
  a benchmark as a whole and singles out the takes that differ, so one click
  should cover the rest. Clearing is how a mistaken rating is taken back.
- **Verify:** test

### REQ-FBK-03: Unresolved ratings become issues
An unresolved rating can be turned into a GitHub issue, labelled
`area:benchmarks`, `type:feedback` and the benchmark ID, with the
description, measurements, stars and feedback. Uses `gh` if it's
authenticated, otherwise writes a markdown draft for Dean to paste.
- **Why:** context lives in GitHub, where agents and Dean can retrieve it later.
- **Verify:** test

### REQ-FBK-04: AI responds to feedback
An advisory reviewer (configured LLM provider, falling back to local Ollama)
reads the rating, report and spec item. It proposes a spec change, a code
change, or a parameter tweak, as an issue comment. It never edits on its own.
- **Why:** AI in the loop speeds up convergence, and the human stays the gate.
- **Verify:** inspect

### REQ-FBK-05: Local only
The page binds to 127.0.0.1 and has no remote access. Of the renders, it
serves only the files a render's own `report.json` lists, under
`data_dir/renders`. Saving a rating or an idea needs JSON from the page's
own origin, so another website can't post one.
- **Why:** standard security. Nothing on the LAN should be able to read the library.
- **Verify:** test

### REQ-FBK-06: Resolved and unresolved
Resolved means nothing more is wanted from a take: it renders again only
when asked to (`--force-rerender`, or the page's Render button). Unresolved
means it needs work: agents find it through the query channel (REQ-QRY-12),
and it renders again once something that shapes it changes (REQ-BEN-14).
Dean sets either one on the rating form, by clicking it, and can add
feedback with it. Until he does, the stars decide: a pass (3 to 5 stars) is
resolved, and a fail (1 or 2 stars) unresolved. 0 stars needs one of the
two, or it is no rating (REQ-FBK-02).
A take was rendered again since it was rated when its newest render's
`inputs` differ from those of the render the rating was given on (or, for a
rating carried over from a verdict, which has none, when it was rendered
after the rating). The page shows this on the take, and the query channel
marks it, because the take then waits for Dean's ear rather than for an
agent.
- **Why:** Dean, 2026-11-13. Stars rank takes, and resolved says whether
  anything still has to happen. A take can meet its description and rate
  lower than its siblings, which is useful contrast, and still be resolved.
  One that sucks but isn't a priority can be resolved too, so it stops
  costing renders. Unresolved takes are where agents find their work, and a
  fix waiting to be heard shouldn't be worked on twice.
- **Verify:** test

### REQ-FBK-07: Rating history, and verdicts carried over
Every rating is kept. A newer one stands instead of an older one, and the
older stays in the history with its stars, status, feedback and the render
it judged. The page shows each take's recent ratings, and the query channel
all of them (REQ-QRY-10).
The pass, close and fail verdicts given before ratings existed are carried
over once, when the catalogue is upgraded. A pass becomes 3 stars:
unresolved if it had notes, which the old rule treated as feedback still to
act on, and otherwise resolved by its stars. A close becomes 2 stars and a
fail 1 star, both unresolved by their stars. Each keeps its notes, time,
scope and render, and says which verdict it came from. The `verdict` table
itself is left as it was, and nothing writes to it any more.
- **Why:** a rating that replaces another must not lose what was said
  before: agents read the history to see what was asked and what changed.
  Carrying verdicts over keeps every judgement Dean already made, and an
  old pass is the lowest pass until he rates it higher. The old table stays
  so a branch from before ratings still runs against the same catalogue.
- **Verify:** test

### REQ-FBK-08: Ideas from the page
On the Benchmarks tab, Dean can add an idea for a new project, for a new
benchmark in a project (an existing one or a project idea), or for a new
variant of a benchmark (an existing one or a benchmark idea). An idea has a
title, his notes on what he wants and why, and an id: one he types, or one
mph fills in.
- A project id is lower-case words joined by hyphens, filled in from the
  name ("Tribal percussion" becomes `tribal-percussion`).
- A benchmark id is `BM-AREA-NN`. Filled in, the area is the one its
  project's benchmarks use most (for a project with none, the first word of
  the project's id, in capitals, cut to five letters), and the number is the
  next one free in that area, counting the spec, the scores, renders and
  every idea, dropped ones too.
- A variant name is lower-case words joined by hyphens, filled in from the
  title, with `-2`, `-3` and so on added if the benchmark already has it.

A typed id must have the same form and be free: not used by a project,
benchmark, take or open idea. Ideas are kept in the catalogue DB with when
they were added. An idea is built once its project, benchmark or variant
exists (a score here declares it, or a render from another branch has it).
Dean can drop an open idea, which drops the open ideas under it too, and
undo that, which brings back just the ideas that drop took. The page shows each open idea
where it would go: projects among the projects, benchmarks in their
project, variants in their benchmark. Agents read every idea through the
query channel (REQ-QRY-12) and build it as a spec and score change.
- **Why:** Dean, 2026-11-13: new patterns and variants come to mind while
  listening, and the page is where he is then. Agents turn them into spec
  and scores, which stay the source of truth (ADR-0001). Filling ids in
  keeps them unique and in the spec's form, without Dean looking them up.
- **Verify:** test
