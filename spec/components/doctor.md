# Doctor and installers

**Status:** built

Implementation: `src/mph/doctor.py`, `src/mph/components.py`.

### REQ-DOC-01: Read-only diagnosis
`mph doctor` reports on data dir writability, each source's readability,
every registered component (found, version, location), and secret presence.
It changes nothing.
- **Why:** it must be safe to run anytime, including in CI and before a render.
- **Verify:** test

### REQ-DOC-02: Every problem comes with a fix
Each non-ok finding carries a concrete fix: a command (`mph install x`), a
config change, or a URL.
- **Why:** the report doubles as a to-do list, with no guesswork.
- **Verify:** test

### REQ-DOC-03: Required vs optional
Missing required components or failed version minimums are failures (exit
code 1). Missing optional ones are warnings (exit code 0).
- **Why:** `mph doctor` works as a pre-flight gate without nagging about
  optional extras like UVR.
- **Verify:** test

### REQ-DOC-04: Scope by stage
`--stage` limits checks to one or more stages (core, catalogue, synthesis,
vocals, renoise, mastering, ai).
- **Why:** a catalogue scan shouldn't fail because Renoise isn't installed.
- **Verify:** test

### REQ-DOC-05: Machine-readable output
`--json` emits the findings as a JSON array.
- **Why:** the feedback page, CI and agents consume doctor output.
- **Verify:** test

### REQ-DOC-06: Modular, consent-based installers
Each component declares its install steps as data. `mph install <id>` prints
the plan, and `--yes` runs only the automatable steps (Homebrew, uv, ghcup,
cabal, ollama). Manual steps (licensed apps, GUI installers) are printed,
never attempted. Nothing uses sudo.
- **Why:** Dean wants automation where possible and hand-holding elsewhere,
  without surprise system changes.
- **Verify:** test

### REQ-DOC-07: Component registry
The registry covers at least: Homebrew, uv, FFmpeg, SoX, SuperCollider
(>= 3.13), sc3-plugins, Tidal Cycles, Renoise (>= 3.5), Surge XT, Demucs, UVR,
Ollama, GitHub CLI.
- **Why:** these are the tools the architecture depends on. Versions below
  the minimum lack features we use (Renoise 3.5 Lua API, SC 3.13 NRT fixes).
- **Verify:** test

### REQ-DOC-08: Installed means it runs
A program installed with `uv tool install` (Demucs) is checked by starting
it (`demucs --help` must exit cleanly), and its version comes from
`uv tool list`. One that is there but won't start is a warning, or a failure
if required, with the error's last line and `mph install <id>`, which
reinstalls it. An app bundle with no real version (UVR's says 0.0.0) is
reported as found, saying that the app doesn't state its version.
- **Why:** on Dean's Mac (2026-11-11), `demucs` was on PATH but every run
  failed with `ModuleNotFoundError: No module named 'numpy'`, while doctor
  said OK. Demucs 4.1 lists numpy as a dependency only on Intel Macs and
  imports it everywhere, so `uv tool install demucs` leaves it out on Apple
  Silicon. The install step now adds it. Finding a file proves little for a
  Python program; starting it proves its dependencies are there.
- **Verify:** test
