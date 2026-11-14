from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mph import cli, config
from mph.bench import kit
from mph.catalogue import commands, db, search
from mph.catalogue.search import Query


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def add(
    conn: sqlite3.Connection,
    digest: str,
    path: str,
    tags: list[tuple[str, str, str, float]],
    *,
    features: dict[str, object] | None = None,
    duration: float = 2.0,
    source: str = "dj",
) -> None:
    licence = "cc0" if source == "fs" else "commercial-pack"
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO source VALUES (?, 'local', '/x', ?, NULL)", (source, licence)
        )
        conn.execute(
            "INSERT INTO asset (hash, bytes, duration_s, origin) VALUES (?, 1, ?, 'source')",
            (digest, duration),
        )
        conn.execute("INSERT INTO source_file VALUES (?, ?, 1, 1, ?, 1)", (source, path, digest))
        for name, value, origin, confidence in tags:
            conn.execute(
                "INSERT INTO tag VALUES (?, ?, ?, ?, ?)", (digest, name, value, origin, confidence)
            )
        if features is not None:
            conn.execute(
                "INSERT INTO analysis VALUES (?, 'core', 2, 't', ?, NULL)",
                (digest, json.dumps(features)),
            )


@pytest.fixture
def library(conn: sqlite3.Connection) -> sqlite3.Connection:
    add(
        conn,
        "hat1",
        "Pack/Hats/Open Hat 909.wav",
        [("role", "hat_open", "filename", 0.6), ("role", "hat", "folder", 0.5)],
        features={"form": "oneshot", "lufs": -14.0},
        duration=0.4,
    )
    add(
        conn,
        "hat2",
        "Pack/Loops/Hat Loop 128bpm.wav",
        [("role", "hat", "filename", 0.6), ("bpm", "128", "filename", 0.6)],
        features={"form": "loop", "lufs": -18.0},
    )
    add(
        conn,
        "hat3",
        "Pack/Loops/Hat Loop 140bpm.wav",
        [("role", "hat", "filename", 0.6), ("bpm", "140", "filename", 0.6)],
    )
    add(
        conn,
        "bass_dm",
        "Synthpop (Dm, 87bpm)/Bass/Bass1.wav",
        [
            ("role", "bass", "folder", 0.5),
            ("key", "D:minor", "folder", 0.4),
            ("key", "E:major", "analysis", 0.9),  # the pack label wins
            ("bpm", "87", "folder", 0.4),
            ("bpm", "174", "analysis", 0.9),  # double time still matches 87
        ],
    )
    add(
        conn,
        "bass_gm",
        "Industrial (Gm, 140bpm)/Bass/Bass 0001.wav",
        [("role", "bass", "folder", 0.5), ("key", "G:minor", "folder", 0.4)],
    )
    add(
        conn,
        "bass_e",
        "Trap (Em)/Bass/Sub.wav",
        [("role", "bass", "folder", 0.5), ("key", "E:minor", "folder", 0.4)],
    )
    add(
        conn,
        "drum_fs",
        "freesound/djembe_slap.wav",
        [("role", "djembe", "filename", 0.6)],
        source="fs",
        duration=0.3,
    )
    return conn


def paths(result: search.Result) -> list[str]:
    return [hit.path for hit in result.hits]


@pytest.mark.req("REQ-CAT-05")
def test_role_and_tempo_rank_and_explain(library: sqlite3.Connection) -> None:
    result = search.search(library, Query(roles=("hat", "hat_open"), bpm=128))
    assert paths(result) == ["Pack/Loops/Hat Loop 128bpm.wav", "Pack/Hats/Open Hat 909.wav"]
    loop, oneshot = result.hits
    assert loop.score > oneshot.score
    assert "128 BPM (filename)" in loop.reasons
    assert "one-shot, fits any tempo" in oneshot.reasons
    assert "hat_open (filename 0.6)" in oneshot.reasons


@pytest.mark.req("REQ-CAT-05")
def test_keys_match_relatives_and_neighbours_and_labels_win(
    library: sqlite3.Connection,
) -> None:
    result = search.search(library, Query(roles=("bass",), key="D:minor"))
    assert paths(result) == [
        "Synthpop (Dm, 87bpm)/Bass/Bass1.wav",  # same key, from the label
        "Industrial (Gm, 140bpm)/Bass/Bass 0001.wav",  # neighbour on the wheel
    ]
    assert "a neighbour of D:minor" in result.hits[1].reasons[1]
    relative = search.search(library, Query(roles=("bass",), key="F:major"))
    assert paths(relative)[0] == "Synthpop (Dm, 87bpm)/Bass/Bass1.wav"


@pytest.mark.req("REQ-CAT-05")
def test_half_and_double_time_match(library: sqlite3.Connection) -> None:
    result = search.search(library, Query(roles=("bass",), bpm=87))
    top = result.hits[0]
    assert top.path == "Synthpop (Dm, 87bpm)/Bass/Bass1.wav"
    assert top.reasons[1] == "174 BPM (analysis)"
    assert "tempo unknown" in result.hits[1].reasons


