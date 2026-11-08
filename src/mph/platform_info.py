"""Facts about the host that detection needs, injectable for tests."""

from __future__ import annotations

import platform
import plistlib
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION = re.compile(r"\d+(?:\.\d+)*")


@dataclass(frozen=True, slots=True)
class Host:
    system: str  # "Darwin", "Linux", ...
    machine: str  # "arm64", "x86_64", ...
    home: Path
    roots: tuple[Path, ...] = (Path("/"),)  # prefixes for app/plugin lookup; tests point elsewhere

    @property
    def is_macos(self) -> bool:
        return self.system == "Darwin"

    def find(self, *relative: str) -> Path | None:
        """Return the first existing path among roots x candidates (``~`` = home)."""
        for candidate in relative:
            if candidate.startswith("~/"):
                path = self.home / candidate[2:]
                if path.exists():
                    return path
                continue
            for root in self.roots:
                path = root / candidate.lstrip("/")
                if path.exists():
                    return path
        return None


def current_host() -> Host:
    return Host(platform.system(), platform.machine(), Path.home())


def bundle_version(bundle: Path) -> str | None:
    """Read CFBundleShortVersionString from a macOS .app/.component bundle."""
    plist = bundle / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as fh:
            value = plistlib.load(fh).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None
    # Some bundles (e.g. Ultimate Vocal Remover) ship a 0.0.0 placeholder.
    if not value or not any(parse_version(str(value))):
        return None
    return str(value)


def parse_version(text: str) -> tuple[int, ...]:
    """First dotted-integer run in ``text``: 'Renoise 3.5.4 (arm64)' -> (3, 5, 4)."""
    match = _VERSION.search(text)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()
