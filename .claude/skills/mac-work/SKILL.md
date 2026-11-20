---
name: mac-work
description: Run checks, renders, Renoise, Demucs or the catalogue on Dean's Mac over Remote Control for this repo. Covers the scripts, worktrees, disk space, what may be changed there, and how to report back.
---

# Work on Dean's Mac

Code is changed and checked in a cloud copy of the repo, on its own branch
(REQ-PRC-06). Anything that needs Renoise, Demucs, SuperCollider with Dean's
plugins, the Keychain or Dean's samples runs on Dean's Mac over Remote Control, after
the branch is pushed. To Dean, Remote Control is "a Claude Code session on your
Mac, started from the thread".

## Where things are

- **Dean's checkout**, `/Users/dean/src/music-production-harness`, is Dean's. Don't
  change its branch, pull into it or run `mph renoise install` from it without
  Dean's go-ahead in the thread, naming the action. Read-only commands from it are
  fine. Its own `mph` serves the listening page and the MCP tools.
- **mph's data folder**, `~/Library/Application Support/mph`, holds the renders,
  the catalogue DB (with Dean's ratings and ideas), the cache (stems included),
  and Renoise's jobs and scratch songs. Dean gave standing permission
  (2026-11-14) to manage anything in it, with judgement. Keep the catalogue DB,
  every finished render (it has a `report.json`) and the cached stems. Never run
  `mph prune --yes`: it also clears caches, and stems take about 9 minutes of
  Demucs to make again. Delete an unfinished render folder (no `report.json`) by
  hand.
- **Sources** (Dean's sample folders) are read-only (REQ-CAT-01).
- **Logs** go to `~/Library/Logs/mph/`. `scripts/mac-check` writes there; save
  any other long output there too, and send its path.

## Running a branch without touching the checkout

- A temporary worktree: `git -C <checkout> worktree add --detach /tmp/mph-<name>
  origin/<branch>`, then `uv sync` in it. To move it on: `git fetch origin
  <branch>`, then `git checkout --detach FETCH_HEAD`. A restart empties `/tmp`,
  so recreate it when it's gone.
- The scripts take a ref and make their own temporary worktree:
  - `scripts/mac-check [REF]`: doctor, every pre-commit hook, then every test,
    those marked `tools` and `macos` too. Any skip fails it (REQ-ENG-08). It
    takes about 5 minutes. Run it before merging anything that touches
    renders, the Renoise bridge or the Keychain.
  - `scripts/renoise-check [REF]`: the bridge tool in real Renoise, which must be
    closed first. Run it before merging any change under `src/mph/renoise/`.
    Even when a step fails it asks the Renoise it started to quit, and it
    puts back the bridge tool that was installed before. Send its output
    back word for word, apart from the per-parameter device rows when
    nothing failed: the device report lists what Renoise really calls and
    shows, and the duck lines show how Renoise applies automation (the
    renoise-bridge skill says what to do with both).
  - `scripts/kit-diff REF [BASE]`: which takes would pick other sounds, and which
    sounds would change role, tempo, key or form. Run it before merging any
    change to naming rules, search or kit choice, because changed picks void
    Dean's verdicts.
- The Lua tests need `luajit` (`brew install luajit`).

## Rendering

- `uv run mph bench render BM-ID ... [--engine renoise|supercollider] [--variant
  TAKE] [--force-rerender]`, from the checkout or worktree whose code should
  render. A passed take with nothing changed is skipped (REQ-BEN-14).
- A Renoise take needs that code's bridge tool. Close Renoise, run `uv run mph
  renoise install` from the worktree, render, then put back the tool Dean's
  checkout uses (run `uv run mph renoise install` from that checkout), as
  `scripts/renoise-check` does. mph refuses to render while a Renoise it didn't
  start is open. It's fine to leave mph's own Renoise open after a render.
- **Disk.** The Mac's disk is nearly full (about 98% in November 2026, with
  around 8 GiB free). Check `df -h ~` before any long render or mac-check, and
  stop and tell Dean the numbers if a job won't fit. Sizes at 96 kHz float:
  - a block of about a minute peaks under 1.1 GB;
  - a whole 8-minute track keeps about 0.37 GB per stem plus the master, and
    a SuperCollider take peaks near twice that, with three 10-channel work
    files;
  - estimate from the newest render folder of the same benchmark.
- A mac-check of a branch older than PR #62 leaves about 2.5 GB in
  `$TMPDIR/pytest-of-dean`; clear it afterwards.

## Remote Control habits

- A session spends Claude usage while it waits, and a long tool can stall when
  the account hits its limit. Keep each step short, run long tools in the
  background, and report after each step.
- Send results word for word where the details matter (failure messages,
  device reports, measurement names), with log paths. Don't summarise the
  evidence away.
- Messages to a running session go through the thread's Remote Control
  instructions, which also wake a session that is waiting; its notes come
  back as messages. Start only one session per thread.
- Give each request its own gate: what to run, what to send back word for
  word, and what to do on a pass and on a fail (for example: render only if
  the check passes, otherwise stop with nothing rendered).
