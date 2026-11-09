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
