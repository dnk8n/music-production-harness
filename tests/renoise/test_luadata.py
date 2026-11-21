from __future__ import annotations

from pathlib import Path

import pytest

from mph.renoise import luadata
from tests.renoise import lua


def test_scalars_and_containers() -> None:
    assert luadata.encode(None) == "nil"
    assert luadata.encode(True) == "true"
    assert luadata.encode(3) == "3"
    assert luadata.encode(0.5) == "0.5"
    assert luadata.encode('say "hi"\n') == '"say \\"hi\\"\\010"'
    assert luadata.encode([]) == "{}"
    assert luadata.encode({}) == "{}"
    assert luadata.encode([1, 2.5]) == "{1, 2.5}"
    assert luadata.encode({"end": 1, "ok": [{"a": "b"}]}) == (
        '{\n  ["end"] = 1,\n  ok = {\n    {\n      a = "b",\n    },\n  },\n}'
    )


def test_refuses_numbers_lua_cant_hold() -> None:
    with pytest.raises(ValueError, match="Lua data"):
        luadata.encode(float("nan"))


@pytest.mark.lua
@pytest.mark.req("REQ-REN-03")
def test_lua_reads_back_what_python_wrote(tmp_path: Path) -> None:
    value: luadata.Data = {
        "songs": [{"name": "tab\there", "volume": 0.25, "slices": [1, 2], "path": "/a b/c\\d"}],
        "flag": False,
    }
    path = tmp_path / "job.lua"
    path.write_text(luadata.document(value), "utf-8")
    assert lua.run("data", str(path)) == value


@pytest.mark.lua
@pytest.mark.req("REQ-REN-03")
def test_a_job_too_big_for_one_lua_function_still_loads(tmp_path: Path) -> None:
    # Written as one table, a whole track's job held more than the 65,536 constants
    # Renoise's Lua (LuaJIT) allows in a function, and Renoise refused it (2026-11-21).
    # 140,000 tables holding 280,000 numbers are past plain Lua 5.1's limit too.
    value: luadata.Data = {"points": [[n, n + 0.5] for n in range(140_000)]}
    path = tmp_path / "job.lua"
    path.write_text(luadata.document(value), "utf-8")
    assert lua.run("data", str(path)) == value


@pytest.mark.lua
@pytest.mark.req("REQ-REN-03")
def test_big_tables_go_in_functions_and_long_lists_in_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(luadata, "LIMIT", 40)
    monkeypatch.setattr(luadata, "SMALL", 8)
    value: luadata.Data = {
        "songs": [
            {"name": "a", "notes": [{"line": n, "volume": 100} for n in range(30)]},
            {"name": "b", "lanes": [{"points": [[n, 0.5], [n + 1, 1.0]]} for n in range(12)]},
        ]
    }
    text = luadata.document(value)
    assert "(function() return {" in text  # the costliest lanes and songs, each on its own
    assert "for i = 1, " in text  # the notes, in groups
    path = tmp_path / "job.lua"
    path.write_text(text, "utf-8")
    assert lua.run("data", str(path)) == value
    with pytest.raises(ValueError, match="too big"):
        luadata.encode({key: 1 for key in "abcdefghijklmnopqrstuvwxyz" * 2})
