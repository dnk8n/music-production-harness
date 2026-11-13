# Query channel

**Status:** built

A read-only window through which Claude can ask about everything mph knows
on Dean's Mac: the sample catalogue (its SQLite database), the benchmarks
with their scores, renders, measurements and ratings, Dean's ideas, the
config and the health checks. It is an MCP server (Model Context Protocol, ADR-0011). Claude
Code starts it when a session needs it, talks to it over stdin and stdout,
and stops it when the session ends. Nothing stays running, and nothing
listens on a port.
Implementation: `src/mph/query/`.

## Commands

- `mph mcp install`: run once per Mac. It registers the server with Claude
  Code for every session on the Mac, including the Remote Control sessions a
  project thread starts.
- `mph mcp serve`: the server itself. Claude Code runs it; Dean doesn't need to.
- `mph mcp call TOOL [JSON]`: runs one tool and prints its answer, exactly
  what Claude would see.

## Tools

| Tool | What it answers |
|---|---|
| `overview` | Start here: config, sources (download stores too) and how much of each is analysed, catalogue size, roles counted two ways (REQ-QRY-11), benchmarks per project with how their takes are rated, open ideas, disk used by renders |
| `feedback` | The work Dean's ratings and ideas ask for: unresolved takes with their stars and feedback, and open ideas (REQ-QRY-12) |
| `labels` | The tag vocabulary (role, pack, bpm, key, form, character) with counts by origin, or the values of one tag |
| `search_sounds` | The catalogue search (REQ-CAT-05): hard filters, then hits ranked by fit with their reasons and licence |
| `sound` | Everything about one sound: files, tags, analysis, licence and download, lineage, stashes, renders that used it |
| `benchmarks` | Every benchmark and its takes: rendered or not, which measurements failed, the rating that stands with its stars, status and feedback (REQ-QRY-10) |
| `benchmark` | One benchmark: what to hear, its score file, each take's newest render in brief (kit, failed measurements, levels, commit), render history, ratings as given |
| `report` | One render's full report: kit, levels, measurements, files, commit, and the ratings given on it |
| `ratings` | Rating history across benchmarks, newest first, each as given |
| `ideas` | Every idea Dean added on the page: open, built or dropped (REQ-FBK-08) |
| `schema` | The database's tables, columns, row counts and comments |
| `sql` | One read-only query against the database |
| `doctor` | The `mph doctor` findings |

The benchmark tools also read what was rendered from other branches
(REQ-QRY-09).

The tools are plain functions with JSON Schema arguments and know nothing
about MCP, so the site and API in issue #31 can offer the same tools over
HTTP, or to an LLM inside a render.

### REQ-QRY-01: MCP over stdio
`mph mcp serve` speaks MCP over stdin and stdout: one JSON-RPC 2.0 message
per line, and nothing on stdout except protocol messages (anything else goes
to stderr). It serves both kinds of client: those that open with an
`initialize` handshake (protocol revisions 2024-11-05 to 2025-11-25), and
those that state the revision in every request (2026-07-28, which probes
with `server/discover`). It offers tools only, and its instructions say
where to start.
- **Why:** Claude Code (in a terminal, or in a Remote Control session from a
  thread) and Claude Desktop start local servers this way. The channel opens
  when a session needs it and closes with the session. Clients are moving to
  the 2026 revision while many still speak the older one.
- **Verify:** test

### REQ-QRY-02: Read-only
The channel opens the catalogue with SQLite's read-only mode and
`query_only`, and never creates or upgrades it. A catalogue made by an
older or newer mph is refused, with what to run instead. The channel never
opens audio, never renders, analyses, downloads or deletes anything, never
rates or adds ideas, and never reads a secret: doctor only checks whether
each one is set.
- **Why:** asking a question must not be able to change Dean's library,
  ratings or sources (REQ-CAT-01). Whatever an agent sends, the worst
  outcome is a refused query.
- **Verify:** test

### REQ-QRY-03: Same answers as the CLI and the page
Each tool answers from the same code as the command or page that shows the
same thing: search from REQ-CAT-05, one sound from `mph catalogue sound`
(REQ-CAT-09), takes and the ratings that stand from the listening page
(REQ-FBK-01, REQ-FBK-02), ideas and their ids as the page has them
(REQ-FBK-08), findings from `mph doctor`.
Benchmark tools work without a catalogue. Catalogue tools say to run
`mph catalogue scan` when there isn't one yet.
- **Why:** Claude and Dean must see the same facts. A second implementation
  would drift from the first.
- **Verify:** test

### REQ-QRY-04: Guarded SQL
`sql` runs one statement, and that statement may only read. SQLite's
authorizer refuses everything else: writes, `ATTACH`, `PRAGMA`,
transactions, loading extensions. It returns at most 1000 rows (100 unless
asked for more), stops after 5 seconds, and refuses any single value longer
than 1 MB.
- **Why:** "everything in the database" includes questions no tool foresaw,
  and SQL can answer them. The guards make a careless or hostile query
  harmless, and stop a runaway one from hanging the session.
- **Verify:** test

### REQ-QRY-05: Bounded answers
Every list has a limit, and no answer is longer than 40,000 characters. A
longer answer is refused with a note to narrow the question or lower the
limit, in the tool's own terms (`benchmarks`: one project at a time). It is
never cut off. The one shortening is in summaries: `benchmarks`,
`benchmark` and `feedback` show the first 300 characters of a long rating
note, marked as clipped, and `ratings` shows it whole.
- **Why:** Claude Code moves tool results over 50,000 characters into a file
  instead of showing them, and long answers crowd out the conversation. A
  refusal says how to ask better; a cut-off answer misleads without saying so.
