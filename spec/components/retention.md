# Retention

**Status:** built

Renders are deterministic (REQ-AUD-05, BM-MST-02), so a render on disk is a
convenience, not a record. mph keeps what is fresh and makes the rest again
when it is needed (ADR-0010). The record is the report, the score and the
verdicts, which are small and always kept.
Implementation: `src/mph/retention.py`, `src/mph/bench/jit.py`.

What mph may delete is only what it can make again, and only under
`data_dir`: render audio, render work files, cached derivatives and Renoise
job files. It never deletes sources, stashes, downloads, the catalogue DB,
compiled synthdefs, a render's `report.json`, or anything outside `data_dir`.

### REQ-RET-01: Fresh renders
A render is fresh when it is one of the newest `retention.keep_renders`
(default 1) renders of its benchmark variant, by `rendered_at`. That is the
render the listening page plays. Every other render is superseded. With
`retention.prune_after_render` (default true), a finished render prunes the
superseded renders of its own benchmark variant straight away, and never
prunes itself, whatever `keep_renders` says.
- **Why:** a 96 kHz float render with stems is hundreds of megabytes, and
  every tweak of a score makes a new one. The page only ever plays the newest
  of each variant, so older ones fill the disk without being heard.
- **Verify:** test

### REQ-RET-02: A pruned render keeps its report
Pruning a render deletes everything in its folder except `report.json`. The
report keeps its measurements, kit, levels and verdict link, and the page
marks it as "audio pruned" (`on_disk` false).
- **Why:** verdicts point at a score hash (REQ-FBK-02), and what was measured
  and heard stays readable. The report is a few kilobytes.
- **Verify:** test

### REQ-RET-03: Work files go when a render finishes
A render's `work/` folder (repeat renders, templates, warm-up, solo
renders) is deleted as soon as its report is written, unless
`retention.keep_work` is true.
- **Why:** they are only needed to measure the render, and they are the
  biggest part of it: two full renders and a solo render per voice.
- **Verify:** test

### REQ-RET-04: Unused cache entries can go
A cached derivative (`cache/`, REQ-CAT-06) that no fresh render's kit uses
can be pruned, because `mph catalogue refresh` rebuilds it byte for byte.
One a fresh render uses is kept, so rendering it again picks the same sound.
Stashes are never pruned.
- **Why:** the kit picker only uses sounds that are on disk (REQ-BEN-05),
  so deleting a derivative a render used would quietly change its kit and
  its score hash.
- **Verify:** test

### REQ-RET-05: `mph prune` shows before it deletes
`mph prune` lists what it would delete, grouped by kind with sizes and a
total, and deletes nothing. `--list` names every item and why it can go.
`--yes` deletes exactly what the list shows. `--keep N` overrides
`retention.keep_renders` for this run; `--keep 0` prunes every render's audio
and relies on REQ-RET-06. It also deletes renders that never finished (no
`report.json`) and leftover Renoise job files. It leaves alone anything
changed in the last hour, so a render in progress is safe. It never follows a
link out of `data_dir`, and refuses to delete a path outside the folders it owns.
- **Why:** deleting is the one step that can't be undone, so Dean sees the
  list first. The guards make a mistake in the list harmless.
- **Verify:** test

### REQ-RET-06: The page renders on demand
On the listening page (`mph bench serve`), a take whose audio was pruned, or a
benchmark or variant that was never rendered, has a "Render" button.
Playing a pruned take renders it first, then plays it. One render runs at a
time in the background, and the page shows its progress and any error. The
render is the current score's, so a take pruned because its score changed
comes back as the current version.
- **Why:** with only fresh renders on disk, revisiting something must not need
  the terminal. Deterministic rendering makes the new audio the same as the
  old whenever the score is the same.
- **Verify:** test

### REQ-RET-07: Status shows disk use
`mph status` shows how much space renders use and how much `mph prune`
would free.
- **Why:** Dean sees the cost of keeping renders without hunting for it.
- **Verify:** test
