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
        if p.name != "proc.py" and imported_modules(p) & {"subprocess", "os.system", "pty"}
    ]
    assert offenders == []


# Third-party runtime imports, each justified by an ADR (REQ-ENG-05), and the
# only modules allowed to use them, so heavy dependencies stay contained.
JUSTIFIED = {
    "librosa": {"catalogue/analysis.py"},  # ADR-0008
    "numpy": {"catalogue/analysis.py"},  # ADR-0008
    "pyloudnorm": {"catalogue/analysis.py"},  # ADR-0008
    "soundfile": {"catalogue/analysis.py"},  # ADR-0008
}


@pytest.mark.req("REQ-ENG-05")
def test_third_party_imports_are_justified_and_contained() -> None:
    allowed = set(sys.stdlib_module_names) | {"mph", "__future__"}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        for module in imported_modules(path) - allowed:
            assert rel in JUSTIFIED.get(module, set()), f"{rel} imports {module}"
