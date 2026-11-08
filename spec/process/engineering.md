# Engineering standards

**Status:** accepted

### REQ-ENG-01: Toolchain
Python 3.12+, managed with `uv`. Linting and formatting with `ruff`
(including bandit-style security rules and a complexity cap of 8),
`mypy --strict`, `pytest` with coverage of at least 90%.
- **Why:** fast, mainstream and strict tools, which a senior engineer can
  pick up without ceremony.
- **Verify:** inspect (enforced by CI)

### REQ-ENG-02: Small, cohesive modules
One responsibility per module, no import cycles, functions under the
complexity cap. CI enforces complexity, and an import-cycle check is added
when the package outgrows a handful of modules.
- **Why:** throwaway code still has to be easy to read and review.
- **Verify:** inspect

### REQ-ENG-03: Side effects behind seams
Processes, the filesystem outside `data_dir`, network and clocks are
reached through injected interfaces (`Runner`, `Host`, ...). Unit tests never
touch the real system.
- **Why:** tests are fast and deterministic, and they run on Linux CI without
  macOS apps.
- **Verify:** test

### REQ-ENG-04: Security baseline
No secrets in the repo (secret scanning in CI). No shell strings in
subprocesses. Dependencies are audited (`pip-audit`), GitHub Actions are
pinned to commit SHAs with least-privilege `permissions`, and Dependabot is enabled.
- **Why:** standard practice, and this repo will hold API integrations.
- **Verify:** inspect

### REQ-ENG-05: Zero runtime dependencies until earned
The core uses the standard library only. A third-party runtime dependency
needs a line in the relevant spec or ADR saying why.
- **Why:** smaller attack surface and easier regeneration. Audio analysis
  libraries will earn their place in step 2.
- **Verify:** inspect

### REQ-ENG-06: CI on Linux, real tools on macOS
CI runs everything that doesn't need macOS apps. Tests needing real tools
are marked `macos` and run locally (`uv run pytest -m macos`).
- **Why:** free CI minutes, and the hardware-specific checks run where the
  hardware is.
- **Verify:** inspect
