from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mph.catalogue import db, embedded
from mph.catalogue.chunks import Acid, AppleLoop, Chunks, Instrument, Loop, Sampler
from mph.catalogue.embedded import Found, Label
from mph.catalogue.probe import Picture, Probe
from tests.catalogue import audiofiles as af

# Love Train's tags, as ffprobe names them (the FLAC's Vorbis comments).
LOVE_TRAIN = {
    "title": "Love Train",
    "artist": "PillowTalk x Soul Clap ft Greg Paulus & Crew Love",
    "album": "Love Train Remixes",
    "track": "7/7",
    "date": "2026",
    "LABEL": "Soul Clap Records",
    "CATALOGNUMBER": "SCR100",
    "genre": "House",
    "SOURCE": "WEB",
}


def labelled(found: Found) -> dict[str, tuple[str, float]]:
    """name -> (value, confidence), for reads that give one value per name."""
    made = embedded.labels(found)
    assert len({label.name for label in made}) == len(made), made
    return {label.name: (label.value, label.confidence) for label in made}


def loop(acid: Acid | None = None, *, seconds: float = 4.0, **tags: str) -> Found:
    return Found(tags=tags, chunks=Chunks(acid=acid), duration_s=seconds)


def an_acid(**kwargs: object) -> Acid:
    fields: dict[str, object] = {
        "one_shot": False,
        "root_note": None,
        "beats": 8,
        "meter": (4, 4),
        "tempo": 120.0,
    }
    return Acid(**(fields | kwargs))  # type: ignore[arg-type]


@pytest.mark.req("REQ-CAT-12")
def test_an_acid_tempo_whose_beats_fill_the_file_is_sure() -> None:
    found = loop(an_acid(tempo=128.0, beats=8, root_note=57), seconds=3.75)  # 8 beats at 128
    assert labelled(found) == {
        "bpm": ("128", 0.9),
        "form": ("loop", 0.8),
        "root": ("A", 0.8),
    }


@pytest.mark.req("REQ-CAT-12")
def test_acids_default_120_on_a_loop_it_doesnt_fit_ranks_below_names() -> None:
    found = loop(an_acid(tempo=120.0, beats=8), seconds=3.75)  # really 128 BPM
    assert labelled(found) == {"bpm": ("120", 0.5)}  # no form: the beats don't fill it


@pytest.mark.req("REQ-CAT-12")
def test_a_one_shot_has_no_tempo_even_when_tagged() -> None:
    found = loop(an_acid(one_shot=True, beats=1, tempo=120.0), seconds=0.5, TBPM="128")
    assert labelled(found) == {"form": ("oneshot", 0.8)}


def test_an_acid_chunk_with_no_beats_or_an_odd_tempo_gives_no_tempo() -> None:
    assert embedded.labels(loop(an_acid(beats=0))) == []
    assert embedded.labels(loop(an_acid(tempo=0.0))) == []
    # 32 beats at 480 fill 4 seconds: a loop, but at no tempo a track would use
    assert labelled(loop(an_acid(tempo=480.0, beats=32), seconds=4.0)) == {"form": ("loop", 0.8)}


@pytest.mark.req("REQ-CAT-12")
@pytest.mark.parametrize(
    ("written", "bpm"),
    [("120", "120"), ("120.00", "120"), ("122,5", "122.5"), ("128 BPM", "128"), ("174", "174")],
)
def test_bpm_tags(written: str, bpm: str) -> None:
    assert labelled(Found(tags={"TBPM": written}))["bpm"] == (bpm, 0.7)


@pytest.mark.parametrize("written", ["0", "", "fast", "999", "45"])
def test_bpm_tags_that_give_none(written: str) -> None:
    assert "bpm" not in labelled(Found(tags={"BPM": written}))


@pytest.mark.req("REQ-CAT-12")
@pytest.mark.parametrize(
    ("written", "key"),
    [
        ("Am", "A:minor"),
        ("A minor", "A:minor"),
        ("A min", "A:minor"),
        ("am", "A:minor"),
        ("F#m", "F#:minor"),
        ("Ebm", "D#:minor"),
        ("Bb maj", "A#:major"),
        ("C", "C:major"),  # ID3 writes major keys as a lone letter
        ("8A", "A:minor"),  # Camelot
        ("8B", "C:major"),
        ("1A", "G#:minor"),
        ("12B", "E:major"),
        ("1m", "A:minor"),  # Open Key
        ("1d", "C:major"),
        ("2m", "E:minor"),
        ("8A - Am", "A:minor"),
        ("Am/8A", "A:minor"),
    ],
)
def test_key_tags_in_every_common_notation(written: str, key: str) -> None:
    assert embedded.parse_key(written) == key
    assert labelled(Found(tags={"INITIALKEY": written}))["key"] == (key, 0.7)


