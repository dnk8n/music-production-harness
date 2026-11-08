"""Secrets live in the macOS Keychain, never in files or the repo (ADR-0005).

Values are only ever read when a component needs them at runtime. Writing
goes through ``security``'s own interactive prompt, so a secret is never in
our argv, our logs, or shell history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mph.proc import Runner

SECURITY = "/usr/bin/security"


@dataclass(frozen=True, slots=True)
class SecretSpec:
    name: str
    purpose: str
    signup_url: str
    required: bool = False


# Every secret the harness knows about. Anything else is rejected, which keeps
# the Keychain namespace tidy and doctor's report complete.
KNOWN_SECRETS: tuple[SecretSpec, ...] = (
    SecretSpec(
        "freesound_api_key",
        "Search and download CC-licensed sounds from Freesound",
        "https://freesound.org/apiv2/apply/",
    ),
    SecretSpec(
        "gemini_api_key",
        "Free-tier LLM reviewer for spec and feedback (falls back to local Ollama)",
        "https://aistudio.google.com/apikey",
    ),
)


class UnknownSecretError(KeyError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def exists(self, name: str) -> bool: ...

    def set_interactive(self, name: str) -> bool: ...

    def delete(self, name: str) -> bool: ...


def spec_for(name: str) -> SecretSpec:
    for spec in KNOWN_SECRETS:
        if spec.name == name:
            return spec
    known = ", ".join(s.name for s in KNOWN_SECRETS)
    raise UnknownSecretError(f"unknown secret {name!r}; known: {known}")


class KeychainStore:
    """Generic-password items under one service, one account per secret name."""

    def __init__(self, runner: Runner, service: str) -> None:
        self._runner = runner
        self._service = service

    def _base(self, verb: str, name: str) -> list[str]:
        spec_for(name)
        return [SECURITY, verb, "-s", self._service, "-a", name]

    def get(self, name: str) -> str | None:
        result = self._runner.run([*self._base("find-generic-password", name), "-w"])
        return result.stdout.rstrip("\n") if result.ok else None

    def exists(self, name: str) -> bool:
        # Without -w, `security` prints item attributes only, never the value.
        return self._runner.run(self._base("find-generic-password", name)).ok

    def set_interactive(self, name: str) -> bool:
        # A trailing bare -w makes `security` prompt for the value on the TTY.
        # -U updates an existing item instead of failing.
        argv = [*self._base("add-generic-password", name), "-U", "-w"]
        return self._runner.run(argv, interactive=True).ok

    def delete(self, name: str) -> bool:
        return self._runner.run(self._base("delete-generic-password", name)).ok
