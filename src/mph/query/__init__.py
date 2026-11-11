"""The query channel: read-only answers about everything mph knows.

Spec: spec/components/query-channel.md, ADR-0011.

- ``readonly``: opens the catalogue so nothing can change it, and runs guarded SQL.
- ``answers``: builds each answer from the same code the CLI and the page use.
- ``tools``: names, descriptions and JSON Schema arguments, free of any protocol.
- ``mcp``: the MCP stdio protocol that offers the tools to Claude.
- ``commands``: ``mph mcp serve | install | call``.
"""

from __future__ import annotations


class Unanswerable(Exception):
    """A question the channel can't answer as asked. The message says why and
    what to try instead, so Claude can correct itself (REQ-QRY-06)."""
