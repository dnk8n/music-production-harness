"""``mph renoise ...`` subcommands (spec/components/renoise.md, REQ-REN-03)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mph.config import Config
from mph.renoise import bridge

STATES = {
    "ready": "the mph bridge tool is installed and up to date",
    "outdated": (
        "the mph bridge tool is out of date: run `mph renoise install`, then restart Renoise"
    ),
    "missing": "the mph bridge tool isn't installed: run `mph renoise install`",
    "no-renoise": "Renoise's preferences weren't found: install Renoise and open it once",
}


def dispatch(args: argparse.Namespace, cfg: Config, home: Path) -> int:
    if args.action == "install":
        return cmd_install(cfg, home)
    return cmd_status(home)


def cmd_install(cfg: Config, home: Path) -> int:
    try:
        target = bridge.install(cfg, home)
    except bridge.BridgeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"installed the mph bridge tool in {target}")
    print("Restart Renoise so it loads the tool. Benchmarks then render through Renoise.")
    return 0


def cmd_status(home: Path) -> int:
    state = bridge.status(home)
    print(STATES[state])
    return 0 if state == "ready" else 1
