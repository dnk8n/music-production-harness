# Architecture

**Status:** accepted (shape), draft (details per component)

## Pipeline

```
 Sources (read-only)          data_dir (read-write, rebuildable)
 ┌──────────────────┐        ┌──────────────────────────────────────────────┐
 │ local folders    │──scan─▶│ catalogue.sqlite  (assets, analysis, recipes)│
 │ online archives  │─fetch─▶│ downloads/  cache/  stashes/                 │
 └──────────────────┘        └───────────────┬──────────────────────────────┘
                                             │ search + select
                                             ▼
   spec/tracks/*.md ──▶ score (patterns, arrangement, automation, as data)
                                             │
                     ┌───────────────────────┼────────────────────────┐
                     ▼                       ▼                        ▼
           SuperCollider NRT        vocal chain (Demucs,       sample slicing
           synth stems              trim, align)               and chopping
                     └───────────────────────┼────────────────────────┘
                                             ▼ stems, 32-bit float, production rate
                                  Renoise song (.xrns) generated from the score,
                                  with plug-ins and keyboard mappings
                                             │  render (Lua/OSC driven)
                                             ▼
                                  master stem, 32-bit float, production rate
                                             │  delivery: one SRC + one dither
                                             ▼
                                  track.aiff  48 kHz / 24-bit  + QA report
```

## Components

| Component | Responsibility | Spec | Plan step |
|---|---|---|---|
| config | Layered, validated, immutable settings | [config.md](components/config.md) | 1 |
| secrets | Keychain access, nothing on disk | [secrets.md](components/secrets.md) | 1 |
| doctor + installers | Diagnose state, guide installs | [doctor.md](components/doctor.md) | 1 |
| cli | `mph` entry point | [cli.md](components/cli.md) | 1 |
| catalogue | Index sources, analyse, search, track provenance | [catalogue.md](components/catalogue.md) | 2 |
| audio policy | Formats, rates, dither, determinism | [audio-policy.md](components/audio-policy.md) | 1 (rules), 3 (enforcement) |
| benchmarks | Listening tests and measurements | [benchmarks.md](components/benchmarks.md) | 3 |
| feedback | Local web page, verdicts to issues, AI loop | [feedback.md](components/feedback.md) | 3 |
| renoise bridge | Generate, control and render Renoise songs | [renoise.md](components/renoise.md) | 4 |
| retention | Keep fresh renders, prune the rest, render again on demand | [retention.md](components/retention.md) | 3 |
| mastering | Loudness, true peak, delivery QA | [mastering.md](components/mastering.md) | 4 |
| query channel | Read-only tools through which Claude asks about the catalogue, benchmarks, renders and health | [query-channel.md](components/query-channel.md) | 3 |

## Boundaries

- **Python** (`src/mph`) orchestrates: config, catalogue, analysis, score
  data, file generation, QA. It never synthesises sound itself.
- **SuperCollider** synthesises in non-realtime (NRT) mode from score data,
  which makes renders deterministic and faster than realtime (ADR-0002).
- **Renoise** is the last creative tool: arrangement, mixing, live control,
  and the render of the master. Python generates `.xrns` files (zipped XML)
  and drives a running Renoise through its Lua scripting API and OSC.
- **SoX/libsoxr** does exactly one job: the delivery SRC and dither (ADR-0003).
- **External processes** are only spawned through `mph.proc.Runner`, so every
  component is testable without the real tools (REQ-ENG-03).
- **Claude** reads what mph knows through the query channel, a local MCP
  server over stdio that can only read (ADR-0011).

## Data ownership

| Data | Owner | Location | Rebuildable? |
|---|---|---|---|
| Source audio | Dean / archives | source paths, URLs | n/a, never touched |
| Catalogue DB | catalogue | `data_dir/catalogue.sqlite` | yes, from sources |
| Downloads | catalogue | `data_dir/downloads/` | yes, from URLs (while online) |
| Cache | any stage | `data_dir/cache/<hash>` | yes, from recipes |
| Stashes | Dean | `data_dir/stashes/` | yes, from recipes |
| Renders + QA reports | benchmarks, tracks | `data_dir/renders/` | yes |
| Spec, code, tests | repo | git | spec is the root |
| Secrets | Dean | macOS Keychain | re-enter |

## Quality attributes

- **Determinism:** fixed seeds, pinned tool versions in the recipe hash, NRT
  rendering. See REQ-AUD-05.
- **Bounded resources:** each render declares its maximum duration, voices and
  stems up front, and fails fast if exceeded. This fits a 16 GB M1 Pro.
- **Observability:** `mph status` and `mph doctor` explain current state, and
  every render writes a JSON QA report next to the audio.
- **Security:** no secrets in the repo or logs. Subprocesses get argv lists,
  never shell strings. Dependencies are audited in CI. See [engineering.md](process/engineering.md).
