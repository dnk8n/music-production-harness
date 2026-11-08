"""Test doubles for the seams in REQ-ENG-03."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from mph.proc import Result

Handler = Callable[[Sequence[str]], Result]


@dataclass
class FakeRunner:
    """Records calls; answers from a map of program name -> handler or Result."""

    on_path: dict[str, str] = field(default_factory=dict)
    responses: dict[str, Result | Handler] = field(default_factory=dict)
    calls: list[tuple[tuple[str, ...], bool]] = field(default_factory=list)

    def run(self, argv: Sequence[str], *, interactive: bool = False) -> Result:
        self.calls.append((tuple(argv), interactive))
        key = argv[0].rsplit("/", 1)[-1]
        response = self.responses.get(key, Result(0))
        return response(argv) if callable(response) else response

    def which(self, name: str) -> str | None:
        return self.on_path.get(name)


@dataclass
class FakeStore:
    present: set[str] = field(default_factory=set)

    def get(self, name: str) -> str | None:
        return "value" if name in self.present else None

    def exists(self, name: str) -> bool:
        return name in self.present

    def set_interactive(self, name: str) -> bool:
        self.present.add(name)
        return True

    def delete(self, name: str) -> bool:
        existed = name in self.present
        self.present.discard(name)
        return existed
