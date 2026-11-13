# Agent instructions

Rules for any agent working in this repo. The spec has the detail; this file
is the short version so every new session starts with the same rules.

## How we work

1. **Spec first.** `spec/` is the source of truth (ADR-0001). Behaviour
   changes start as a spec change with a Why and a Verify method. Run
   `uv run mph spec check` before every commit.
2. **Cite IDs.** Module docstrings name their spec file. Tests link
   requirements with `@pytest.mark.req("REQ-...")`. PR descriptions list the
   IDs they implement, and the ids of Dean's ratings they answer: the
   `feedback` tool lists his unresolved takes and open ideas (REQ-QRY-12).
3. **Where work runs (REQ-PRC-06).** Code is changed and checked in a cloud
   copy of the repo, on its own branch. Anything that needs Renoise, demucs,
   real SuperCollider renders, the Keychain or Dean's samples runs on Dean's
   Mac over Remote Control, in `/Users/dean/src/music-production-harness`,
   after the PR is pushed. To look something up there (sounds, labels,
   benchmarks, ratings, ideas, health), use the read-only `mph` MCP tools, or
   `uv run mph mcp call TOOL` where they aren't loaded
   (spec/components/query-channel.md).
4. **One branch per PR, cut from the latest `main`.** Don't stack PRs. Before
   merging, update the branch with `main` and let CI pass again (REQ-PRC-05).
5. **Track work in GitHub issues** with labels from `.github/labels.yml`:
   one `type:` (`spec`, `dev`, `qa`, `infra`, ...), one `area:`, and a
   `step:` where relevant. PRs follow `.github/pull_request_template.md`.

## Checks

6. **Set up hooks once per checkout:** `uv sync && uv run pre-commit install`.
   Commits then run format, lint, types and the spec gate; pushes also run
   the tests. CI runs the same `.pre-commit-config.yaml` (REQ-ENG-07).
   To run everything by hand: `uv run pre-commit run --all-files && uv run pytest --cov=mph`.
7. **CI pins runner images** (`ubuntu-26.04`, not `ubuntu-latest`) and pins
   actions to commit SHAs. Leave no warnings or annotations in a run.
8. **Seams.** Spawn processes only via `mph.proc.Runner`. Unit tests use the
   fakes in `tests/fakes.py` and never touch real apps, the Keychain or the
   network. Tests that need real tools are marked `tools` or `macos`;
   `scripts/mac-check [REF]` runs everything, those included, on Dean's Mac
   (REQ-ENG-08). Ask for (or run, over Remote Control) a Mac check before
   merging anything that touches renders, the Renoise bridge or the Keychain;
   for the bridge, also `scripts/renoise-check [REF]` (real Renoise).

## Data, audio and secrets

9. **Sources are read-only.** Never write under a configured source path.
   Everything derived goes to `data_dir`.
10. **Audio is converted once** (ADR-0003, `spec/components/audio-policy.md`):
    samples and vocals stay at their native rate and depth, synth parts may
    render at a higher rate, and the single conversion happens at final
    export to 48 kHz / 24-bit AIFF. No round trips between formats.
11. **Keep only fresh renders** (ADR-0010, `spec/components/retention.md`).
    Renders are deterministic and can be made again. New outputs go inside
    the render folder (intermediates in `work/`) so `mph prune` covers them.
12. **Secrets live in the macOS Keychain only** (ADR-0005), through
    `mph secrets`. Credential setup asks for every field a service needs and
    says where to find each one.
13. **Never commit** secrets, audio files, local config or the catalogue DB.

## Writing

14. Write for Dean: plain words in user-facing text, and no gendered pronouns
    for people whose pronouns aren't stated.
