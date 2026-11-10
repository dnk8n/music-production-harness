# Feedback loop

**Status:** draft (built in step 3)

A local web page where Dean judges renders by ear, and an AI loop that turns
verdicts into spec or code changes. No code reading needed.

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
Each sound the render used can be played
inline: the catalogue sample on its own, and that voice's stem from the render.
A benchmark with rendered variants (REQ-BEN-07) shows a "Variants" list that
expands. Each variant can be played, compared with `main` at the same moment,
and given its own verdict, with its measurements beside it.
A take whose audio was pruned, or that was never rendered, can be rendered
from the page (REQ-RET-06).
- **Why:** Dean needs to judge output without looking at code. A louder take
  tends to sound better whatever else differs, so fair comparison needs
  matched loudness.
- **Verify:** test

### REQ-FBK-02: Verdicts are recorded
A verdict (pass, fail, or "close", plus free-text notes and optional
timestamps like "0:42 hats too loud") is stored in the catalogue DB with the
render's score hash.
- **Why:** verdicts tie back to the exact render they judged.
- **Verify:** test

### REQ-FBK-03: Verdicts become issues
A fail or "close" verdict can be turned into a GitHub issue, labelled
`area:benchmarks`, `type:feedback` and the benchmark ID, with the
description, measurements and notes. Uses `gh` if it's authenticated,
otherwise writes a markdown draft for Dean to paste.
- **Why:** context lives in GitHub, where agents and Dean can retrieve it later.
- **Verify:** test

### REQ-FBK-04: AI responds to feedback
An advisory reviewer (configured LLM provider, falling back to local Ollama)
reads the verdict, report and spec item. It proposes a spec change, a code
change, or a parameter tweak, as an issue comment. It never edits on its own.
- **Why:** AI in the loop speeds up convergence, and the human stays the gate.
- **Verify:** inspect

### REQ-FBK-05: Local only
The page binds to 127.0.0.1 and has no remote access. Of the renders, it
serves only the files a render's own `report.json` lists, under
`data_dir/renders`. Saving a verdict needs JSON from the page's own origin,
so another website can't post one.
- **Why:** standard security. Nothing on the LAN should be able to read the library.
- **Verify:** test
