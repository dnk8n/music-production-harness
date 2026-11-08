"""Local listening page for the catalogue (spec/components/browser.md).

``mph catalogue browse`` serves one page on 127.0.0.1: search, play, queue,
layer, and walk a sound's lineage. It is read-only (REQ-BRW-01): audio is
streamed from wherever the catalogue says it lives, and only sounds the
catalogue knows by hash can be requested, so no path from the browser ever
reaches the filesystem. Formats a browser can't play (AIFF) are decoded by
ffmpeg into memory, never to disk (REQ-BRW-03, REQ-AUD-01).

The request handling is a plain function from (path, headers) to a response,
so tests drive it without opening a socket.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mph.catalogue import db, provenance, search
from mph.proc import Runner

HOST = "127.0.0.1"
HASH = re.compile(r"^[0-9a-f]{8,64}$")
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
NATIVE = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
}
DECODED_CACHE_ITEMS = 8  # recent AIFF decodes kept in memory, for seeking
MAX_LIMIT = 200
DECODE_TO_WAV = ("-c:a", "pcm_f32le", "-f", "wav", "-")  # float, native rate, to a pipe


@dataclass(slots=True)
class Response:
    status: HTTPStatus
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def text(status: HTTPStatus, message: str) -> Response:
    return Response(status, message.encode())


def as_json(payload: object) -> Response:
    return Response(HTTPStatus.OK, json.dumps(payload).encode(), "application/json")


def page() -> bytes:
    return resources.files("mph.catalogue").joinpath("browse.html").read_bytes()


# --- query parsing ------------------------------------------------------------------


def _number(params: Mapping[str, list[str]], name: str) -> float | None:
    raw = (params.get(name) or [""])[0].strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise search.QueryError(f"{name} must be a number") from exc


def query_from(params: Mapping[str, list[str]]) -> search.Query:
    key = (params.get("key") or [""])[0].strip()
    limit = int(_number(params, "limit") or 50)
    tolerance = _number(params, "bpm_tolerance")
    return search.Query(
        roles=tuple(r for r in params.get("role", []) if r),
        bpm=_number(params, "bpm"),
        bpm_tolerance=(tolerance / 100) if tolerance else search.DEFAULT_BPM_TOLERANCE,
        key=search.parse_key_query(key) if key else None,
        form=(params.get("form") or [""])[0] or None,
        min_lufs=_number(params, "min_lufs"),
        max_lufs=_number(params, "max_lufs"),
        max_seconds=_number(params, "max_seconds"),
        text=tuple((params.get("q") or [""])[0].split()),
        limit=max(1, min(limit, MAX_LIMIT)),
    )


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Inclusive (start, end) for a single ``bytes=`` range; None means the whole body."""
    match = RANGE.match(header or "")
    if not match or size == 0:
        return None
    first, last = match.groups()
    if not first:  # "bytes=-500": the last 500 bytes
        start = max(size - int(last or 0), 0)
        return start, size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    return (start, end) if start <= end else None


# --- the app ----------------------------------------------------------------------