@pytest.mark.parametrize("written", ["o", "", "none", "13A", "H"])
def test_key_tags_that_give_none(written: str) -> None:
    assert embedded.parse_key(written) is None


@pytest.mark.req("REQ-CAT-12")
@pytest.mark.parametrize(
    ("written", "key"),
    [
        ("Am", "A:minor"),
        ("Ab", "G#:major"),
        ("8A", "A:minor"),
        ("A", None),
        ("Bass", None),
        ("am", None),
    ],
)
def test_ikey_is_keywords_so_only_a_clear_key_counts(written: str, key: str | None) -> None:
    found = labelled(Found(tags={"IKEY": written}))
    assert found.get("key") == ((key, 0.7) if key else None)


@pytest.mark.req("REQ-CAT-12")
def test_an_apple_loop_gives_tempo_key_root_and_form() -> None:
    found = Found(chunks=Chunks(basc=AppleLoop(False, 8, 48, "minor", (4, 4))), duration_s=3.75)
    assert labelled(found) == {
        "bpm": ("128", 0.9),
        "form": ("loop", 0.8),
        "key": ("C:minor", 0.8),
        "root": ("C", 0.8),
    }


def test_apple_loops_without_a_scale_or_as_one_shots() -> None:
    drums = Found(chunks=Chunks(basc=AppleLoop(False, 4, 60, "neither", (4, 4))), duration_s=2.0)
    assert labelled(drums) == {"bpm": ("120", 0.9), "form": ("loop", 0.8)}
    both = Found(chunks=Chunks(basc=AppleLoop(False, 4, 62, "both", (0, 0))), duration_s=2.0)
    assert labelled(both)["root"] == ("D", 0.8)
    assert "key" not in labelled(both)
    hit = Found(chunks=Chunks(basc=AppleLoop(True, 1, 60, "neither", (4, 4))), duration_s=0.4)
    assert labelled(hit) == {"form": ("oneshot", 0.8)}


@pytest.mark.req("REQ-CAT-12")
def test_sampler_notes_count_unless_they_look_untouched() -> None:
    tuned = Found(chunks=Chunks(smpl=Sampler(45, 0.0, ())))
    assert labelled(tuned) == {"root": ("A", 0.6)}
    default = Found(chunks=Chunks(smpl=Sampler(60, 0.0, ())))
    assert labelled(default) == {}  # middle C, no tuning, no loops: nobody set it
    looped = Found(chunks=Chunks(smpl=Sampler(60, 0.0, (Loop(0, 100, "forward"),))))
    assert labelled(looped) == {"root": ("C", 0.6)}
    detuned = Found(chunks=Chunks(inst=Instrument(60, -12, (0, 127), (1, 127), 0)))
    assert labelled(detuned) == {"root": ("C", 0.6)}
    untouched = Found(chunks=Chunks(inst=Instrument(60, 0, (0, 127), (1, 127), 0)))
    assert labelled(untouched) == {}


def test_a_root_found_twice_counts_once_at_its_surest() -> None:
    found = loop(an_acid(root_note=57, beats=0))
    found = Found(chunks=Chunks(acid=found.chunks.acid, smpl=Sampler(45, 0.0, ())))
    assert embedded.labels(found) == [Label("root", "A", 0.8)]


@pytest.mark.req("REQ-CAT-12")
def test_love_trains_tags_become_labels_as_written() -> None:
    assert labelled(Found(tags=LOVE_TRAIN)) == {
        "album": ("Love Train Remixes", 1.0),
        "artist": ("PillowTalk x Soul Clap ft Greg Paulus & Crew Love", 1.0),
        "catalogue_number": ("SCR100", 1.0),
        "genre": ("House", 1.0),
        "record_label": ("Soul Clap Records", 1.0),
        "title": ("Love Train", 1.0),
        "track": ("7/7", 1.0),
        "year": ("2026", 1.0),
    }


@pytest.mark.parametrize(
    ("tags", "name", "value"),
    [
        ({"TRACKNUMBER": "3", "TRACKTOTAL": "12"}, "track", "3/12"),
        ({"track": "03"}, "track", "03"),
        ({"date": "2016-04-08"}, "year", "2016"),
        ({"ICRD": "x", "originaldate": "1998"}, "year", "1998"),
        ({"publisher": "Loopmasters"}, "record_label", "Loopmasters"),
        ({"ORGANIZATION": "Crew Love"}, "record_label", "Crew Love"),
        ({"Catalog #": "CL001"}, "catalogue_number", "CL001"),
        ({"author": "Someone"}, "artist", "Someone"),  # AIFF's AUTH chunk
        ({"title": "  Two   spaces\n"}, "title", "Two spaces"),
    ],
)
def test_tag_names_and_spellings(tags: dict[str, str], name: str, value: str) -> None:
    assert labelled(Found(tags=tags))[name] == (value, 1.0)


