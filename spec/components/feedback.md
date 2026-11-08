# Feedback loop

**Status:** draft (built in step 3)

A local web page where Dean judges renders by ear, and an AI loop that turns
verdicts into spec or code changes. No code reading needed.

### REQ-FBK-01: Local listening page
`mph bench serve` opens a page on `localhost` listing benchmarks. Each shows
its description, measurements (pass/fail with numbers), a player, and an
A/B toggle where the benchmark defines one (e.g. sidechain bypass).
- **Why:** Dean needs to judge output without looking at code.
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
The page binds to 127.0.0.1, serves only files under `data_dir/renders`, and
has no remote access.
- **Why:** standard security. Nothing on the LAN should be able to read the library.
- **Verify:** test
