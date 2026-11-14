"""scripts/kit_picks.py, the half of scripts/kit-diff that reads the catalogue."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import ModuleType

import pytest

from mph.bench import kit
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
def test_sounds_right_inside_a_pack_group_by_the_pack(kit_picks: ModuleType) -> None:
    where = {"s1": "Stab Pack/Stab 1.wav", "s2": "Stab Pack/Stab 2.wav"}
    before = {"roles": {"s1": ["stab"], "s2": ["stab"]}, "paths": where}
    after = {"roles": {"s1": ["bass", "stab"], "s2": ["bass", "stab"], "f00d": ["kick"]}}
    assert kit_picks.changed_roles(before, {**after, "paths": where}) == {
        "+ bass       Stab Pack": ["Stab Pack/Stab 1.wav", "Stab Pack/Stab 2.wav"],
        "+ kick       f00d": ["f00d"],  # a sound with no path goes by its hash
    }


@pytest.mark.req("REQ-ENG-08")
def test_changed_picks_list_only_the_voices_that_change(kit_picks: ModuleType) -> None:
    before = {"picks": {"BM-1 a": {"kick": "K1", "hat": "H1"}}}
    after = {"picks": {"BM-1 a": {"kick": "K2", "hat": "H1"}, "BM-2 a": {"bell": "mph_bell"}}}
    assert kit_picks.changed_picks(before, after) == {
        "BM-1 a": ["kick: K1  ->  K2"],
        "BM-2 a": ["bell: -  ->  mph_bell"],
    }


@pytest.mark.req("REQ-ENG-08")
@pytest.mark.req("REQ-CAT-12")
def test_a_break_shows_the_tempo_it_is_sliced_by(kit_picks: ModuleType) -> None:
    sample = kit.Choice("break", "mph_play2", "why", "/s/b.wav", "ab" * 32, "Breaks/Amen.wav")
    assert kit_picks.pick(sample) == f"Breaks/Amen.wav ({'ab' * 6})"
    sliced = kit.Choice(
        "break", "mph_play2", "why", "/s/b.wav", "ab" * 32, "Breaks/Amen.wav", bpm=136
    )
    assert kit_picks.pick(sliced) == f"Breaks/Amen.wav ({'ab' * 6}) at 136 BPM"
    assert kit_picks.pick(kit.Choice("bell", "mph_bell", "why", label="mph_bell")) == "mph_bell"


@pytest.mark.req("REQ-ENG-08")
@pytest.mark.req("REQ-CAT-12")
def test_tempo_key_and_form_changes_group_by_field_and_folder(
    kit_picks: ModuleType, conn: sqlite3.Connection
) -> None:
    conn.executemany(
        "INSERT INTO tag VALUES (?, ?, ?, ?, ?)",
        [
            ("k", "role", "kick", "filename", 0.6),  # roles are compared on their own
            ("k", "form", "oneshot", "embedded", 0.8),
            ("d", "bpm", "100", "filename", 0.6),
            ("d", "bpm", "96", "embedded", 0.9),  # the strongest label, as search weighs them
        ],
    )
    assert kit_picks.labels(conn) == {"k": {"form": "oneshot"}, "d": {"bpm": "96"}}
    where = kit_picks.paths(conn)
    before = {"labels": {"k": {"form": "oneshot"}, "d": {"bpm": "120"}}, "paths": where}
    after = {
        "labels": {"k": {"form": "oneshot"}, "d": {"bpm": "96", "key": "A:minor"}},
        "paths": where,
    }
    assert kit_picks.changed_labels(before, after) == {
        "bpm  downloads/freesound": ["downloads/freesound/Djembe Loop 07: 120 -> 96"],
        "key  downloads/freesound": ["downloads/freesound/Djembe Loop 07: - -> A:minor"],
    }


@pytest.mark.req("REQ-ENG-08")
def test_compare_prints_every_kind_of_change(
    kit_picks: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    where = {"k": "Pack/Kicks/Kick 1.wav", "l": "Pack/Loops/Loop 1.wav"}
    sides = {
        "base": {"names_version": 5, "reader_version": None, "picks": {"BM-1 a": {"kick": "K1"}},
                 "roles": {"k": ["kick"]}, "labels": {"l": {"bpm": "120"}}, "paths": where},
        "ref": {"names_version": 5, "reader_version": 1, "picks": {"BM-1 a": {"kick": "K1"}},
                "roles": {"k": ["kick"]}, "labels": {"l": {"bpm": "124", "form": "loop"}},
                "paths": where},
    }  # fmt: skip
    for side, found in sides.items():
        (tmp_path / f"{side}.json").write_text(json.dumps(found))
    kit_picks.compare(tmp_path / "base.json", tmp_path / "ref.json")
    assert capsys.readouterr().out.splitlines() == [
        "names v5, files' own metadata not read  ->  names v5, files' own metadata reader v1",
        "",
        "Kit picks: 0 of 1 takes would pick different sounds",
        "",
        "Roles: 0 sounds change (- loses, + gains), by top two folders",
        "",
        "Tempo, key and form: 1 sounds change (old -> new), by top two folders",
        "      1  bpm  Pack/Loops",
        "         Pack/Loops/Loop 1.wav: 120 -> 124",
        "      1  form Pack/Loops",
        "         Pack/Loops/Loop 1.wav: - -> loop",
    ]
