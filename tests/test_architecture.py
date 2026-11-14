"""Structural rules from the spec, enforced as tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "mph"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.req("REQ-ENG-03")
def test_only_proc_spawns_processes() -> None:
    offenders = [
        p.name
        for p in SRC.rglob("*.py")
        if p.name != "proc.py"
        and imported_modules(p) & {"subprocess", "os.system", "pty", "webbrowser"}
    ]
    assert offenders == []


# Third-party runtime imports, each justified by an ADR (REQ-ENG-05), and the
# only modules allowed to use them, so heavy dependencies stay contained.
AUDIO_IO = {"catalogue/analysis.py", "bench/audio.py", "deliver.py"}
JUSTIFIED = {
    "librosa": {"catalogue/analysis.py", "catalogue/grid.py"},  # ADR-0008
    "numpy": AUDIO_IO | {"bench/render.py", "bench/levels.py", "catalogue/grid.py"},  # ADR-0008
    "pyloudnorm": {"catalogue/analysis.py", "bench/audio.py", "bench/levels.py"},  # ADR-0008
    "soundfile": AUDIO_IO,  # ADR-0008
    "soxr": {"bench/audio.py", "deliver.py"},  # ADR-0003: libsoxr does the one SRC
}


@pytest.mark.req("REQ-ENG-05")
def test_third_party_imports_are_justified_and_contained() -> None:
    allowed = set(sys.stdlib_module_names) | {"mph", "__future__"}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        for module in imported_modules(path) - allowed:
            assert rel in JUSTIFIED.get(module, set()), f"{rel} imports {module}"


@pytest.mark.req("REQ-ENG-03")
def test_only_net_talks_to_the_network() -> None:
    network = {"urllib", "http", "socket", "ssl", "requests", "httpx"}
    allowed = {"net.py", "catalogue/browse.py"}  # browse serves localhost only (REQ-BRW-01)
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if p.relative_to(SRC).as_posix() not in allowed and imported_modules(p) & network
    ]
    assert offenders == []


@pytest.mark.req("REQ-ENG-09")
def test_a_passing_tests_temporary_folder_is_deleted(pytestconfig: pytest.Config) -> None:
    # The setting pytest actually loaded, wherever it was configured.
    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
