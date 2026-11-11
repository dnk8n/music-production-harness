"""Diagnose the current state of the harness. Spec: spec/components/doctor.md.

Doctor never changes anything. Every finding carries a concrete fix, so the
report doubles as a to-do list. The exit code is non-zero only when a
*required* check fails, which makes ``mph doctor`` usable as a CI/pre-flight gate.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum

from mph.components import Component, Context, Stage, select
from mph.config import Config, Source
from mph.secrets import CREDENTIALS, SecretStore, missing_fields


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    status: Status
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class Report:
    findings: tuple[Finding, ...]

    @property
    def healthy(self) -> bool:
        return all(f.status is not Status.FAIL for f in self.findings)

    def count(self, status: Status) -> int:
        return sum(f.status is status for f in self.findings)


def check_component(component: Component, ctx: Context) -> Finding:
    check = f"component:{component.id}"
    detection = component.detector.detect(ctx)
    if not detection.found:
        status = Status.FAIL if component.required else Status.WARN
        return Finding(
            check,
            status,
            f"{component.name} not found ({component.purpose})",
            f"mph install {component.id}",
        )
    version = detection.version or "unknown version"
    if not component.version_ok(detection):
        return Finding(
            check,
            Status.FAIL,
            f"{component.name} {version} < {component.min_version}",
            f"upgrade {component.name}",
        )
    named = (
        f"{component.name} {detection.version}"
        if detection.version
        else f"{component.name} ({detection.note or 'version unknown'})"
    )
    if detection.problem:  # there, but broken: as bad as missing (REQ-DOC-08)
        return Finding(
            check,
            Status.FAIL if component.required else Status.WARN,
            f"{named} at {detection.location} {detection.problem}",
            f"mph install {component.id}",
        )
    return Finding(check, Status.OK, f"{named} at {detection.location}")


def check_source(source: Source) -> Finding:
    check = f"source:{source.id}"
    root = source.local_path
    if root is None:
        return Finding(check, Status.OK, f"remote source {source.location}")
    if not root.is_dir():
        return Finding(
            check,
            Status.FAIL,
            f"{root} is not a readable directory",
            "fix the path in your config or mount the drive",
        )
    if not os.access(root, os.R_OK | os.X_OK):
        return Finding(check, Status.FAIL, f"no read permission on {root}", f"chmod u+rx {root}")
    return Finding(check, Status.OK, f"{root} readable (treated as read-only)")


def check_data_dir(config: Config) -> Finding:
    data_dir = config.data_dir
    existing = next((p for p in (data_dir, *data_dir.parents) if p.exists()), None)
    if existing is None or not os.access(existing, os.W_OK):
        return Finding(
            "data_dir",
            Status.FAIL,
            f"cannot create or write {data_dir}",
            "set [paths].data_dir to a writable location",
        )
    state = "exists" if data_dir.exists() else "will be created on first use"
    return Finding("data_dir", Status.OK, f"{data_dir} ({state})")


def check_secrets(store: SecretStore) -> Iterable[Finding]:
    for credential in CREDENTIALS:
        check = f"secret:{credential.name}"
        missing = missing_fields(store, credential)
        if not missing:
            yield Finding(check, Status.OK, f"{credential.provider}: all fields in Keychain")
            continue
        status = Status.FAIL if credential.required else Status.WARN
        what = ", ".join(f.label for f in missing)
        yield Finding(
            check,
            status,
            f"{credential.provider} missing {what} ({credential.purpose})",
            f"mph secrets set {credential.name}  (keys: {credential.signup_url})",
        )


def run(
    config: Config,
    ctx: Context,
    store: SecretStore | None,
    stages: Sequence[Stage] = (),
) -> Report:
    findings: list[Finding] = []
    if not stages or Stage.CORE in stages or Stage.CATALOGUE in stages:
        findings.append(check_data_dir(config))
        if not config.sources:
            findings.append(
                Finding(
                    "sources",
                    Status.WARN,
                    "no sample sources configured",
                    "add a [[sources]] table to your config",
                )
            )
        findings.extend(check_source(s) for s in config.sources)
    findings.extend(check_component(c, ctx) for c in select(stages))
    if not stages or Stage.AI in stages or Stage.CATALOGUE in stages:
        if store is None:
            findings.append(Finding("secrets", Status.SKIP, "Keychain only available on macOS"))
        else:
            findings.extend(check_secrets(store))
    return Report(tuple(findings))
