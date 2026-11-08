# Music Production Harness

A spec-driven, deterministic music production system. You describe the music
and the system in `spec/`. The code builds a sample catalogue, synthesises
parts with SuperCollider, assembles an editable Renoise song you can perform
live, and delivers a mastered 48 kHz / 24-bit AIFF. There are no neural
music generators: every sound traces back to a sample, a synth patch or a
pattern.

**Start with [spec/README.md](spec/README.md).** The spec is the product, and
the code is regenerable from it.

## Quick start (macOS, Apple Silicon)

```sh
brew install uv                  # if you don't have it
uv sync                          # creates .venv with dev tools
uv run mph status                # one-screen overview
uv run mph doctor                # what's installed, what's missing, how to fix it
uv run mph install supercollider # print the install plan (add --yes to run it)
```

Point the harness at your samples in `~/.config/mph/config.toml`:

```toml
[[sources]]
id = "dj-samples"
kind = "local"
path = "/Users/you/dj/samples"   # read-only: never written to
license = "commercial-pack"
```

Store third-party credentials in the macOS Keychain, never in files. Setup asks
for every field and says where to find it:

```sh
uv run mph secrets list
uv run mph secrets set freesound   # client ID + client secret
uv run mph secrets set gemini      # API key
```

## Plan

| Step | Outcome | Status |
|---|---|---|
| 1 | Spec, config, Keychain secrets, doctor and installers, CI | this PR |
| 2 | Sample catalogue and search engine | next |
| 3 | Benchmark kit and listening/feedback page | |
| 4 | Renoise bridge and mastering | |

Once Dean signs off the benchmarks, the first track gets its own spec.

## Development

```sh
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest --cov=mph
uv run mph spec check
```

Work is tracked in GitHub issues with `type:`, `area:` and `step:` labels.
See [spec/process/agent-workflow.md](spec/process/agent-workflow.md).
