"""Credentials live in the macOS Keychain, never in files or the repo (ADR-0005).

Spec: spec/components/secrets.md.

A third-party service needs a *credential*: one or more named fields (e.g.
Freesound's client ID and client secret). Each field is its own Keychain item,
with account ``<credential>.<field>`` under one service. ``setup`` walks the
user through every field, says where to find it, validates the shape, and
writes nothing until all fields are collected. Values reach ``security`` over
stdin, never argv, so they don't show up in the process list or shell history.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from mph.proc import Runner

SECURITY = "/usr/bin/security"
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9._\-]{8,512}$")
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    label: str
    where: str  # where on the provider's site the user finds this value
    secret: bool = True  # hidden while typing


@dataclass(frozen=True, slots=True)
class Credential:
    name: str
    provider: str
    purpose: str
    signup_url: str
    fields: tuple[Field, ...]
    required: bool = False
    notes: str = ""

    def account(self, field: Field) -> str:
        return f"{self.name}.{field.key}"


# Every credential the harness knows about. Anything else is rejected, which
# keeps the Keychain namespace tidy and doctor's report complete (REQ-SEC-03).
CREDENTIALS: tuple[Credential, ...] = (
    Credential(
        "freesound",
        "Freesound",
        "Search and download CC-licensed sounds",
        "https://freesound.org/apiv2/apply/",
        (
            Field(
                "client_id",
                "Client ID",
                "the 'Client id' column of your app on https://freesound.org/apiv2/apply/",
                secret=False,
            ),
            Field(
                "client_secret",
                "Client secret / API key",
                "the 'Client secret/Api key' column on the same page",
            ),
        ),
        notes="Search and previews work with these. Downloading original-quality files "
        "also needs a one-time browser sign-in (OAuth2), added with the catalogue in step 2.",
    ),
    Credential(
        "gemini",
        "Google Gemini (free tier)",
        "LLM reviewer for spec and feedback (falls back to local Ollama)",
        "https://aistudio.google.com/apikey",
        (Field("api_key", "API key", "'Create API key' on https://aistudio.google.com/apikey"),),
    ),
)


class UnknownCredentialError(KeyError):
    pass


class SecretStore(Protocol):
    def get(self, account: str) -> str | None: ...

    def exists(self, account: str) -> bool: ...

    def put(self, account: str, value: str) -> bool: ...

    def delete(self, account: str) -> bool: ...


class Prompter(Protocol):
    def say(self, text: str) -> None: ...

    def ask(self, label: str, *, secret: bool) -> str: ...


def credential_for(name: str) -> Credential:
    for credential in CREDENTIALS:
        if credential.name == name:
            return credential
    known = ", ".join(c.name for c in CREDENTIALS)
    raise UnknownCredentialError(f"unknown credential {name!r}; known: {known}")


def missing_fields(store: SecretStore, credential: Credential) -> list[Field]:
    return [f for f in credential.fields if not store.exists(credential.account(f))]


def read(store: SecretStore, credential: Credential) -> dict[str, str] | None:
    """All field values, or None if any is missing. Call only at point of use."""
    values: dict[str, str] = {}
    for field in credential.fields:
        value = store.get(credential.account(field))
        if value is None:
            return None
        values[field.key] = value
    return values


def _collect(field: Field, prompter: Prompter) -> str | None:
    prompter.say(f"\n{field.label}: find it at {field.where}")
    for _ in range(MAX_ATTEMPTS):
        value = prompter.ask(field.label, secret=field.secret).strip()
        if TOKEN_SHAPE.match(value):
            return value
        prompter.say(
            "  That doesn't look right: expected 8+ characters of letters, digits, "
            "'.', '_' or '-', with no spaces. Try again."
        )
    return None


def setup(store: SecretStore, credential: Credential, prompter: Prompter) -> bool:
    """Guided entry of every field; writes only after all are collected (REQ-SEC-05)."""
    prompter.say(f"{credential.provider}: {credential.purpose}")
    prompter.say(f"Sign up or manage keys at {credential.signup_url}")
    values: dict[str, str] = {}
    for field in credential.fields:
        value = _collect(field, prompter)
        if value is None:
            prompter.say("Nothing was saved.")
            return False
        values[field.key] = value
    return _write_all(store, credential, values, prompter)


def _write_all(
    store: SecretStore, credential: Credential, values: Mapping[str, str], prompter: Prompter
) -> bool:
    failed = [
        f.label for f in credential.fields if not store.put(credential.account(f), values[f.key])
    ]
    if failed:
        prompter.say(f"Could not save to the Keychain: {', '.join(failed)}")
        return False
    prompter.say(f"\nSaved {len(values)} field(s) for {credential.provider} in your Keychain.")
    if credential.notes:
        prompter.say(credential.notes)
    return True


def remove(store: SecretStore, credential: Credential) -> bool:
    """Delete every field; True if anything was deleted."""
    results = [store.delete(credential.account(f)) for f in credential.fields]
    return any(results)


class KeychainStore:
    """Generic-password items under one Keychain service."""

    def __init__(self, runner: Runner, service: str) -> None:
        self._runner = runner
        self._service = service

    def _args(self, verb: str, account: str) -> list[str]:
        return [verb, "-s", self._service, "-a", account]

    def get(self, account: str) -> str | None:
        result = self._runner.run([SECURITY, *self._args("find-generic-password", account), "-w"])
        return result.stdout.rstrip("\n") if result.ok else None

    def exists(self, account: str) -> bool:
        # Without -w, `security` prints item attributes only, never the value.
        return self._runner.run([SECURITY, *self._args("find-generic-password", account)]).ok

    def put(self, account: str, value: str) -> bool:
        if not TOKEN_SHAPE.match(value):  # token chars only, so no quoting is needed below
            return False
        # `security -i` reads commands from stdin, keeping the value out of argv.
        # -U updates an existing item instead of failing.
        command = " ".join([*self._args("add-generic-password", account), "-U", "-w", value])
        self._runner.run([SECURITY, "-i"], stdin=command + "\n")
        # `security -i` exits 0 even when a command fails, so read the value back.
        return self.get(account) == value

    def delete(self, account: str) -> bool:
        return self._runner.run([SECURITY, *self._args("delete-generic-password", account)]).ok
