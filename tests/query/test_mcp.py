from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from mph.query import mcp, tools
from mph.query.answers import Library
from mph.query.tools import Tool
from tests.query.conftest import library_for, make_cfg

LEGACY = "2025-06-18"
MODERN = "2026-07-28"


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    return library_for(make_cfg(tmp_path), tmp_path)


@pytest.fixture
def server(lib: Library) -> mcp.Server:
    return mcp.Server(lib)


def request(method: str, params: dict[str, Any] | None = None, rid: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        message["params"] = params
    return message


def modern(method: str, params: dict[str, Any] | None = None, version: str = MODERN) -> Any:
    return request(method, {**(params or {}), "_meta": {mcp.VERSION_KEY: version}})


def opened(server: mcp.Server, version: str = LEGACY) -> mcp.Server:
    params = {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "t"}}
    server.handle(request("initialize", params, rid=0))
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return server


def result(response: dict[str, Any] | None) -> Any:
    assert response is not None
    assert "error" not in response, response
    return response["result"]


def failure(response: dict[str, Any] | None) -> tuple[int, str]:
    assert response is not None
    assert "result" not in response, response
    return response["error"]["code"], response["error"]["message"]


# --- clients that open with initialize -------------------------------------------------


@pytest.mark.req("REQ-QRY-01")
@pytest.mark.parametrize("version", mcp.LEGACY_VERSIONS)
def test_initialize_agrees_on_the_clients_version(server: mcp.Server, version: str) -> None:
    params = {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "t"}}
    found = result(server.handle(request("initialize", params)))
    assert found["protocolVersion"] == version
    assert found["capabilities"] == {"tools": {"listChanged": False}}
    assert found["serverInfo"]["name"] == "mph"
    assert "Start with overview" in found["instructions"]
    assert len(found["instructions"]) < 2048  # Claude Code keeps the first 4096 characters


@pytest.mark.req("REQ-QRY-01")
def test_initialize_offers_the_newest_it_knows_for_an_unknown_version(server: mcp.Server) -> None:
    found = result(server.handle(request("initialize", {"protocolVersion": "2099-01-01"})))
    assert found["protocolVersion"] == mcp.LEGACY_VERSIONS[0]


@pytest.mark.req("REQ-QRY-01")
def test_tools_need_initialize_first(server: mcp.Server) -> None:
    code, message = failure(server.handle(request("tools/list")))
    assert code == mcp.INVALID_REQUEST
    assert "send initialize first" in message
    assert result(server.handle(request("ping"))) == {}


@pytest.mark.req("REQ-QRY-01")
def test_tools_list_describes_read_only_tools(server: mcp.Server) -> None:
    found = result(opened(server).handle(request("tools/list")))
    assert [t["name"] for t in found["tools"]] == [t.name for t in tools.TOOLS]
    assert "ttlMs" not in found
    for tool in found["tools"]:
        assert set(tool) == {"name", "title", "description", "inputSchema", "annotations"}
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["openWorldHint"] is False


@pytest.mark.req("REQ-QRY-01")
def test_tools_call_answers_in_text(server: mcp.Server) -> None:
    found = result(opened(server).handle(request("tools/call", {"name": "overview"})))
    assert found["isError"] is False
    [content] = found["content"]
    assert content["type"] == "text"
    assert json.loads(content["text"])["catalogue"]["scanned"] is False


@pytest.mark.req("REQ-QRY-06")
def test_problems_claude_can_fix_are_tool_errors(server: mcp.Server) -> None:
    opened(server)
    for name, arguments in [("labels", {}), ("labels", {"limit": "all"}), ("sql", {})]:
        found = result(server.handle(request("tools/call", {"name": name, "arguments": arguments})))
        assert found["isError"] is True
        assert found["content"][0]["text"]
    bad = result(server.handle(request("tools/call", {"name": "sql", "arguments": {}})))
    assert bad["content"][0]["text"] == "bad arguments: query is needed"


@pytest.mark.req("REQ-QRY-06")
@pytest.mark.parametrize(
    ("params", "says"),
    [
        ({"name": "drop_everything"}, "Unknown tool: drop_everything"),
        ({}, "tools/call needs the tool's name"),
        ({"name": "overview", "arguments": ["x"]}, "arguments must be an object"),
    ],
)
def test_malformed_calls_are_protocol_errors(
    server: mcp.Server, params: dict[str, Any], says: str
) -> None:
    code, message = failure(opened(server).handle(request("tools/call", params)))
    assert (code, message) == (mcp.INVALID_PARAMS, says)


@pytest.mark.req("REQ-QRY-01")
def test_only_tools_are_offered(server: mcp.Server) -> None:
    opened(server)
    for method in ("resources/list", "prompts/list", "completion/complete", "sampling/x"):
        code, message = failure(server.handle(request(method)))
        assert (code, message) == (mcp.METHOD_NOT_FOUND, f"method not found: {method}")


@pytest.mark.req("REQ-QRY-01")
@pytest.mark.parametrize(
    ("message", "answer_id"),
    [
        ([request("ping")], None),  # batches were dropped from MCP
        ({"id": 3, "method": "ping"}, 3),  # no "jsonrpc": "2.0"
        ({"jsonrpc": "2.0", "id": 4, "method": 7}, 4),
        ({"jsonrpc": "2.0", "id": True, "method": "ping"}, None),
        ({"jsonrpc": "2.0", "id": None, "method": "ping"}, None),
        ({"jsonrpc": "2.0", "id": 5}, None),
        ("ping", None),
    ],
)
def test_invalid_requests(server: mcp.Server, message: object, answer_id: object) -> None:
    response = server.handle(message)
    assert response is not None
    assert response["id"] == answer_id
    assert response["error"]["code"] == mcp.INVALID_REQUEST


