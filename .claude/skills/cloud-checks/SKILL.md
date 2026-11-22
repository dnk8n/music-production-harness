---
name: cloud-checks
description: Change and check this repo in a cloud (Linux) copy, before anything runs on Dean's Mac. Covers setting up the sandbox, real SuperCollider without a display, which tests run where, the spec gate's traps, and making pictures for the docs.
---

# Check changes in a cloud copy

Code is changed and checked in a cloud copy of the repo, on its own branch
cut from the latest `main` (REQ-PRC-06, CLAUDE.md rule 4). Only what needs
Renoise, Demucs, the Keychain or Dean's samples waits for Dean's Mac (the
`mac-work` skill).

## Set up once per sandbox

```sh
scripts/cloud-check --setup        # as root (sudo if you aren't): apt installs
                                   # SuperCollider, LuaJIT, SoX, FFmpeg; then every check
uv run pre-commit install          # the git hooks
```

`scripts/cloud-check` runs every pre-commit hook, then every test but the
macOS ones, real SuperCollider included (about 4 minutes). CI runs the same
hooks but leaves out the tests marked `tools`.

sclang is a Qt app. With no display, and as root, it needs these, which the
script sets; export them yourself before running `tools` tests by hand or
`git push` (the pre-push hook runs the tests):

```sh
export QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--no-sandbox \
  QTWEBENGINE_DISABLE_SANDBOX=1 XDG_RUNTIME_DIR=/tmp/runtime-$(id -un)
mkdir -p "$XDG_RUNTIME_DIR"
```

## While you work

- Run what you changed first, then the whole suite once:
  `uv run pytest -q tests/bench/test_projects.py`, then `scripts/cloud-check`.
  After a failure, re-run only the failing tests until they pass.
- `uv run mph spec check` before every commit. Two traps:
  - every ID it sees must exist in the spec, in code, tests, docs, example
    projects, skills and scripts alike. A made-up benchmark id in a test or
    a doc must end in `-00` (`BM-OWN-00`), which marks it as a placeholder;
  - a requirement in a `built` spec file with `Verify: test` needs a test
    marked `@pytest.mark.req("REQ-...")`.
- Unit tests use the fakes in `tests/fakes.py`: no real apps, Keychain or
  network. The sandbox can't reach Freesound or the Internet Archive anyway.
- A test that changes which projects are on gets them put back by the
  autouse fixture in `tests/conftest.py`.
- Lua tests prefer LuaJIT, Renoise's own Lua, and skip without it.
- A score made only of synths (the free remix, BM-LTFS-01 to 05) renders
  here for real: `uv run mph bench render BM-ID --engine supercollider`.
  With no config it uses built-in defaults and a data folder under
  `$HOME/Library/Application Support/mph`; point `HOME` at your scratchpad to
  keep it there. A block takes 1 to 2 minutes and the 8-minute whole track
  about 5. If one takes far longer, `uv run --with py-spy py-spy dump --pid
  PID` shows where: per-hit work on a whole stem is the usual cause (the
  choke check took 17 minutes of a whole track until it cut before mixing
  to mono).

## Before pushing

- Nothing binary but small images in `docs/images/` (REQ-ENG-10), no audio,
  no Renoise songs, no secrets, local config or catalogue (CLAUDE.md rule 13).
  The `no-audio` and `no-binaries` hooks refuse them; when they print
  "(no files to check) Skipped", nothing matched, which is the pass.
- Changing a file the render gate hashes (`rerender.CODE`, such as
  `bench/score.py`) makes every unresolved or unrated take render once more
  on the next `render --all`. Say so in the PR, with how long the big ones
  take (docs/reference-mac.md).
- Then the Mac: `scripts/mac-check origin/<branch>` for anything that touches
  renders, the Renoise bridge or the Keychain, and `scripts/renoise-check`
  for the bridge (the `mac-work` skill).

## Pictures for the docs

Only for the docs, only in `docs/images/`, under 500 KB, and never with
private details (paths, names of Dean's files, ratings text). Make them from
demo data in the sandbox:

1. Point `MPH_CONFIG` at a throwaway config whose `data_dir` is in a scratch
   folder, and render or fake what the page should show.
2. `uv run mph bench serve --no-open --port 8765`, then screenshot with
   Playwright at `device_scale_factor=2`.
3. Shrink: `convert in.png -resize 50% +dither -colors 128 PNG8:out.png`
   (ImageMagick; `+dither` keeps flat colours flat).
