"""Test doubles for the seams in REQ-ENG-03."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mph.net import HttpError
from mph.proc import Result

Handler = Callable[[Sequence[str]], Result]


@dataclass
class FakeRunner:
    """Records calls; answers from a map of program name -> handler or Result."""

    on_path: dict[str, str] = field(default_factory=dict)
    responses: dict[str, Result | Handler] = field(default_factory=dict)
    calls: list[tuple[tuple[str, ...], bool]] = field(default_factory=list)
    stdins: list[str | None] = field(default_factory=list)

    def run(
        self, argv: Sequence[str], *, interactive: bool = False, stdin: str | None = None
    ) -> Result:
        self.calls.append((tuple(argv), interactive))
        self.stdins.append(stdin)
        key = argv[0].rsplit("/", 1)[-1]
        response = self.responses.get(key, Result(0))
        return response(argv) if callable(response) else response

    def capture(self, argv: Sequence[str]) -> Result:
        return self.run(argv)

    def which(self, name: str) -> str | None:
        return self.on_path.get(name)


@dataclass
class FakeStore:
    values: dict[str, str] = field(default_factory=dict)
    fail_puts: bool = False

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def exists(self, account: str) -> bool:
        return account in self.values

    def put(self, account: str, value: str) -> bool:
        if self.fail_puts:
            return False
        self.values[account] = value
        return True

    def delete(self, account: str) -> bool:
        return self.values.pop(account, None) is not None


@dataclass
class ScriptedPrompter:
    """Answers prompts from a list; records everything said and asked."""

    answers: list[str]
    said: list[str] = field(default_factory=list)
    asked: list[tuple[str, bool]] = field(default_factory=list)

    def say(self, text: str) -> None:
        self.said.append(text)

    def ask(self, label: str, *, secret: bool) -> str:
        self.asked.append((label, secret))
        return self.answers.pop(0)


@dataclass
class FakeHttp:
    """Answers from URL-prefix maps; downloads write the mapped bytes."""

    json_by_prefix: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    posts: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)
    downloads: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    post_reply: dict[str, str] = field(
        default_factory=lambda: {"access_token": "acc", "refresh_token": "ref2"}
    )

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Any:
        self.gets.append(url)
        for prefix, reply in self.json_by_prefix.items():
            if url.startswith(prefix):
                return reply
        raise HttpError(f"404 from fake: {url}")

    def post_form(self, url: str, form: Mapping[str, str]) -> Any:
        self.posts.append((url, dict(form)))
        return self.post_reply

    def download(
        self, url: str, dest: Path, *, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> int:
        self.downloads.append((url, dict(headers or {})))
        if url not in self.files:
            raise HttpError(f"404 from fake: {url}")
        body = self.files[url]
        if len(body) > max_bytes:
            raise HttpError("bigger than the limit")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return len(body)
