from __future__ import annotations

import io
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from mph import net
from mph.net import HttpError, UrllibHttp


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Any:
        seen.append(request)
        if "fail" in request.full_url:
            raise urllib.error.HTTPError(
                request.full_url, 401, "no", Message(), io.BytesIO(b"bad token")
            )
        if "down" in request.full_url:
            raise urllib.error.URLError("unreachable")
        return FakeResponse(b'{"ok": true}' if "json" in request.full_url else b"x" * 100)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


@pytest.mark.req("REQ-ENG-03")
def test_json_and_form(opened: list[urllib.request.Request]) -> None:
    http = UrllibHttp()
    assert http.get_json("https://x.example/json", {"Authorization": "Bearer t"}) == {"ok": True}
    assert http.post_form("https://x.example/json", {"a": "1 2"}) == {"ok": True}
    assert str(opened[0].get_header("User-agent")).startswith("mph/")
    assert opened[0].get_header("Authorization") == "Bearer t"
    assert opened[1].data == b"a=1+2"


def test_download_and_size_limit(opened: list[urllib.request.Request], tmp_path: Path) -> None:
    http = UrllibHttp()
    dest = tmp_path / "d" / "a.wav"
    assert http.download("https://x.example/a.wav", dest, max_bytes=1000) == 100
    assert dest.read_bytes() == b"x" * 100
    with pytest.raises(HttpError, match="limit"):
        http.download("https://x.example/b.wav", tmp_path / "b.wav", max_bytes=10)
    assert not list(tmp_path.glob("b.wav*"))  # no partial file left behind


def test_errors_are_explained(opened: list[urllib.request.Request]) -> None:
    http = UrllibHttp()
    with pytest.raises(HttpError, match="refusing non-HTTPS"):
        http.get_json("http://x.example/json")
    with pytest.raises(HttpError, match=r"401 from x\.example: bad token"):
        http.get_json("https://x.example/fail")
    with pytest.raises(HttpError, match=r"cannot reach x\.example"):
        http.get_json("https://x.example/down")


def test_with_query_repeats_keys() -> None:
    assert net.with_query("https://a/b", [("fl[]", "x"), ("fl[]", "y"), ("n", 2)]) == (
        "https://a/b?fl%5B%5D=x&fl%5B%5D=y&n=2"
    )
