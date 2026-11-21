"""Running the bridge tool under LuaJIT with a fake Renoise (tests only).

Renoise's scripting is LuaJIT, a Lua 5.1, so LuaJIT comes first here
(``apt-get install luajit``, ``brew install luajit``): its limits are
Renoise's. It refuses a function with more than 65,536 constants, which plain
Lua 5.1 allows, and a whole track's job hit that in Renoise (2026-11-21).
Plain Lua 5.1 stands in where LuaJIT isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).parent / "harness.lua"
INTERPRETERS = ("luajit", "lua5.1", "lua-5.1")


def interpreter() -> str | None:
    """The first Lua 5.1 compatible interpreter on PATH, if any."""
    return next((name for name in INTERPRETERS if shutil.which(name)), None)


def tool_dir() -> Path:
    return Path(str(resources.files("mph.renoise").joinpath("tool", "com.mph.bridge.xrnx")))


def run(*args: str) -> Any:
    lua = interpreter()
    assert lua is not None, "no Lua 5.1 interpreter (luajit or lua5.1)"
    done = subprocess.run(
        [lua, str(HARNESS), str(tool_dir()), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