@pytest.mark.req("REQ-CAT-05")
def test_filters_loudness_length_licence_text_form(library: sqlite3.Connection) -> None:
    quiet = search.search(library, Query(max_lufs=-16))
    assert paths(quiet) == ["Pack/Loops/Hat Loop 128bpm.wav"]
    assert quiet.unanalysed_skipped == 5
    short = search.search(library, Query(max_seconds=0.5))
    assert set(paths(short)) == {"Pack/Hats/Open Hat 909.wav", "freesound/djembe_slap.wav"}
    assert paths(search.search(library, Query(licence="cc0"))) == ["freesound/djembe_slap.wav"]
    assert paths(search.search(library, Query(text=("909", "OPEN")))) == [
        "Pack/Hats/Open Hat 909.wav"
    ]
    assert paths(search.search(library, Query(text=("djembe",)))) == ["freesound/djembe_slap.wav"]
    assert paths(search.search(library, Query(form="oneshot"))) == ["Pack/Hats/Open Hat 909.wav"]
    assert search.search(library, Query(min_seconds=1, max_seconds=1.5)).hits == []


@pytest.mark.req("REQ-CAT-05")
@pytest.mark.parametrize(
    ("text", "found"),
    [
        (("dub",), ["Dub Techno/Chord 1.wav"]),  # not DUBSTEP
        (("m1",), ["Korg M1/Organ 2.wav"]),  # not Tom1
        (("hand",), ["Hand Drums/Conga.wav"]),  # not Ghandi
        (("909",), ["Pack/Hats/Open Hat 909.wav", "TR909/ClosedHat.wav"]),  # TR909 is tr 909
        (("closed", "hats"), ["TR909/ClosedHat.wav"]),  # joined-up words, plurals
        (("tom",), ["Toms/Tom1.wav"]),
        (("crash",), ["Crashes/Big.wav"]),
        (("open hat",), ["Pack/Hats/Open Hat 909.wav"]),  # a phrase, in order
        (("hat open",), ["Pack/Hats/Open Hat 909.wav"]),  # ... or a label, hat_open
        (("techno", "dubstep"), []),
    ],
)
def test_words_match_whole_words(
    library: sqlite3.Connection, text: tuple[str, ...], found: list[str]
) -> None:
    for n, path in enumerate(
        (
            "DUBSTEP/Kick 1.wav",
            "Dub Techno/Chord 1.wav",
            "Toms/Tom1.wav",
            "Korg M1/Organ 2.wav",
            "Ghandi/Speech.wav",
            "Hand Drums/Conga.wav",
            "TR909/ClosedHat.wav",
            "Crashes/Big.wav",
        )
    ):
        add(library, f"w{n}", path, [])
    assert sorted(paths(search.search(library, Query(text=text)))) == found


@pytest.mark.req("REQ-CAT-05")
def test_hits_carry_the_licence_a_render_would_record(library: sqlite3.Connection) -> None:
    with library:
        library.execute("INSERT INTO source VALUES ('freesound', 'remote', '/d', 'varies', NULL)")
        library.execute("INSERT INTO asset (hash, bytes, origin) VALUES ('rain', 1, 'download')")
        library.execute(
            "INSERT INTO source_file VALUES ('freesound', '9/rain.wav', 1, 1, 'rain', 0)"
        )
        library.execute(
            "INSERT INTO download VALUES ('freesound', '9', 'rain', '9/rain.wav', 'u', 'p', "
            "'Rain', 'someone', 'cc-by', 'l', 't')"
        )
    hits = {hit.digest: hit for hit in search.search(library, Query(limit=50)).hits}
    assert hits["hat1"].licence == "commercial-pack"
    assert hits["drum_fs"].to_dict()["licence"] == "cc0"
    assert hits["rain"].licence == "cc-by"  # its own, not its store's "varies"
    assert search.search(library, Query(licence="varies")).hits[0].licence == "cc-by"
    for digest, hit in hits.items():
        assert hit.licence == kit._licence(library, digest)  # what a render's report says


def test_limit_and_stable_order(library: sqlite3.Connection) -> None:
    result = search.search(library, Query(limit=2))
    assert result.matched == 7
    assert len(result.hits) == 2
    assert result.hits[0].path < result.hits[1].path  # equal scores fall back to path


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Dm", "D:minor"),
        ("d minor", "D:minor"),
        ("D:minor", "D:minor"),
        ("Ab", "G#:major"),
        ("F#maj", "F#:major"),
        ("A", "A:major"),
        ("Ebm", "D#:minor"),
    ],
)
def test_parse_key_query(text: str, key: str) -> None:
    assert search.parse_key_query(text) == key


def test_bad_key_query() -> None:
    with pytest.raises(search.QueryError):
        search.parse_key_query("purple")


