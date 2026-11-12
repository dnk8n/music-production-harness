"""scripts/kit_picks.py, the half of scripts/kit-diff that reads the catalogue."""

from __future__ import annotations

import importlib.util
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import ModuleType

import pytest

from mph.catalogue import db

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kit_picks.py"


@pytest.fixture(scope="module")
def kit_picks() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kit_picks", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with closing(db.connect(tmp_path / db.FILENAME)) as connection, connection:
        connection.execute("INSERT INTO source VALUES ('dj', 'local', '/s', 'pack', NULL)")
        connection.execute(
            "INSERT INTO source VALUES ('freesound', 'remote', '/d', 'varies', NULL)"
        )
        for digest, origin in (("k", "source"), ("d", "download"), ("both", "download")):
            connection.execute(
                "INSERT INTO asset (hash, bytes, origin) VALUES (?, 1, ?)", (digest, origin)
            )
        connection.executemany(
            "INSERT INTO source_file VALUES (?, ?, 1, 1, ?, 1)",
            [
                ("dj", "Pack/Kicks/Kick 1.wav", "k"),
                ("freesound", "485475/Djembe Loop 07.wav", "d"),
                ("freesound", "9/Hand Drum.wav", "both"),
                ("dj", "Hand Drums/Djembe.wav", "both"),
            ],
        )
        for digest, remote_id, title in (("d", "485475", "Djembe Loop 07"), ("both", "9", "Hd")):
            connection.execute(
                "INSERT INTO download VALUES ('freesound', ?, ?, 'x', 'u', 'p', ?, 'c', 'cc0', "
                "'l', 't')",
                (remote_id, digest, title),
            )
        yield connection


@pytest.mark.req("REQ-ENG-08")
def test_sounds_are_named_by_path_or_by_download_title(
    kit_picks: ModuleType, conn: sqlite3.Connection
) -> None:
    assert kit_picks.paths(conn) == {
        "k": "Pack/Kicks/Kick 1.wav",
        "d": "downloads/freesound/Djembe Loop 07",  # not 485475/..., or its hash
        "both": "Hand Drums/Djembe.wav",  # a path in a sample folder wins
    }


@pytest.mark.req("REQ-ENG-08")
def test_role_changes_group_downloads_by_source(
    kit_picks: ModuleType, conn: sqlite3.Connection
) -> None:
    where = kit_picks.paths(conn)
    before = {"roles": {"d": ["djembe"], "k": ["drums", "kick"]}, "paths": where}
    after = {"roles": {"d": ["djembe", "drums"], "k": ["kick"]}, "paths": where}
    assert kit_picks.changed_roles(before, after) == {
        "+ drums      downloads/freesound": ["downloads/freesound/Djembe Loop 07"],
        "- drums      Pack/Kicks": ["Pack/Kicks/Kick 1.wav"],
    }


@pytest.mark.req("REQ-ENG-08")
def test_changed_picks_list_only_the_voices_that_change(kit_picks: ModuleType) -> None:
    before = {"picks": {"BM-1 a": {"kick": "K1", "hat": "H1"}}}
    after = {"picks": {"BM-1 a": {"kick": "K2", "hat": "H1"}, "BM-2 a": {"bell": "mph_bell"}}}
    assert kit_picks.changed_picks(before, after) == {
        "BM-1 a": ["kick: K1  ->  K2"],
        "BM-2 a": ["bell: -  ->  mph_bell"],
    }
