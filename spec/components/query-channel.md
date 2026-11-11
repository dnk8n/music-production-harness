# Query channel

**Status:** built

A read-only window through which Claude can ask about everything mph knows
on Dean's Mac: the sample catalogue (its SQLite database), the benchmarks
with their scores, renders, measurements and verdicts, the config and the
health checks. It is an MCP server (Model Context Protocol, ADR-0011). Claude
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
| `overview` | Start here: config, sources, catalogue size and how much is analysed, benchmarks per project with verdict counts, disk used by renders |
| `labels` | The tag vocabulary (role, pack, bpm, key, form, character) with counts by origin, or the values of one tag |
| `search_sounds` | The catalogue search (REQ-CAT-05): hard filters, then hits ranked by fit with their reasons |
| `sound` | Everything about one sound: files, tags, analysis, licence and download, lineage, stashes, renders that used it |
| `benchmarks` | Every benchmark and its takes: rendered or not, measurements passed, the verdict that stands |
| `benchmark` | One benchmark: what to hear, its score file, each take's newest render in brief (kit, failed measurements, levels), render history, verdicts |
| `report` | One render's full report: kit, levels, measurements, files |
| `verdicts` | Verdict history across benchmarks, newest first |
| `schema` | The database's tables, columns, row counts and comments |
| `sql` | One read-only query against the database |
| `doctor` | The `mph doctor` findings |

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
opens audio, never renders, analyses, downloads or deletes anything, and
never reads a secret: doctor only checks whether each one is set.
- **Why:** asking a question must not be able to change Dean's library,
  verdicts or sources (REQ-CAT-01). Whatever an agent sends, the worst
  outcome is a refused query.
- **Verify:** test

### REQ-QRY-03: Same answers as the CLI and the page
Each tool answers from the same code as the command or page that shows the
same thing: search from REQ-CAT-05, takes and the verdicts that stand from
the listening page (REQ-FBK-01, REQ-FBK-02), findings from `mph doctor`.
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
limit. It is never cut off. The one shortening is in summaries: `benchmark`
shows the first 300 characters of a long verdict note, marked as clipped,
and `verdicts` shows it whole.
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