@pytest.mark.req("REQ-QRY-01")
def test_params_must_be_an_object(server: mcp.Server) -> None:
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]}
    assert failure(server.handle(message)) == (mcp.INVALID_PARAMS, "params must be an object")


@pytest.mark.req("REQ-QRY-01")
def test_notifications_and_stray_responses_get_no_answer(server: mcp.Server) -> None:
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 9, "result": {}}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 9, "error": {"code": 1}}) is None


@pytest.mark.req("REQ-QRY-01")
def test_a_bug_is_an_internal_error_and_serving_goes_on(
    lib: Library, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(library: Library, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("oops")

    server = opened(mcp.Server(lib, [Tool("broken", "Broken", "fails", tools._args(), broken)]))
    code, message = failure(server.handle(request("tools/call", {"name": "broken"})))
    assert (code, message) == (mcp.INTERNAL_ERROR, "internal error: RuntimeError('oops')")
    assert "Traceback" in capsys.readouterr().err
    assert result(server.handle(request("tools/list")))["tools"][0]["name"] == "broken"


# --- clients that state the version in every request (2026-07-28) ------------------------


@pytest.mark.req("REQ-QRY-01")
def test_discover_needs_no_handshake(server: mcp.Server) -> None:
    found = result(server.handle(modern("server/discover")))
    assert found["supportedVersions"] == [MODERN, *mcp.LEGACY_VERSIONS]
    assert found["capabilities"] == {"tools": {"listChanged": False}}
    assert found["resultType"] == "complete"
    assert found["_meta"][mcp.SERVER_INFO_KEY]["name"] == "mph"
    assert "Start with overview" in found["instructions"]
    assert result(server.handle(request("server/discover")))["resultType"] == "complete"


@pytest.mark.req("REQ-QRY-01")
def test_modern_requests_carry_their_version(server: mcp.Server) -> None:
    listed = result(server.handle(modern("tools/list")))
    assert (listed["resultType"], listed["cacheScope"]) == ("complete", "private")
    assert listed["ttlMs"] == mcp.LIST_TTL_MS
    assert len(listed["tools"]) == len(tools.TOOLS)
    called = result(server.handle(modern("tools/call", {"name": "benchmarks"})))
    assert called["isError"] is False
    assert called["_meta"][mcp.SERVER_INFO_KEY]["version"]
    assert server.legacy_version is None  # no session was opened


@pytest.mark.req("REQ-QRY-01")
def test_an_unknown_version_is_refused_with_the_known_ones(server: mcp.Server) -> None:
    response = server.handle(modern("tools/list", version="2027-01-01"))
    assert response is not None
    assert response["error"]["code"] == mcp.UNSUPPORTED_VERSION
    assert response["error"]["data"] == {
        "supported": [MODERN, *mcp.LEGACY_VERSIONS],
        "requested": "2027-01-01",
    }


# --- stdio -----------------------------------------------------------------------------


def lines(*messages: object) -> io.BytesIO:
    return io.BytesIO(
        b"".join((m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n" for m in messages)
    )


def served(server: mcp.Server, reader: io.BytesIO) -> list[dict[str, Any]]:
    writer = io.BytesIO()
    mcp.serve(server, reader, writer)
    out = writer.getvalue()
    assert out.endswith(b"\n")
    out.decode("ascii")  # one line per message, nothing but JSON
    return [json.loads(line) for line in out.splitlines()]


@pytest.mark.req("REQ-QRY-01")
def test_serve_answers_one_line_per_request(server: mcp.Server) -> None:
    reader = lines(
        request("initialize", {"protocolVersion": LEGACY}, rid=1),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        b"",
        b"   ",
        b"{not json",
        b"\xff\xfe",
        request("tools/call", {"name": "overview"}, rid=2),
    )
    answers = served(server, reader)
    assert [a["id"] for a in answers] == [1, None, None, 2]
    assert answers[1]["error"]["code"] == answers[2]["error"]["code"] == mcp.PARSE_ERROR
    assert answers[3]["result"]["isError"] is False


@pytest.mark.req("REQ-QRY-01")
def test_serve_skips_a_line_that_is_too_long(
    server: mcp.Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp, "MAX_LINE_BYTES", 64)
    long_line = json.dumps(request("ping", {"pad": "x" * 500})).encode()
    answers = served(server, lines(long_line, request("ping", rid=7)))
    assert answers[0]["error"] == {"code": mcp.INVALID_REQUEST, "message": "message too long"}
    assert answers[1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


@pytest.mark.req("REQ-QRY-01")
def test_serve_keeps_non_ascii_text_intact(full: Library) -> None:
    query = {"query": "SELECT 'Café Ünder' AS pack"}
    [answer] = served(
        mcp.Server(full), lines(modern("tools/call", {"name": "sql", "arguments": query}))
    )
    assert json.loads(answer["result"]["content"][0]["text"])["rows"] == [["Café Ünder"]]
