"""Running the bridge tool under lua5.1 with a fake Renoise (tests only)."""

from __future__ import annotations

import json
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).parent / "harness.lua"


def tool_dir() -> Path:
    return Path(str(resources.files("mph.renoise").joinpath("tool", "com.mph.bridge.xrnx")))


def run(*args: str) -> Any:
    done = subprocess.run(
        ["lua5.1", str(HARNESS), str(tool_dir()), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
