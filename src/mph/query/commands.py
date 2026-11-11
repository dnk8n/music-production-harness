"""`mph mcp ...`: the query channel's commands (spec/components/query-channel.md).

- ``serve`` is what Claude Code runs: MCP on stdin and stdout (REQ-QRY-01).
- ``install`` registers ``serve`` for every Claude Code session on this Mac
  (REQ-QRY-07).
- ``call`` runs one tool and prints what Claude would see (REQ-QRY-08).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import sys
from pathlib import Path
from typing import IO

from mph import config
from mph.proc import Runner
from mph.query import Unanswerable, mcp, tools
from mph.query.answers import Library

SERVER_NAME = "mph"
CHECKOUT = Path(__file__).resolve().parents[3]  # src/mph/query/commands.py -> the repo
SERVE_HINT = (
    "mph mcp serve speaks MCP on stdin and stdout, for Claude Code to start. "
    "Register it once with `uv run mph mcp install`. "
    "To see a tool's answer here, try `uv run mph mcp call overview`. Ctrl-D stops."
)


def dispatch(args: argparse.Namespace, library: Library, runner: Runner) -> int:
    if args.action == "serve":
        return cmd_serve(library)
    if args.action == "install":
        return cmd_install(runner, config_file=pinned_config(args.config))
    return cmd_call(library, args.tool, args.arguments)


def pinned_config(explicit: Path | None) -> Path | None:
    """The config file to name in the registration: --config, else $MPH_CONFIG if
    it exists, since Claude Code may start without this shell's environment. The
    default file needs no naming."""
    if explicit is not None:
        return explicit
    from_env = Path(os.environ.get(config.ENV_CONFIG, ""))
    return from_env if from_env.name and from_env.is_file() else None


def cmd_serve(
    library: Library, reader: IO[bytes] | None = None, writer: IO[bytes] | None = None
) -> int:
    reader = reader or sys.stdin.buffer
    writer = writer or sys.stdout.buffer
    if reader.isatty():
        print(SERVE_HINT, file=sys.stderr)
    server = mcp.Server(library)
    # Only protocol messages may reach stdout: anything else printed goes to stderr.
    with contextlib.redirect_stdout(sys.stderr), contextlib.suppress(KeyboardInterrupt):
        try:
            mcp.serve(server, reader, writer)
        except BrokenPipeError:  # the client went away mid-answer
            return 0
    return 0


def server_command(uv: str, checkout: Path, config_file: Path | None = None) -> list[str]:
    """How Claude Code starts the server: this checkout, through uv, by absolute paths."""
    command = [uv, "--directory", str(checkout), "run", "--quiet", "mph"]
    if config_file is not None:
        command += ["--config", str(config_file.expanduser().resolve())]
    return [*command, "mcp", "serve"]


def cmd_install(
    runner: Runner, *, config_file: Path | None = None, checkout: Path = CHECKOUT
) -> int:
    claude, uv = runner.which("claude"), runner.which("uv")
    if claude is None:
        print("Claude Code isn't installed here (no `claude` on PATH)", file=sys.stderr)
        return 1
    if uv is None:
        print("uv isn't on PATH: `brew install uv`", file=sys.stderr)
        return 1
    if not (checkout / "pyproject.toml").is_file():
        print(f"{checkout} isn't an mph checkout: run this from one", file=sys.stderr)
        return 1
    command = server_command(uv, checkout, config_file)
    runner.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME])  # an older one, if any
    added = runner.run([claude, "mcp", "add", "--scope", "user", SERVER_NAME, "--", *command])
    if not added.ok:
        reason = added.stderr.strip() or added.stdout.strip() or f"exit {added.code}"
        print(f"`claude mcp add` failed: {reason}", file=sys.stderr)
        return 1
    print(f"Claude Code sessions on this Mac now have the read-only `{SERVER_NAME}` tools.")
    print(f"  server: {shlex.join(command)}")
    print("Sessions started from now on pick it up; `claude mcp list` should show it connected.")
    print("Other MCP clients, such as Claude Desktop, can run the same command.")
    print(f"To remove it: claude mcp remove --scope user {SERVER_NAME}")
    return 0


def cmd_call(library: Library, name: str, raw: str) -> int:
    try:
        arguments = json.loads(raw)
    except ValueError as exc:
        print(f"arguments must be a JSON object: {exc}", file=sys.stderr)
        return 2
    if not isinstance(arguments, dict):
        print("arguments must be a JSON object, such as '{\"limit\": 5}'", file=sys.stderr)
        return 2
    try:
        answer = tools.run(tools.by_name(name), library, arguments)
    except Unanswerable as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(json.loads(answer), indent=2, ensure_ascii=False))
    return 0
