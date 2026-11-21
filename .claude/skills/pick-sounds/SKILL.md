---
name: pick-sounds
description: Choose sounds from Dean's sample catalogue for a benchmark take or a remix part, with the read-only mph MCP tools on Dean's Mac. Covers the tools, what the labels and measurements can be trusted for, pinning a pick, and checking a change against Dean's library before merging.
---

# Pick sounds from Dean's catalogue

Dean's samples are on Dean's Mac and read-only (REQ-CAT-01). The catalogue, an
SQLite DB in mph's data folder, labels every sound and the stems and clips
derived from them (spec/components/catalogue.md). A cloud copy of the repo
can't see it: ask over Remote Control (the mac-work skill), where the `mph`
MCP tools are loaded, or run `uv run mph mcp call TOOL '{...}'` there
(spec/components/query-channel.md).

## The tools

All read-only: `overview`, `search_sounds`, `sound`, `labels`, `benchmarks`,
`benchmark`, `report`, `ratings`, `feedback`, `ideas`, `schema`, `sql` (a
guarded SELECT) and `doctor`. `feedback` lists Dean's unresolved ratings and
open ideas: the agents' work queue (REQ-QRY-12). Answers stop at 40,000
characters: `benchmarks` for every project passed that on 2026-11-21
(41,117), so ask one project at a time, `{"project": "Drum patterns"}`, as
`overview` names them. From a shell,
`uv run mph catalogue sound REF` (a hash, a prefix of 6 or more characters,
or a path) answers as `sound` does: every label with its origin and
confidence, the files, the analysis, any download and the renders that used
it.

## What to trust

- Names are hints. A filename (0.6) and its two nearest folders (0.5 and
  0.4) suggest a role, tempo or key. Genre names and artist credits ("bass
  house", "X presents") never give a role, however deep they sit.
- What was measured wins in search: the analysis gives form, tempo, key and
  onsets.
- What a file says about itself (REQ-CAT-12): an ACID chunk's tempo counts
  only when the file's beats at that tempo fill it to within 0.5%, because
  one big pack stamped 120 BPM on nearly every loop. Tags count for little
  (0.35).
- A hat, shaker or cymbal must sound like noise. One whose spectrum is
  tonal (flatness under 0.05, `kit.TONAL_FLATNESS`) whistles, and kit choice
  passes it over.
- Check the length: some one-shots in the library last only 10 to 15 ms.

## Pin what you pick

A voice can pin one sound:
`sound = { role = "kick", hash = "<64 hex>", why = "..." }` (REQ-BEN-16).
The render plays exactly that sound, and its report shows the why as
"picked: ...". Write the why from what you heard or measured. A pin needs a
role and a why, and can't sit with a synth. If the pinned sound is missing
or unplayable, the voice falls back to its search and the report says so.

A take Dean rated a pass keeps its sounds when it renders again
(REQ-BEN-15), so a better search never swaps out what Dean heard.

## Before changing naming rules, search or kit choice

Run `scripts/kit-diff REF [BASE]` on the Mac. It lists the takes that would
pick other sounds and the sounds whose role, tempo, key or form would
change. Changed picks void Dean's verdicts on those takes, so say so in the
PR. A change to the name rules bumps `names.VERSION`, and a change to what
is read from inside files bumps `embedded.VERSION`: either makes the next
scan relabel every sound.