@pytest.mark.parametrize("tags", [{"track": "0"}, {"track": "0/12"}, {"date": "sometime"}])
def test_tags_that_say_nothing(tags: dict[str, str]) -> None:
    assert embedded.labels(Found(tags=tags)) == []


@pytest.mark.req("REQ-CAT-12")
@pytest.mark.req("REQ-CAT-03")
def test_text_inside_a_file_never_gives_a_role_or_form() -> None:
    found = Found(
        tags={
            "title": "Kick Loop 128",
            "genre": "Drum & Bass",
            "artist": "Soul Clap",
            "comment": "perc",
        }
    )
    names = {label.name for label in embedded.labels(found)}
    assert names == {"title", "genre", "artist"}


@pytest.mark.req("REQ-CAT-12")
def test_lyrics_are_left_out_and_long_values_kept_short() -> None:
    kept = embedded.kept_tags(
        {"LYRICS": "words", "lyrics-eng": "words", "USLT": "words", "comment": "x" * 5000, "a": " "}
    )
    assert kept == {"comment": "x" * embedded.MAX_VALUE}


@pytest.mark.req("REQ-CAT-12")
def test_read_combines_the_probe_and_the_chunks(tmp_path: Path) -> None:
    path = tmp_path / "loop.wav"
    path.write_bytes(af.wav(2.0, af.riff_chunk(b"acid", af.acid(beats=4, tempo=120.0))))
    facts = Probe(duration_s=2.0, tags={"title": "Loop"}, picture=Picture("mjpeg", 600, 600))
    found = embedded.read(path, facts)
    assert found.error is None
    assert found.to_json() == {
        "tags": {"title": "Loop"},
        "acid": {"one_shot": False, "root_note": None, "beats": 4, "meter": (4, 4), "tempo": 120.0},
        "picture": {"codec": "mjpeg", "width": 600, "height": 600},
    }
    assert labelled(found)["bpm"] == ("120", 0.9)


def test_read_reports_what_kept_the_file_from_being_read(tmp_path: Path) -> None:
    assert embedded.read(tmp_path / "x.wav", Probe(error="Invalid data")).error == "Invalid data"
    missing = embedded.read(tmp_path / "gone.wav", Probe(duration_s=1.0))
    assert (missing.error or "").startswith("cannot read gone.wav")
    assert embedded.labels(missing) == []


def test_skipped_chunks_are_kept_in_the_record() -> None:
    found = Found(chunks=Chunks(skipped=("acid: 12 bytes, wanted at least 24",)))
    assert found.to_json() == {"tags": {}, "skipped": ["acid: 12 bytes, wanted at least 24"]}


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "c.sqlite")
    connection.execute("INSERT INTO asset (hash, bytes, origin) VALUES ('a', 1, 'source')")
    connection.execute("INSERT INTO tag VALUES ('a', 'bpm', '128', 'filename', 0.6)")
    connection.commit()
    yield connection
    connection.close()


def tags_of(conn: sqlite3.Connection) -> set[tuple[str, str, str, float]]:
    return {tuple(r) for r in conn.execute("SELECT name, value, origin, confidence FROM tag")}


@pytest.mark.req("REQ-CAT-12")
def test_store_keeps_the_read_and_replaces_only_embedded_labels(conn: sqlite3.Connection) -> None:
    with conn:
        embedded.store(conn, "a", loop(an_acid(), seconds=4.0, title="Loop"), "t1")
    assert tags_of(conn) == {
        ("bpm", "128", "filename", 0.6),
        ("bpm", "120", "embedded", 0.9),
        ("form", "loop", "embedded", 0.8),
        ("title", "Loop", "embedded", 1.0),
    }
    with conn:
        made = embedded.store(conn, "a", Found(tags={"genre": "Techno"}), "t2")
    assert made == [Label("genre", "Techno", 1.0)]
    assert tags_of(conn) == {("bpm", "128", "filename", 0.6), ("genre", "Techno", "embedded", 1.0)}
    row = conn.execute("SELECT version, read_at, found_json, error FROM embedded").fetchone()
    assert tuple(row) == (embedded.VERSION, "t2", json.dumps({"tags": {"genre": "Techno"}}), None)


def test_store_records_an_unreadable_file_without_labels(conn: sqlite3.Connection) -> None:
    with conn:
        assert embedded.store(conn, "a", Found(error="Invalid data"), "t") == []
    assert conn.execute("SELECT error FROM embedded").fetchone()[0] == "Invalid data"
    assert tags_of(conn) == {("bpm", "128", "filename", 0.6)}
