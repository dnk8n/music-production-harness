"""Downloads from free sources, with licence and attribution (REQ-CAT-07).

Connectors find sounds; the ``Fetcher`` downloads them into
``data_dir/downloads/<connector>/<id>/`` and records each one like any other
catalogued file, plus a ``download`` row saying where it came from, who made it
and under what licence, and what the file says about itself (REQ-CAT-12). A
deleted download can always be fetched again from its recorded URL, and must
come back byte-identical.

- **freesound:** text search with the API key. Original files need a one-time
  browser sign-in (OAuth2, ``mph catalogue auth freesound``); without it,
  ``--previews`` takes the lossy MP3 previews instead.
- **archive:** any Internet Archive audio item. One file per item, the best
  quality available (FLAC, then WAV, then the highest-bitrate MP3).
- **librivox:** the Internet Archive's LibriVox collection: public-domain
  spoken word, which Dean's library lacks.

Licences are normalised to short names. ``CLEAR`` lists the ones that allow
publishing a track (attribution is fine; non-commercial and share-alike are
not), which is what the reserved ``licence:clear`` label depends on.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mph import secrets
from mph.catalogue import embedded, names
from mph.catalogue.probe import probe
from mph.catalogue.scanner import blake2b_file, utc_now
from mph.net import Http, HttpError, with_query
from mph.proc import Runner

DOWNLOADS = "downloads"
CLEAR = frozenset({"cc0", "public-domain", "cc-by"})
DEFAULT_MAX_BYTES = 200_000_000
SAFE = re.compile(r"[^A-Za-z0-9._ -]+")

FREESOUND_API = "https://freesound.org/apiv2"
FREESOUND_FIELDS = "id,name,username,license,type,tags,url,previews,duration"
FREESOUND_CLEAR_FILTER = 'license:("Creative Commons 0" OR "Attribution")'
REFRESH_ACCOUNT = "freesound.refresh_token"
PKCE_VERIFIER_BYTES = 48  # 64 URL-safe characters; RFC 7636 allows 43-128
ARCHIVE = "https://archive.org"
# Best first. Matched against archive.org's "format" names.
ARCHIVE_FORMATS = (
    "Flac",
    "24bit Flac",
    "WAVE",
    "AIFF",
    "VBR MP3",
    "128Kbps MP3",
    "64Kbps MP3",
    "Ogg Vorbis",
)


class RemoteError(ValueError):
    pass


def short_licence(text: str) -> str:
    """'http://creativecommons.org/licenses/by/4.0/' -> 'cc-by', and so on."""
    t = text.lower()
    if "publicdomain/zero" in t or "creative commons 0" in t:
        return "cc0"
    if "publicdomain" in t or "public domain" in t:
        return "public-domain"
    if "sampling+" in t or "sampling-plus" in t:
        return "sampling-plus"
    match = re.search(r"licenses/(by(?:-nc)?(?:-sa|-nd)?)/", t)
    if match:
        return f"cc-{match.group(1)}"
    words = {"attribution noncommercial": "cc-by-nc", "attribution": "cc-by"}
    return next((short for word, short in words.items() if word in t), "unknown")


def safe_name(text: str) -> str:
    cleaned = SAFE.sub("_", Path(text).name).strip(" .")
    return cleaned[:120] or "sound"


@dataclass(frozen=True, slots=True)
class RemoteSound:
    remote_id: str
    title: str
    creator: str
    url: str  # the bytes
    page_url: str  # the human page
    licence_url: str
    filename: str
    licence: str = ""  # filled from licence_url when empty
    keywords: tuple[str, ...] = ()

    @property
    def short_licence(self) -> str:
        return self.licence or short_licence(self.licence_url)


class Connector(Protocol):
    name: str

    def search(self, query: str, limit: int, *, clear_only: bool) -> list[RemoteSound]: ...

    def headers_for(self, sound_url: str) -> dict[str, str]: ...


# --- Freesound -------------------------------------------------------------------


def new_verifier() -> str:
    return base64.urlsafe_b64encode(os.urandom(PKCE_VERIFIER_BYTES)).decode().rstrip("=")


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass(slots=True)
class Freesound:
    http: Http
    store: secrets.SecretStore
    previews: bool = False
    name: str = "freesound"
    _access: str | None = field(default=None, repr=False)
    _verifier: str | None = field(default=None, repr=False)

    def _keys(self) -> dict[str, str]:
        values = secrets.read(self.store, secrets.credential_for("freesound"))
        if values is None:
            raise RemoteError("Freesound isn't set up: run `mph secrets set freesound`")
        return values

    def search(self, query: str, limit: int, *, clear_only: bool) -> list[RemoteSound]:
        params: list[tuple[str, str | int]] = [
            ("query", query),
            ("fields", FREESOUND_FIELDS),
            ("page_size", min(limit, 150)),
            ("token", self._keys()["client_secret"]),
        ]
        if clear_only:
            params.append(("filter", FREESOUND_CLEAR_FILTER))
        if not self.previews:
            self.access_token()  # fail early, before searching, if sign-in is missing
        data = self.http.get_json(with_query(f"{FREESOUND_API}/search/text/", params))
        return [self._sound(item) for item in data.get("results", [])[:limit]]

    def _sound(self, item: Mapping[str, Any]) -> RemoteSound:
        sound_id = str(item["id"])
        if self.previews:
            url = item["previews"]["preview-hq-mp3"]
            filename = f"{safe_name(Path(str(item['name'])).stem)}.preview.mp3"
        else:
            url = f"{FREESOUND_API}/sounds/{sound_id}/download/"
            stem = Path(str(item["name"])).stem
            filename = f"{safe_name(stem)}.{item.get('type') or 'wav'}"
        return RemoteSound(
            remote_id=sound_id,
            title=str(item["name"]),
            creator=str(item.get("username", "")),
            url=url,
            page_url=str(item.get("url", f"https://freesound.org/s/{sound_id}/")),
            licence_url=str(item.get("license", "")),
            filename=filename,
            keywords=tuple(str(t) for t in item.get("tags", [])),
        )

    def headers_for(self, sound_url: str) -> dict[str, str]:
        if sound_url.startswith(f"{FREESOUND_API}/sounds/"):
            return {"Authorization": f"Bearer {self.access_token()}"}
        return {}

    def access_token(self) -> str:
        """A fresh access token from the stored refresh token (Freesound rotates both)."""
        if self._access:
            return self._access
        refresh = self.store.get(REFRESH_ACCOUNT)
        if not refresh:
            raise RemoteError(
                "original Freesound files need a one-time sign-in: run "
                "`mph catalogue auth freesound` (or add --previews for lossy MP3 previews)"
            )
        keys = self._keys()
        tokens = self.http.post_form(
            f"{FREESOUND_API}/oauth2/access_token/",
            {
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            },
        )
        self._remember(tokens)
        return str(tokens["access_token"])

    def authorize_url(self, verifier: str | None = None) -> str:
        """The page Dean approves. Freesound requires PKCE (RFC 7636, S256): the
        link carries a hash of a one-off secret, and ``sign_in`` proves we hold it."""
        self._verifier = verifier or new_verifier()
        return with_query(
            f"{FREESOUND_API}/oauth2/authorize/",
            [
                ("client_id", self._keys()["client_id"]),
                ("response_type", "code"),
                ("code_challenge", pkce_challenge(self._verifier)),
                ("code_challenge_method", "S256"),
            ],
        )

    def sign_in(self, code: str) -> None:
        if self._verifier is None:
            raise RemoteError("open the sign-in link first (`mph catalogue auth freesound`)")
        keys = self._keys()
        tokens = self.http.post_form(
            f"{FREESOUND_API}/oauth2/access_token/",
            {
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "grant_type": "authorization_code",
                "code": code.strip(),
                "code_verifier": self._verifier,
            },
        )
        self._remember(tokens)

    def _remember(self, tokens: Mapping[str, Any]) -> None:
        if "access_token" not in tokens or "refresh_token" not in tokens:
            raise RemoteError(f"Freesound sign-in failed: {tokens}")
        if not self.store.put(REFRESH_ACCOUNT, str(tokens["refresh_token"])):
            raise RemoteError("could not save the Freesound sign-in to the Keychain")
        self._access = str(tokens["access_token"])


# --- Internet Archive and LibriVox -----------------------------------------------------


@dataclass(slots=True)
class Archive:
    http: Http
    name: str = "archive"
    collection: str | None = None
    default_licence: str = ""  # used when an item states none

    def search(self, query: str, limit: int, *, clear_only: bool) -> list[RemoteSound]:
        terms = [f"({query})", "mediatype:audio"]
        if self.collection:
            terms.append(f"collection:{self.collection}")
        params: list[tuple[str, str | int]] = [("q", " AND ".join(terms))]
        params += [("fl[]", f) for f in ("identifier", "title", "creator", "licenseurl")]
        params += [("rows", limit), ("output", "json")]
        docs = self.http.get_json(with_query(f"{ARCHIVE}/advancedsearch.php", params))
        found = []
        for doc in docs.get("response", {}).get("docs", []):
            sound = self._best_file(doc)
            if sound and (not clear_only or sound.short_licence in CLEAR):
                found.append(sound)
        return found

    def _best_file(self, doc: Mapping[str, Any]) -> RemoteSound | None:
        identifier = str(doc["identifier"])
        meta = self.http.get_json(f"{ARCHIVE}/metadata/{identifier}")
        files = [f for f in meta.get("files", []) if f.get("format") in ARCHIVE_FORMATS]
        if not files:
            return None
        best = min(files, key=lambda f: (ARCHIVE_FORMATS.index(f["format"]), f["name"]))
        creator = doc.get("creator", "")
        licence_url = str(doc.get("licenseurl", "") or "")
        return RemoteSound(
            remote_id=f"{identifier}/{best['name']}",
            title=str(doc.get("title", identifier)),
            creator=", ".join(creator) if isinstance(creator, list) else str(creator),
            url=f"{ARCHIVE}/download/{identifier}/{best['name']}",
            page_url=f"{ARCHIVE}/details/{identifier}",
            licence_url=licence_url,
            filename=safe_name(str(best["name"])),
            licence="" if licence_url else self.default_licence,
        )

    def headers_for(self, sound_url: str) -> dict[str, str]:
        return {}


def librivox(http: Http) -> Archive:
    # LibriVox recordings are dedicated to the public domain.
    return Archive(http, "librivox", "librivoxaudio", "public-domain")


# --- fetching and recording ----------------------------------------------------------


@dataclass(slots=True)
class FetchReport:
    fetched: list[str] = field(default_factory=list)  # "hash title (licence)"
    already: int = 0
    failed: list[str] = field(default_factory=list)


class Fetcher:
    def __init__(
        self,
        conn: sqlite3.Connection,
        runner: Runner,
        http: Http,
        data_dir: Path,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._conn = conn
        self._runner = runner
        self._http = http
        self._data_dir = data_dir
        self._clock = clock

    def folder(self, connector: str) -> Path:
        return self._data_dir / DOWNLOADS / connector

    def fetch(
        self,
        connector: Connector,
        query: str,
        limit: int,
        *,
        clear_only: bool,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> FetchReport:
        report = FetchReport()
        for sound in connector.search(query, limit, clear_only=clear_only):
            if self._have(connector.name, sound.remote_id):
                report.already += 1
                continue
            try:
                digest = self._get(connector, sound, max_bytes)
            except HttpError as exc:
                report.failed.append(f"{sound.title}: {exc}")
                continue
            report.fetched.append(f"{digest[:12]} {sound.title} ({sound.short_licence})")
        return report

    def refresh(self, connectors: Mapping[str, Connector]) -> FetchReport:
        """Fetch again any download whose file has gone, and check it's the same sound."""
        report = FetchReport()
        rows = self._conn.execute(
            "SELECT source_id, rel_path, url, asset_hash, title FROM download ORDER BY 1, 2"
        ).fetchall()
        for source_id, rel_path, url, digest, title in rows:
            path = self.folder(source_id) / rel_path
            if path.is_file():
                report.already += 1
                continue
            connector = connectors.get(source_id)
            try:
                if connector is None:
                    raise HttpError(f"no connector named {source_id}")
                self._http.download(
                    url, path, max_bytes=DEFAULT_MAX_BYTES, headers=connector.headers_for(url)
                )
            except (HttpError, RemoteError) as exc:
                report.failed.append(f"{title}: {exc}")
                continue
            if blake2b_file(path) != digest:
                path.unlink()
                report.failed.append(f"{title}: the file online has changed since it was fetched")
                continue
            report.fetched.append(f"{digest[:12]} {title}")
        return report

    def _have(self, source_id: str, remote_id: str) -> bool:
        row = self._conn.execute(
            "SELECT rel_path FROM download WHERE source_id = ? AND remote_id = ?",
            (source_id, remote_id),
        ).fetchone()
        return row is not None and (self.folder(source_id) / row[0]).is_file()

    def _get(self, connector: Connector, sound: RemoteSound, max_bytes: int) -> str:
        rel = f"{safe_name(sound.remote_id.replace('/', '_'))}/{sound.filename}"
        path = self.folder(connector.name) / rel
        self._http.download(
            sound.url, path, max_bytes=max_bytes, headers=connector.headers_for(sound.url)
        )
        digest = blake2b_file(path)
        self._record(connector.name, sound, rel, path, digest)
        return digest

    def _record(
        self, source_id: str, sound: RemoteSound, rel: str, path: Path, digest: str
    ) -> None:
        facts = probe(self._runner, path)
        found = embedded.read(path, facts)  # what the file says about itself (REQ-CAT-12)
        stat = path.stat()
        now = self._clock()
        with self._conn:
            self._conn.execute(
                "INSERT INTO source (id, kind, location, licence) "
                "VALUES (?, 'remote', ?, 'varies') "
                "ON CONFLICT (id) DO UPDATE SET location = excluded.location",
                (source_id, str(self.folder(source_id))),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO asset (hash, bytes, container, codec, sample_rate, "
                "bit_depth, channels, duration_s, origin, probe_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'download', ?)",
                (
                    digest,
                    stat.st_size,
                    facts.container,
                    facts.codec,
                    facts.sample_rate,
                    facts.bit_depth,
                    facts.channels,
                    facts.duration_s,
                    facts.error,
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO source_file (source_id, rel_path, size, mtime_ns, "
                "asset_hash, scan_id) VALUES (?, ?, ?, ?, ?, 0)",
                (source_id, rel, stat.st_size, stat.st_mtime_ns, digest),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO download VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    sound.remote_id,
                    digest,
                    rel,
                    sound.url,
                    sound.page_url,
                    sound.title,
                    sound.creator,
                    sound.short_licence,
                    sound.licence_url,
                    now,
                ),
            )
            self._tag(digest, source_id, sound)
            embedded.store(self._conn, digest, found, now)

    def _tag(self, digest: str, source_id: str, sound: RemoteSound) -> None:
        rows: list[tuple[str, str, str, float]] = [("pack", source_id, "folder", 1.0)]
        text = " ".join((sound.title, *sound.keywords))
        rows += [(p.name, p.value, p.origin, p.confidence) for p in names.priors_from_text(text)]
        for name, value, origin, confidence in rows:
            self._conn.execute(
                "INSERT OR IGNORE INTO tag (asset_hash, name, value, origin, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (digest, name, value, origin, confidence),
            )


def attribution(conn: sqlite3.Connection, hashes: Sequence[str]) -> list[str]:
    """Credit lines for a set of sounds, e.g. for a track's liner notes."""
    lines = []
    for digest in hashes:
        row = conn.execute(
            "SELECT title, creator, licence, page_url FROM download WHERE asset_hash = ?",
            (digest,),
        ).fetchone()
        if row:
            by = f" by {row[1]}" if row[1] else ""
            lines.append(f'"{row[0]}"{by} ({row[2]}), {row[3]}')
    return lines
