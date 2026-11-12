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
CI runs everything that doesn't need macOS apps, on a pinned runner image
(never a moving `-latest` label). Tests that need real tools are marked
`tools` (real SuperCollider) or `macos` (Keychain, app bundles) and run on
macOS (REQ-ENG-08).
- **Why:** free CI minutes, and the hardware-specific checks run where the
  hardware is. A pinned image changes only when we choose to move it, so a
  runner migration can't break or warn on an unrelated PR.
- **Verify:** inspect

### REQ-ENG-07: Same checks locally and in CI
`.pre-commit-config.yaml` is the one list of fast checks: file hygiene, no
audio files, no private keys, `ruff format`, `ruff check`, `mypy` and
`mph spec check` before each commit, and the Linux test suite before each
push. Tool versions come from `uv.lock`. CI runs the same file with
`pre-commit run --all-files`, so a check can't pass locally and fail in CI.
Set up once per checkout with `uv run pre-commit install`.
- **Why:** problems show up seconds after they are made instead of minutes
  later in CI, and the two lists can't drift apart.
- **Verify:** inspect

### REQ-ENG-08: Full check on the Mac, on demand
`scripts/mac-check [REF]` runs on Dean's Mac when Dean (or an agent over
Remote Control) starts it: `mph doctor`, the pre-commit hooks, and every
test with no marker deselected, against the apps installed on that Mac.
A skipped test fails the check, because it means something a real run
needs is missing (`--allow-skips` to accept that knowingly). With a REF it
checks that branch or commit in a temporary worktree and leaves the
checkout alone. The log goes to `~/Library/Logs/mph/`. No schedule, no
self-hosted runner, no secrets in the repo or the log.

`scripts/kit-diff REF [BASE]` checks a change against Dean's own library:
the sounds every benchmark take would pick, and the sounds whose roles
would change, on REF compared with BASE (`origin/main` by default). Each
side scans its own copy of the catalogue in a temporary worktree, so Dean's
catalogue isn't touched. Run it before merging a change to naming rules,
search or kit choice.
- **Why:** Linux CI can't see macOS-only breakage or the real tools. A
  self-hosted runner on a public repo would let anyone's PR run code on the
  Mac, and a schedule would run when nobody is looking; on demand fits how
  Dean works. Tests use made-up libraries, so only Dean's library shows
  whether a change would swap a sound his verdicts were given on
  (2026-11-12).
- **Verify:** inspect (run it on the Mac)