- **Verify:** test

### REQ-QRY-06: Errors Claude can act on
A problem Claude can fix (a wrong argument, an unknown sound or benchmark,
bad SQL, no catalogue yet) comes back as a tool error with the reason and
what to try. Protocol errors are only for malformed requests and unknown
tools or methods.
- **Why:** MCP passes tool errors to the model, which can correct itself and
  try again. A protocol error usually just stops it.
- **Verify:** test

### REQ-QRY-07: One command to connect
`mph mcp install` registers the server with Claude Code for every session
on the Mac (user scope), replacing an older registration of the same name.
The registration runs this checkout through uv, by absolute paths, so it
works from any folder, and names the config file when `--config` or
`MPH_CONFIG` chose one. It says how to check the result (`claude mcp list`)
and how to remove it.
- **Why:** Dean asked for one command that opens the channel. Remote Control
  sessions on the Mac open in the mph data folder rather than the checkout,
  so a `.mcp.json` in the repo wouldn't reach them, and the Claude app may
  start a session with a short PATH.
- **Verify:** test

### REQ-QRY-08: See what Claude sees
`mph mcp call TOOL [JSON]` checks the arguments the same way, runs the tool
and prints its answer. A tool error is printed with exit code 1.
- **Why:** Dean, or a check over Remote Control, can see exactly what Claude
  would get, without involving Claude.
- **Verify:** test

### REQ-QRY-09: Renders from other branches
Threads render benchmarks from the branch they work on, while the checkout
the channel runs from is often on `main`. So the benchmark tools read every
render under `data_dir/renders`, not only those of the scores in this
checkout. A benchmark this checkout has no score for is listed after the
others, under the project "Not in this checkout", with its title, tempo and
takes taken from its reports, and no score file or "hear" text. A take the
score here doesn't declare is listed after the declared ones. Both are marked
`in_checkout: false`, and `benchmark`, `report`, `ratings`, `feedback` and
`sound` take them like any other. A render from before takes were equal (take `main`) is
still not a take. A standard take (`renoise`, `supercollider`, `synth-kit`)
that the score here no longer makes (REQ-BEN-07) is listed the same way, with
the state `retired` whatever its rating: it isn't work for agents
(REQ-QRY-12), and its renders and ratings stay readable.
- **Why:** BM-SC-01 was rendered and judged from PR #29 while the Mac's
  checkout was on `main`. Its renders and Dean's verdicts were on disk, but
  every benchmark tool refused it (2026-11-11). The north star benchmarks and
  the Renoise takes of issue #30 will be rendered from branches the same way.
  BM-DRUM-04's synth-kit take stopped being made (rating 156), and its
  unresolved rating would otherwise wait in the agents' queue for good.
- **Verify:** test

### REQ-QRY-10: Ratings as Dean gave them
A rating for the whole benchmark is stored once per take (REQ-FBK-02).
`ratings` and `benchmark` list it once, with every take and render it
covered, and count it once. In `benchmarks` and `benchmark`, a take's
standing rating carries its feedback when it is the take's own. A
whole-benchmark rating that stands for some takes is given once per
benchmark, with its feedback and those takes, and the takes point to it. A
rating on a `main` render counts the same way, because it covers every
take rendered by then. Each rating gives its stars, the status Dean set (or
none), the status that follows from it, and the verdict it was carried over
from, if any (REQ-FBK-07). `benchmarks` also lists each take's failed
measurements, with value, limit and note.
- **Why:** one whole-benchmark verdict showed up once per take, so its notes
  repeated and the counts grew with the number of takes. And to see what
  failed and what Dean said, Claude had to ask about each benchmark in turn
  (2026-11-11).
- **Verify:** test

### REQ-QRY-11: Counts say what they count
`overview` counts each role two ways, side by side, and says which is which:
`strongest` counts each sound once, under its most confident role (as
`mph catalogue stats` does), and `labelled` counts every sound that has the
role among its labels (as `labels` does), so a sound with two roles counts
under both. `labels` gives both for roles. Every source says how many of its
sounds aren't analysed yet. A download store, which `mph catalogue fetch`
fills for each connector, is shown as one, with how many sounds it holds,
when it last fetched and the command that analyses them, not as a source
missing from the config.
- **Why:** overview said 292 perc sounds and labels said 396, with nothing
  to say why (2026-11-11). The 13 Freesound and LibriVox downloads showed as
  `configured: false`, which reads as a mistake to fix, when what they
  needed was analysing.
- **Verify:** test

### REQ-QRY-12: Work for agents
`feedback` lists what Dean's ratings and ideas ask for. Every unresolved take
(REQ-FBK-06) that isn't retired (REQ-QRY-09), grouped by benchmark, with its stars, the feedback of its
standing rating and of the ratings before it on the same take, its failed
measurements, and whether it was rendered again since it was rated: then it
waits for Dean's ear, not for an agent. Takes waiting for an agent come
first. Each standing rating is given once, with its id, for a pull request
to cite. Then every open idea (REQ-FBK-08), with where it goes. It can be
narrowed to one project or one benchmark.
- **Why:** Dean, 2026-11-13: unresolved takes are for agents to find and
  act on, ideally ending in a high rating. One call gives an agent what it
  needs to pick up work without asking Dean, and the mark for takes already
  rendered again stops two agents fixing the same thing.
- **Verify:** test
