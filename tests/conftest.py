from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from mph.platform_info import Host


@pytest.fixture
def mac_host(tmp_path: Path) -> Host:
    root = tmp_path / "root"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    return Host("Darwin", "arm64", home, (root,))


def make_bundle(root: Path, relative: str, version: str | None) -> Path:
    bundle = root / relative.lstrip("/")
    (bundle / "Contents").mkdir(parents=True)
    info = {"CFBundleShortVersionString": version} if version else {}
    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)
    return bundle


@pytest.fixture(autouse=True)
def _isolate_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never read Dean's real ~/.config/mph during tests."""
    monkeypatch.setenv("MPH_CONFIG", str(tmp_path / "absent.toml"))
