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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``tools`` tests need SuperCollider and ``lua`` tests need Lua 5.1; skip them
    quietly where those aren't installed."""
    from mph.bench.sc import find_tools  # noqa: PLC0415 - only when collecting
    from mph.proc import SubprocessRunner  # noqa: PLC0415
    from tests.renoise import lua  # noqa: PLC0415

    runner = SubprocessRunner()
    missing = {
        "tools": find_tools(runner) is None and "SuperCollider isn't installed",
        "lua": lua.interpreter() is None and "no Lua 5.1 (lua5.1, or luajit on a Mac)",
    }
    for item in items:
        for marker, reason in missing.items():
            if reason and marker in item.keywords:
                item.add_marker(pytest.mark.skip(reason=str(reason)))
