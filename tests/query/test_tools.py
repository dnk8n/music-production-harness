from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mph.query import Unanswerable, tools
from mph.query.answers import Library
from mph.query.tools import Tool
from tests.query.conftest import library_for, make_cfg

LISTS = {
    "labels": "limit",
    "search_sounds": "limit",
    "ratings": "limit",
    "ideas": "limit",
    "sql": "max_rows",
}


def stub(answer: Any, schema: dict[str, Any] | None = None) -> Tool:
    return Tool("stub", "Stub", "for tests", schema or tools._args(), lambda lib, a: answer(a))


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    return library_for(make_cfg(tmp_path), tmp_path)


def problems(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    return tools.check(tools.by_name(tool_name).schema, arguments)


@pytest.mark.req("REQ-QRY-01")
def test_every_tool_is_described_for_the_model() -> None:
    names = [tool.name for tool in tools.TOOLS]
    assert len(names) == len(set(names)) == 13
    for tool in tools.TOOLS:
        assert re.fullmatch(r"[a-z_]{2,32}", tool.name)
        assert tool.title
        assert 80 < len(tool.description) < 1000
        assert tool.schema["type"] == "object"
        assert tool.schema["additionalProperties"] is False
        for name in tool.schema.get("required", []):
            assert name in tool.schema["properties"]
        json.dumps(tool.schema)  # plain JSON, as tools/list sends it


@pytest.mark.req("REQ-QRY-01")
def test_schemas_keep_to_json_schema() -> None:
    """Only standard keywords, and every default passes its own checks."""
    standard = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "maxItems",
        "pattern",
        "default",
        "description",
        "examples",
    }

    def walk(schema: dict[str, Any], where: str) -> None:
        assert set(schema) <= standard, where
        if "default" in schema:
            assert tools.check(schema, schema["default"]) == [], where
        for name, item in schema.get("properties", {}).items():
            walk(item, f"{where}.{name}")
        if "items" in schema:
            walk(schema["items"], f"{where}[]")

    for tool in tools.TOOLS:
        walk(tool.schema, tool.name)


@pytest.mark.req("REQ-QRY-05")
@pytest.mark.parametrize(("name", "limit"), LISTS.items())
def test_every_list_has_a_limit(name: str, limit: str) -> None:
    spec = tools.by_name(name).schema["properties"][limit]
    assert spec["minimum"] == 1
    assert spec["default"] <= spec["maximum"] <= 1000


@pytest.mark.req("REQ-QRY-06")
@pytest.mark.parametrize(
    ("tool", "arguments", "says"),
    [
        ("sound", {}, "ref is needed"),
        ("sound", {"ref": ""}, "ref is empty"),
        ("labels", {"limit": "ten"}, "limit must be a whole number"),
        ("labels", {"limit": True}, "limit must be a whole number"),
        ("labels", {"limit": 0}, "limit must be at least 1"),
        ("labels", {"limit": 501}, "limit must be at most 500"),
        ("labels", {"origin": "guess"}, "origin must be one of: folder, filename, analysis"),
        ("labels", {"nmae": "role"}, "unknown argument nmae (arguments: name, origin, limit)"),
        ("search_sounds", {"bpm": "fast"}, "bpm must be a number"),
        ("search_sounds", {"roles": "kick"}, "roles must be a list"),
        ("search_sounds", {"roles": ["kick", 3]}, "roles[1] must be a string"),
        ("search_sounds", {"words": ["x"] * 13}, "words takes at most 12 items"),
        ("report", {"id": "BM-DRUM-02", "score_hash": "XYZ"}, "should look like 0123456789abcdef"),
        ("ratings", {"since": "yesterday"}, "since should look like 2026-11-10"),
        ("ratings", {"state": "great"}, "state must be one of: resolved, unresolved"),
        ("sql", {"query": "x" * 20_001}, "query is longer than 20000 characters"),
        ("doctor", {"stages": ["core", "everything"]}, "stages[1] must be one of: core"),
        ("schema", {"table": "asset"}, "unknown argument table (arguments: none)"),
    ],
)
def test_bad_arguments_are_explained(tool: str, arguments: dict[str, Any], says: str) -> None:
    found = problems(tool, arguments)
    assert any(says in problem for problem in found), found


@pytest.mark.req("REQ-QRY-06")
def test_arguments_must_be_an_object() -> None:
    assert tools.check(tools.by_name("labels").schema, ["role"]) == ["arguments must be an object"]


@pytest.mark.req("REQ-QRY-06")
def test_run_refuses_bad_arguments_before_answering(lib: Library) -> None:
    asked: list[object] = []
    tool = stub(asked.append, tools._args({"n": {"type": "integer"}}))
    with pytest.raises(Unanswerable, match="bad arguments: n must be a whole number"):
        tools.run(tool, lib, {"n": 1.5})
    assert asked == []


@pytest.mark.req("REQ-QRY-03")
def test_run_fills_in_defaults(lib: Library) -> None:
    seen: list[dict[str, Any]] = []

    def answer(arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append(arguments)
        return {"ok": True}

    schema = tools._args({"limit": tools._limit(20, 200, "hits"), "word": {"type": "string"}})
    tool = stub(answer, schema)
    assert json.loads(tools.run(tool, lib, {"word": "909"})) == {"ok": True}
    assert tools.run(tool, lib, {"limit": 5}) == '{"ok":true}'
    assert seen == [{"limit": 20, "word": "909"}, {"limit": 5}]


@pytest.mark.req("REQ-QRY-05")
def test_long_answers_are_refused_not_cut_off(lib: Library) -> None:
    room = tools.MAX_ANSWER_CHARS - len('{"text":""}')
    assert len(tools.run(stub(lambda a: {"text": "x" * room}), lib, {})) == tools.MAX_ANSWER_CHARS
    with pytest.raises(
        Unanswerable, match=r"40,001 characters, over the 40,000 limit: ask for less"
    ):
        tools.run(stub(lambda a: {"text": "x" * (room + 1)}), lib, {})
    big = replace(stub(lambda a: {"text": "x" * (room + 1)}), narrower="one project at a time")
    with pytest.raises(Unanswerable, match=r"ask for less \(one project at a time\)"):
        tools.run(big, lib, {})
    assert "one project" in tools.by_name("benchmarks").narrower


@pytest.mark.req("REQ-QRY-05")
def test_answers_keep_text_as_written(lib: Library) -> None:
    text = tools.run(stub(lambda a: {"path": "Café/Ünder.wav"}), lib, {})
    assert "Café/Ünder.wav" in text


@pytest.mark.req("REQ-QRY-06")
@pytest.mark.parametrize(
    "failure", [sqlite3.OperationalError("database is locked"), PermissionError("report.json")]
)
def test_read_failures_say_to_try_again(lib: Library, failure: Exception) -> None:
    def fail(arguments: dict[str, Any]) -> None:
        raise failure

    with pytest.raises(Unanswerable, match=r"could not read what this needs \(.+\); try again"):
        tools.run(stub(fail), lib, {})


@pytest.mark.req("REQ-QRY-01")
def test_roles_come_from_the_name_vocabulary() -> None:
    roles = tools.by_name("search_sounds").schema["properties"]["roles"]["description"]
    assert "kick" in roles
    assert "hat_open" in roles
    assert roles.count("kick") == 1
