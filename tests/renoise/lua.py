"""Running the bridge tool under Lua 5.1 with a fake Renoise (tests only).

Renoise's scripting is Lua 5.1. Linux packages it as ``lua5.1``; Homebrew no
longer does, so on a Mac LuaJIT (``brew install luajit``, Lua 5.1 compatible)
stands in.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).parent / "harness.lua"
INTERPRETERS = ("lua5.1", "lua-5.1", "luajit")


def interpreter() -> str | None:
    """The first Lua 5.1 compatible interpreter on PATH, if any."""
    return next((name for name in INTERPRETERS if shutil.which(name)), None)


def tool_dir() -> Path:
    return Path(str(resources.files("mph.renoise").joinpath("tool", "com.mph.bridge.xrnx")))


def run(*args: str) -> Any:
    lua = interpreter()
    assert lua is not None, "no Lua 5.1 interpreter (lua5.1 or luajit)"
    done = subprocess.run(
        [lua, str(HARNESS), str(tool_dir()), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
