"""The one place that talks to the network (REQ-ENG-03).

Connectors depend on the ``Http`` protocol, so unit tests substitute a fake
and never reach the internet. Requests carry a plain ``mph`` user agent and
nothing that identifies Dean beyond the credentials a service requires.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from mph import __version__

USER_AGENT = f"mph/{__version__} (music production harness)"
CHUNK = 1 << 16


class HttpError(RuntimeError):
    pass


class Http(Protocol):
    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Any: ...

    def post_form(self, url: str, form: Mapping[str, str]) -> Any: ...

    def download(
        self, url: str, dest: Path, *, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> int:
        """Stream to ``dest`` (written via a temporary sibling); returns bytes written."""
        ...


def with_query(url: str, params: Sequence[tuple[str, str | int]]) -> str:
    """Pairs, not a dict, because some APIs repeat a key (archive.org's ``fl[]``)."""
    return f"{url}?{urllib.parse.urlencode([(k, str(v)) for k, v in params])}"


class UrllibHttp:
    def __init__(self, timeout_s: float = 60.0) -> None:
        self._timeout_s = timeout_s

    def _open(self, url: str, headers: Mapping[str, str] | None, data: bytes | None = None) -> Any:
        if not url.startswith("https://"):
            raise HttpError(f"refusing non-HTTPS URL: {url}")
        request = urllib.request.Request(  # noqa: S310 - scheme checked above
            url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            return urllib.request.urlopen(request, timeout=self._timeout_s)  # noqa: S310
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            raise HttpError(
                f"{exc.code} from {urllib.parse.urlsplit(url).netloc}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HttpError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {exc}") from exc

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> Any:
        with self._open(url, headers) as response:
            return json.load(response)

    def post_form(self, url: str, form: Mapping[str, str]) -> Any:
        body = urllib.parse.urlencode(form).encode()
        with self._open(url, None, body) as response:
            return json.load(response)

    def download(
        self, url: str, dest: Path, *, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        written = 0
        try:
            with self._open(url, headers) as response, partial.open("wb") as out:
                while chunk := response.read(CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise HttpError(f"bigger than the {max_bytes // 1_000_000} MB limit")
                    out.write(chunk)
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)
        return written
