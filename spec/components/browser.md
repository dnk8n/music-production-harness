# Sample browser

**Status:** draft (built alongside step 2)

`mph catalogue browse` opens a local page for listening to the catalogue:
search with the same filters as `mph catalogue search`, play any result, build
a selection and play it in order or layered in sync, and walk a derived
sound's lineage back to its source, playing each step. The step 3 feedback
page (spec/components/feedback.md) reuses its player and server.

### REQ-BRW-01: Local and read-only
The server listens on 127.0.0.1 only and answers only requests addressed to
`127.0.0.1` or `localhost` on its port. Audio is requested by content hash,
never by path, and only for sounds the catalogue knows. Files are opened
read-only.
- **Why:** sources must never be touched (REQ-CAT-01), and a page on the open
  web must not be able to read Dean's library through the browser.
- **Verify:** test

### REQ-BRW-02: Search, play, select, layer, trace
The page offers search (words, role, BPM, key, form, length) with the reasons
for each match; one-click and keyboard playback; a selection that plays in
order or all together from a common start time with per-sound levels, with
optional looping; a list of everything mph has made; and a lineage tree with
a play button on every node. Audio supports byte ranges so seeking works.
- **Why:** choosing samples is done by ear, and layering candidates is how you
  hear whether they fit.
- **Verify:** test

### REQ-BRW-03: Every catalogued format plays
Formats browsers play natively (WAV, MP3, M4A, FLAC, OGG) are streamed as
stored. Others (AIFF) are decoded by ffmpeg to a float WAV at the native rate,
in memory only, and kept for a few recent sounds so seeking stays quick.
- **Why:** about 230 of Dean's samples are AIFF, which Chrome can't play, and
  REQ-AUD-01 forbids converted copies on disk.
- **Verify:** test
