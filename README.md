# Music Production Harness

A spec-driven, deterministic music production system. You describe the music
and the system in `spec/`. The code builds a sample catalogue, synthesises
parts with SuperCollider, assembles an editable Renoise song you can perform
live, and delivers a mastered 48 kHz / 24-bit AIFF. There are no neural
music generators: every sound traces back to a sample, a synth patch or a
pattern.

**Start with [spec/README.md](spec/README.md).** The spec is the product, and
the code is regenerable from it. New here?
[docs/getting-started.md](docs/getting-started.md) goes from a fresh Mac to a
demo of Dean's example projects, and [docs/README.md](docs/README.md) maps the
rest: the reference Mac, the history, the roadmap and Dean's preferences.

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

[projects]
examples = true                  # Dean's example projects: true, false, or a list
folders = []                     # folders of your own projects
```

[config/example.toml](config/example.toml) is a full one to copy.

Index your samples (read-only; the source folder is never written to):

```sh
uv run mph catalogue scan
uv run mph catalogue stats
```

Benchmarks live in projects: folders of scores judged together
(spec/components/projects.md). Dean's ship with the repo as text in
[examples/projects/](examples/projects/), each with a README of what Dean
heard and decided; turn them off for a clean slate, or copy one to make it
your own. Where your samples lack a sound a score needs, `fill` downloads a
free placeholder (CC0, public domain or CC-BY):

```sh
uv run mph projects list                 # every project, on or off
uv run mph projects show sidechain       # what it is and needs
uv run mph projects fill                 # what your catalogue lacks; --yes downloads
uv run mph projects copy drum-patterns --to ~/Music/mph-projects
```

Render the benchmarks. Each one becomes a small Renoise song, with one track
per part, that you can open and change by hand (ADR-0009). Install the bridge
tool once, then restart Renoise. Synth sounds come from SuperCollider
(`mph install supercollider`). Without Renoise, benchmarks render in
SuperCollider alone. Each render is measured (timing to the millisecond,
swing, chokes, a second render that must match bit for bit) and delivered as
a 48 kHz / 24-bit AIFF:

```sh
uv run mph renoise install # once; then restart Renoise
uv run mph bench list
uv run mph bench render BM-DRUM-02                       # every take of it with something new
uv run mph bench render --all                            # leaves resolved takes alone
uv run mph bench render BM-DRUM-01 --variant synth-kit   # just one take
uv run mph bench render BM-DRUM-01 --force-rerender      # even resolved takes
uv run mph bench serve     # listen, compare, rate, add ideas, open in Renoise
```

Renders are deterministic, so mph keeps only the newest of each benchmark
variant and makes older ones again when needed (spec/components/retention.md).
You rate each take 0 to 5 stars on the listening page (3 or more is a pass),
and mark it resolved or unresolved, with feedback (REQ-FBK-02, REQ-FBK-06).
A resolved take renders again only with `--force-rerender`. An unresolved
one renders again once its score, the render code or its sounds change, and
agents find it through the `feedback` tool. A take rated a pass keeps the
sounds you heard (REQ-BEN-14, 15). You can also add ideas for new projects,
benchmarks and variants there (REQ-FBK-08). The listening page renders a
pruned take when you play it.

```sh
uv run mph prune           # what can be freed; deletes nothing
uv run mph prune --list    # every item, and why it can go
uv run mph prune --yes     # delete it
```

Store third-party credentials in the macOS Keychain, never in files. Setup asks
for every field and says where to find it:

```sh
uv run mph secrets list
uv run mph secrets set freesound   # client ID + client secret
uv run mph secrets set gemini      # API key
```

Let Claude look things up for you: one command gives every Claude Code
session on this Mac read-only tools for the catalogue, benchmarks, renders,
your ratings and ideas, and health (spec/components/query-channel.md). Claude can then
answer questions like "which kicks have I never used in a render?" without
being able to change anything.

```sh
uv run mph mcp install         # once; `claude mcp list` then shows mph connected
uv run mph mcp call overview   # see what Claude sees
uv run mph mcp call search_sounds '{"roles": ["kick"], "limit": 5}'
```

## Plan

| Step | Outcome | Status |
|---|---|---|
| 1 | Spec, config, Keychain secrets, doctor and installers, CI | done |
| 2 | Sample catalogue and search engine | done |
| 3 | Benchmark kit and listening/feedback page | most benchmarks built |
| 4 | Renoise bridge and mastering | bridge built for drums and arranged tracks; mastering to go |

Once Dean signs off the benchmarks, the first track gets its own spec. The
order of the next work, and why, is in [docs/roadmap.md](docs/roadmap.md).

## Development

```sh
uv sync && uv run pre-commit install   # once per checkout
uv run pre-commit run --all-files      # format, lint, types, spec gate
uv run pytest --cov=mph                # also runs before each push
scripts/cloud-check                    # on Linux: every hook and test but the macOS ones
scripts/mac-check [origin/main]        # on the Mac: doctor + every test, real tools
scripts/renoise-check [origin/main]    # on the Mac: the bridge in the real Renoise
```

Only text goes in git, plus small images for the docs (REQ-ENG-10): sounds,
renders and Renoise songs stay in mph's data folder. Agents follow
[CLAUDE.md](CLAUDE.md) and the skills in [.claude/skills/](.claude/skills/).

Work is tracked in GitHub issues with `type:`, `area:` and `step:` labels.
See [spec/process/agent-workflow.md](spec/process/agent-workflow.md).
