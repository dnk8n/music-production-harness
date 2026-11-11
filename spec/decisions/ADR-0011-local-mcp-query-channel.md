# ADR-0011: Claude queries mph through a local MCP server

**Status:** accepted
**Date:** 2026-11-11

## Context

Dean wants Claude to answer questions about the catalogue, the benchmarks and
everything else mph knows, whenever it's useful (2026-11-11). That data lives
on Dean's Mac: the catalogue database and the renders in `data_dir`, the
scores in the checkout. Project threads run in the cloud and can't reach the
Mac's localhost. They reach the Mac through Remote Control, which runs a
Claude Code session there. Claude Code and Claude Desktop can both start
local MCP servers and call their tools.

## Decision

`mph mcp serve` is an MCP server over stdio that offers read-only tools
(spec/components/query-channel.md). `mph mcp install` registers it with
Claude Code for every session on the Mac.

The protocol is written with the standard library. The stdio subset this
server needs is small: JSON-RPC lines on stdin and stdout, the `initialize`
handshake, `server/discover`, `tools/list` and `tools/call`. Tests pin the
messages of each supported revision, and the server is checked by hand
against real Claude Code.

The tools are plain functions with JSON Schema arguments and don't depend
on MCP. The site and API planned in issue #31 can then offer the same tools
over HTTP, or to an LLM inside a render: the Claude API and Ollama both take
tools in that shape.

## Alternatives considered

- *The official MCP Python SDK:* the usual choice, but it brings pydantic,
  anyio, httpx, starlette, uvicorn and more as runtime dependencies
  (REQ-ENG-05), for the small part of the protocol a stdio server uses.
- *An HTTP API on localhost:* needs a port and request checks, and cloud
  threads still couldn't reach it. Deferred to issue #31, where the page
  needs an API anyway.
- *A public tunnel and a claude.ai connector:* cloud threads could then query
  directly, but Dean's library would be on the internet behind an
  authentication layer we would have to build and trust. Not now.
- *Letting Claude run `sqlite3` directly:* nothing guards against writes or a
  runaway query, renders and scores aren't covered, and every session would
  have to relearn the schema.

## Consequences

- A thread that needs facts from the Mac starts a Remote Control session and
  asks through the tools. Dean can do the same in any Claude Code session.
- The channel only reads. Rendering, analysing and giving verdicts stay with
  the CLI and the page, until issue #31 decides how an agent may do them.
- When MCP changes, the protocol module has to change with it. Revision
  2026-07-28 removed the handshake, so the server speaks both styles.
