# Projects

**Status:** built

A project is a folder of scores that are judged together: the benchmarks of
one kind (drum patterns, a sidechain test) or the blocks of one track (a
remix). Dean's projects ship with the repo as examples. Anyone who clones it
can turn them on or off, copy one to make it their own, and keep projects of
their own beside them.
Implementation: `src/mph/bench/projects.py` (finding projects),
`src/mph/bench/fill.py` (placeholder sounds) and
`src/mph/bench/project_commands.py` (`mph projects`).

```
examples/projects/<project id>/   Dean's projects, in the repo (text only)
  project.toml                    name, what it is, its spec, what it needs
  README.md                       what Dean thought of it, and how to hear it
  BM-....toml                     its scores (REQ-BEN-04, spec/components/tracks.md)
<your folder>/<project id>/       your own, in [projects].folders
```

### REQ-PRJ-01: A project is a folder
A project is a folder that holds a `project.toml` and the score of each of
its benchmarks, one `<benchmark id>.toml` each (REQ-BEN-04). `project.toml`
gives the project's `name`, which the listening page groups by (REQ-BEN-11),
and may say what the project is (`about`), where its benchmarks are
described (`spec`) and what it needs that the repo can't hold (`needs`). The
folder's name is the project's id. A score may leave out its `project`, and
then it belongs to its folder's. A score that names another project is
refused, and so is a benchmark id found in two projects of the same kind
(two examples, or two of the user's own).
- **Why:** a folder per project is something a person can read, copy,
  share and turn on or off without mph, and a name given in one place can't
  disagree with itself.
- **Verify:** test

### REQ-PRJ-02: Dean's projects are examples
Every project Dean and the agents made lives in `examples/projects/`, in a
folder named by its project id: lower-case words joined by hyphens, as
REQ-FBK-08 makes them. Each spec section that describes benchmarks has an
example project of the same name, even before any of its scores exist, so a
planned benchmark already has a place. Only text goes there: scores, project
files and notes. Renders, Renoise songs and sounds stay in `data_dir`, and a
score names a sound it pins by its hash (REQ-BEN-16), never by a copy.
- **Why:** Dean, 2026-11-21: anyone who clones the repo should be able to
  learn from Dean's projects and render a demo of them, and no binary render
  may reach git.
- **Verify:** test

### REQ-PRJ-03: Inherited projects can be turned off
`[projects]` in the config says which example projects are on: `examples =
true` (all of them, the default), `false` (none, a clean slate) or a list of
project ids. `folders` lists folders that hold the user's own projects.
mph lists, renders, serves and answers about only the projects that are on,
and shows the spec's planned benchmarks only for example projects that are
on. A project of the user's own that has a benchmark id an example also has
is used instead of that example. `mph projects list` shows every project,
on or off, with its benchmarks and what it needs.
- **Why:** Dean, 2026-11-21: a new user can start from a clean, personal
  slate, or look through Dean's projects and reuse them.
- **Verify:** test

### REQ-PRJ-04: Reuse a project by copying it
`mph projects copy <project> [--to FOLDER]` copies a project's text files
into a folder of the user's own: `--to`, or else the first of
`[projects].folders`. It never overwrites a file, never writes inside a
source (REQ-CAT-01), and says how to keep the copy and the example apart.
- **Why:** reuse starts from a copy the user owns and can change, while the
  example stays as Dean left it.
- **Verify:** test

### REQ-PRJ-05: Placeholder sounds from free sources
`mph projects fill [project ...]` lists each sound the projects that are on
need and the catalogue can't give (REQ-BEN-05): every role and form a voice
asks for, what it plays meanwhile (a synth, or nothing, so it can't render)
and what mph would look for. With `--yes` it downloads up to `--limit`
sounds for each from a free source: Freesound with the user's keys, or the
Internet Archive. It takes only CC0, public domain and CC-BY sounds
(REQ-CAT-07) of the right length for a one-shot or a loop, labels each with
the role it was found for, analyses them, and says what is still missing.
A voice that plays a recording (a clip, REQ-TRK-02) can't be filled, so the
list names the recording it needs.
- **Why:** a demo of Dean's projects should render from a fresh install with
  real sounds, not only synths, without Dean's sample packs. Free,
  publishable placeholders do that, and the user can swap in their own later.
- **Verify:** test

### REQ-PRJ-06: Doctor and status see the projects
`mph doctor` says how many projects are on, and reports each problem with
them with its fix: a folder that isn't there, a broken `project.toml`, a
score that names another project, or a benchmark id in two projects.
`mph status` gives a one-line count.
- **Why:** a broken or missing project should show at a glance, not later
  as a benchmark that has gone missing.
- **Verify:** test
