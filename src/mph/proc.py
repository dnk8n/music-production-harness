"""The one place that spawns external processes.

Everything else depends on the ``Runner`` protocol so tests can substitute a
fake and never touch the real system (REQ-ENG-03).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Result:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0


class Runner(Protocol):
    def run(
        self, argv: Sequence[str], *, interactive: bool = False, stdin: str | None = None
    ) -> Result: ...

    def which(self, name: str) -> str | None: ...


class SubprocessRunner:
    """Runs argv lists (never shell strings) with a hard timeout."""

    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s

    def run(
        self, argv: Sequence[str], *, interactive: bool = False, stdin: str | None = None
    ) -> Result:
        """``interactive`` inherits the terminal (installers); ``stdin`` feeds input
        privately, e.g. secrets to ``security -i``, never via argv."""
        if interactive:
            return Result(subprocess.run(list(argv), check=False).returncode)
        try:
            completed = subprocess.run(
                list(argv),
                check=False,
                capture_output=True,
                text=True,
                input=stdin,
                timeout=self._timeout_s,
            )
        except FileNotFoundError:
            return Result(127, stderr=f"not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            return Result(124, stderr=f"timed out after {self._timeout_s}s: {argv[0]}")
        return Result(completed.returncode, completed.stdout, completed.stderr)

    def which(self, name: str) -> str | None:
        return shutil.which(name)