def cfg_for(tmp_path: Path) -> config.Config:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    return config.parse(raw)


def args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["catalogue", "search", *argv])


@pytest.mark.req("REQ-CAT-05")
def test_cli_renders_reasons_and_json(
    library: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_for(tmp_path)
    assert commands.cmd_search(cfg, args("--role", "hat", "--bpm", "128", "--max-lufs", "-16")) == 0
    out = capsys.readouterr().out
    assert "1 matches, best 1:" in out
    assert "why: hat (filename 0.6); 128 BPM (filename)" in out
    assert "| -18.0 LUFS | commercial-pack" in out
    assert "     hat2 | role hat, bpm 128, form loop" in out  # hash, then strongest labels
    assert "every label of one sound: mph catalogue sound HASH" in out
    assert "have no loudness yet" in out
    assert commands.cmd_search(cfg, args("--key", "Dm", "--role", "bass", "--json")) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits[0]["path"] == "Synthpop (Dm, 87bpm)/Bass/Bass1.wav"


def test_cli_errors_and_empty_results(
    library: sqlite3.Connection, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_for(tmp_path)
    assert commands.cmd_search(cfg, args("--key", "purple")) == 2
    assert commands.cmd_search(cfg, args("--role", "kick", "--bpm", "128")) == 1
    assert "widen --bpm-tolerance" in capsys.readouterr().out
    assert commands.cmd_search(cfg_for(tmp_path / "elsewhere"), args("x")) == 1


@pytest.mark.req("REQ-CAT-10")
def test_a_long_recording_labelled_a_track_is_a_track(conn: sqlite3.Connection) -> None:
    add(
        conn,
        "song",
        "full_tracks/soul_clap-love_train.flac",
        [
            ("form", "track", "folder", 0.9),
            ("form", "phrase", "analysis", 0.7),
            ("bpm", "120", "analysis", 0.9),
        ],
        features={"form": "phrase", "bpm": 120.0},
        duration=337.5,
    )
    add(conn, "fresh", "full_tracks/not_analysed.flac", [("form", "track", "folder", 0.9)])
    add(
        conn,
        "drone",
        "Pack/Atmos/Long Drone.wav",
        [("form", "phrase", "analysis", 0.7), ("role", "pad", "folder", 0.5)],
        features={"form": "phrase"},
        duration=90.0,
    )
    tracks = search.search(conn, Query(form="track")).hits
    assert sorted(hit.digest for hit in tracks) == ["fresh", "song"]
    assert {hit.facts.get("form") for hit in tracks} == {"track"}
    assert [hit.digest for hit in search.search(conn, Query(form="phrase")).hits] == ["drone"]
    best = search.search(conn, Query(form="track", bpm=120)).hits[0]
    assert best.digest == "song"
    assert cli.build_parser().parse_args(["catalogue", "search", "--form", "track"]).form == "track"


@pytest.mark.req("REQ-CAT-12")
def test_what_files_say_about_themselves_counts_in_search(conn: sqlite3.Connection) -> None:
    add(  # a loop its name says little about; its ACID and Apple Loops chunks say more
        conn,
        "own",
        "Pack/Loops/Loop 07.wav",
        [
            ("bpm", "126", "embedded", 0.9),
            ("key", "F:minor", "embedded", 0.8),
            ("key", "E:major", "analysis", 0.9),
        ],
    )
    add(  # a loop whose tags disagree with its name: the name wins
        conn,
        "tagged",
        "Pack/Loops/Groove 124bpm Am.wav",
        [
            ("bpm", "124", "filename", 0.6),
            ("bpm", "120", "embedded", 0.35),
            ("key", "A:minor", "filename", 0.6),
            ("key", "F:minor", "embedded", 0.35),
        ],
    )
    add(
        conn,
        "song",
        "full_tracks/01 night-drive.flac",
        [
            ("title", "Night Drive", "embedded", 1.0),
            ("artist", "The Somebodies", "embedded", 1.0),
            ("record_label", "Example Records", "embedded", 1.0),
        ],
        duration=300.0,
    )
    keyed = search.search(conn, Query(key="F:minor"))
    assert keyed.hits[0].reasons == ("key F:minor (embedded)",)  # the file's label wins
    assert "tagged" not in {hit.digest for hit in keyed.hits}  # its name's A minor wins
    tempo = {hit.digest: hit for hit in search.search(conn, Query(bpm=124)).hits}
    assert tempo["tagged"].reasons == ("124 BPM (filename)",)
    assert tempo["tagged"].facts == {"bpm": "124", "key": "A:minor"}
    assert tempo["own"].reasons == ("126 BPM (embedded)",)
    for words in (("somebodies",), ("example records",), ("night", "drive")):
        assert paths(search.search(conn, Query(text=words))) == ["full_tracks/01 night-drive.flac"]
    assert search.search(conn, Query(roles=("artist",))).hits == []  # text gives no role
