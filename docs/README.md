# Docs

What the spec doesn't say: how to get going, the Mac it all runs on, how the
project got here, what comes next, and the research behind it. The spec in
[spec/](../spec/README.md) is still the source of truth for how mph behaves;
where a page here and the spec differ, the spec wins.

| Page | For |
|---|---|
| [getting-started.md](getting-started.md) | a fresh Mac to a demo of Dean's projects, then projects of your own |
| [reference-mac.md](reference-mac.md) | Dean's Mac, tools and sample library, and how long renders take |
| [history.md](history.md) | what was built, in what order, and what Dean decided along the way |
| [roadmap.md](roadmap.md) | the order of the next work, and why |
| [preferences.md](preferences.md) | Dean's standing choices, and where each one is written down |
| [research/love-train-remix-practice.md](research/love-train-remix-practice.md) | how remixes are made, and a plan for six Love Train remixes |
| [images/](images/) | the screenshots these pages show |

## Where everything is

| Folder | Holds |
|---|---|
| [spec/](../spec/README.md) | what mph does and why: components, decisions (ADRs), process, tracks |
| [examples/projects/](../examples/projects/) | Dean's projects: scores, `project.toml`, and a README of what Dean thought |
| [examples/config/](../examples/config/) | Dean's config, as an example |
| [config/example.toml](../config/example.toml) | a config to start yours from |
| [src/mph/](../src/mph/) | the code |
| [tests/](../tests/) | the tests, each linked to its requirement |
| [scripts/](../scripts/) | checks that run whole: `mac-check`, `renoise-check`, `kit-diff`, `cloud-check` |
| [.claude/skills/](../.claude/skills/) | how agents do recurring work: `cloud-checks`, `mac-work`, `renoise-bridge`, `pick-sounds`, `remix` |
| [CLAUDE.md](../CLAUDE.md) | the short rules every agent follows |

Nothing binary is kept in git except the images here (REQ-ENG-10). Sounds,
renders, Renoise songs and the catalogue live in mph's data folder on the
Mac, and can be made again from the sources and the scores; ratings and
ideas live in the catalogue, so back it up.

## If you're not Dean

The repo is Dean's, so the docs and skills say "Dean" for the person whose
ear decides and whose Mac runs mph. On your own Mac that is you: your
ratings, your samples, your go-ahead. Dean's projects stay as examples you
can learn from, render with your own sounds, copy, or turn off.