class App:
    def __init__(self, db_file: Path, data_dir: Path, runner: Runner, port: int) -> None:
        self._db_file = db_file
        self._data_dir = data_dir
        self._runner = runner
        self._hosts = {f"{HOST}:{port}", f"localhost:{port}"}
        self._decoded: OrderedDict[str, bytes] = OrderedDict()

    def handle(self, target: str, headers: Mapping[str, str]) -> Response:
        # Only answer requests addressed to this machine, so a web page elsewhere
        # can't reach the catalogue through a DNS trick.
        if headers.get("Host") not in self._hosts:
            return text(HTTPStatus.FORBIDDEN, "local use only")
        url = urlsplit(target)
        if url.path == "/":
            return Response(HTTPStatus.OK, page(), "text/html; charset=utf-8")
        with closing(db.connect(self._db_file)) as conn:
            return self._route(conn, url.path, parse_qs(url.query), headers)

    def _route(
        self,
        conn: sqlite3.Connection,
        path: str,
        params: Mapping[str, list[str]],
        headers: Mapping[str, str],
    ) -> Response:
        if path == "/api/search":
            return self._search(conn, params)
        if path == "/api/derivatives":
            return as_json(derivatives(conn))
        if path.startswith(("/audio/", "/api/lineage/")):
            digest = path.rsplit("/", 1)[-1]
            if not HASH.match(digest) or not _known(conn, digest):
                return text(HTTPStatus.NOT_FOUND, "no such sound")
            if path.startswith("/api/lineage/"):
                return as_json(provenance.lineage_tree(conn, digest).to_dict())
            return self._audio(conn, digest, headers.get("Range"))
        return text(HTTPStatus.NOT_FOUND, "not found")

    def _search(self, conn: sqlite3.Connection, params: Mapping[str, list[str]]) -> Response:
        try:
            query = query_from(params)
        except search.QueryError as exc:
            return text(HTTPStatus.BAD_REQUEST, str(exc))
        result = search.search(conn, query)
        return as_json(
            {
                "matched": result.matched,
                "unanalysed_skipped": result.unanalysed_skipped,
                "hits": [hit.to_dict() for hit in result.hits],
            }
        )

    def _audio(self, conn: sqlite3.Connection, digest: str, range_header: str | None) -> Response:
        path = provenance.locate(conn, self._data_dir, digest)
        if path is None:
            return text(HTTPStatus.GONE, "not on disk: run `mph catalogue refresh`")
        if not path.is_file():
            return text(HTTPStatus.GONE, "moved or deleted since the last scan: rescan")
        container = conn.execute("SELECT container FROM asset WHERE hash = ?", (digest,)).fetchone()
        mime = NATIVE.get(str(container[0]) if container and container[0] else "wav")
        if mime is None:
            body = self._decode(digest, path)
            if body is None:
                return text(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "ffmpeg could not decode this")
            return partial(body, "audio/wav", range_header)
        with path.open("rb") as fh:  # read-only (REQ-BRW-01)
            return partial(fh.read(), mime, range_header)

    def _decode(self, digest: str, path: Path) -> bytes | None:
        if digest in self._decoded:
            self._decoded.move_to_end(digest)
            return self._decoded[digest]
        result = self._runner.capture(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), *DECODE_TO_WAV]
        )
        if not result.ok or not result.data:
            return None
        self._decoded[digest] = result.data
        while len(self._decoded) > DECODED_CACHE_ITEMS:
            self._decoded.popitem(last=False)
        return result.data


def partial(body: bytes, mime: str, range_header: str | None) -> Response:
    """Whole body, or the requested slice: browsers need ranges to seek."""
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    wanted = byte_range(range_header, len(body))
    if wanted is None:
        return Response(HTTPStatus.OK, body, mime, headers)
    start, end = wanted
    headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
    return Response(HTTPStatus.PARTIAL_CONTENT, body[start : end + 1], mime, headers)


def _known(conn: sqlite3.Connection, digest: str) -> bool:
    return conn.execute("SELECT 1 FROM asset WHERE hash = ?", (digest,)).fetchone() is not None


def derivatives(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Everything made so far, newest first, with the stashes each is kept in."""
    rows = conn.execute(
        "SELECT d.output_hash, r.kind, r.params_json, d.created_at, a.duration_s, "
        "(SELECT group_concat(name, ', ') FROM stash s WHERE s.asset_hash = d.output_hash) "
        "FROM derivation d JOIN recipe r ON r.id = d.recipe_id "
        "JOIN asset a ON a.hash = d.output_hash ORDER BY d.created_at DESC, d.output_hash"
    ).fetchall()
    return [
        {
            "hash": row[0],
            "label": f"{row[1]}("
            + ", ".join(f"{k}={v:g}" for k, v in json.loads(row[2]).items())
            + ")",
            "created_at": row[3],
            "duration_s": row[4],
            "stashes": row[5] or "",
        }
        for row in rows
    ]


# --- serving ----------------------------------------------------------------------


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "mph-browse"

        def do_GET(self) -> None:  # the method name http.server looks for
            response = app.handle(self.path, dict(self.headers.items()))
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            return  # quiet: the terminal shows the URL and nothing else

    return Handler


def serve(app: App, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), make_handler(app))
