"""Ratings, their history, and verdicts carried over (REQ-FBK-02, REQ-FBK-06, REQ-FBK-07)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mph.bench import ratings
from mph.bench.ratings import Rating
from mph.catalogue import db

AT = "2026-11-13T09:00:00Z"
VERDICTS = [  # benchmark, render, verdict, notes, when, scope
    ("BM-DRUM-02", "1" * 16, "pass", "", "2026-11-09T10:00:00Z", "take"),
    ("BM-DRUM-02", "2" * 16, "pass", "0:42 hats a touch loud", "2026-11-09T11:00:00Z", "take"),
    ("BM-DRUM-02", "3" * 16, "close", "nearly", "2026-11-10T09:00:00Z", "all"),
    ("BM-SC-01", "4" * 16, "fail", "can't hear the duck", "2026-11-11T09:00:00Z", "take"),
]


def catalogue_before_ratings(path: Path) -> None:
    """A catalogue as mph left it before ratings (schema 8), with verdicts in it."""
    with closing(sqlite3.connect(path)) as conn:
        for version, script in enumerate(db.MIGRATIONS[:8]):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version + 1}")
        conn.executemany(
            "INSERT INTO verdict (benchmark, score_hash, verdict, notes, created_at, scope) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            VERDICTS,
        )
        conn.commit()


@pytest.mark.req("REQ-FBK-07")
def test_verdicts_are_carried_over_once_when_the_catalogue_is_upgraded(tmp_path: Path) -> None:
    path = tmp_path / db.FILENAME
    catalogue_before_ratings(path)
    with closing(db.connect(path)) as conn:
        assert db.schema_version(conn) == len(db.MIGRATIONS)
        carried = list(reversed(ratings.history(conn, "BM-DRUM-02")))  # oldest first
        assert [(r.stars, r.status, r.state) for r in carried] == [
            (3, None, "resolved"),  # a pass: the lowest pass, resolved by its stars
            (3, "unresolved", "unresolved"),  # a pass with notes was still to act on
            (2, None, "unresolved"),  # close
        ]
        assert [(r.via, r.verdict, r.scope) for r in carried] == [
            ("verdict", "pass", "take"),
            ("verdict", "pass", "take"),
            ("verdict", "close", "all"),
        ]
        assert carried[1].notes == "0:42 hats a touch loud"
        assert carried[1].created_at == "2026-11-09T11:00:00Z"
        assert (carried[1].score_hash, carried[1].take, carried[1].inputs) == ("2" * 16, "", "")
        failed = ratings.history(conn, "BM-SC-01")[0]
        assert (failed.stars, failed.state, failed.verdict) == (1, "unresolved", "fail")
        assert conn.execute("SELECT count(*) FROM verdict").fetchone()[0] == 4  # left as it was
    with closing(db.connect(path)) as conn:  # opening it again carries nothing twice
        assert conn.execute("SELECT count(*) FROM rating").fetchone()[0] == 4


@pytest.mark.req("REQ-FBK-06")
def test_a_rating_says_who_set_its_state() -> None:
    clicked = Rating(1, "BM-DRUM-02", "1" * 16, "take", 5, "unresolved", "more swing", AT)
    found = clicked.to_dict()
    assert (found["state"], found["status_from"], found["passed"]) == ("unresolved", "you", True)
    by_stars = Rating(2, "BM-DRUM-02", "1" * 16, "take", 2, None, "", AT)
    assert (by_stars.state, by_stars.status_from, by_stars.passed) == ("unresolved", "stars", False)
    cleared = Rating(3, "BM-DRUM-02", "1" * 16, "take", 0, None, "", AT)
    assert (cleared.cleared, cleared.state, cleared.status_from) == (True, "unrated", "")
    assert ratings.state_of(None) == "unrated"


@pytest.mark.req("REQ-FBK-06")
def test_rendered_again_compares_what_shaped_the_render() -> None:
    given = Rating(1, "BM-DRUM-02", "1" * 16, "take", 2, None, "", AT, "renoise", "a" * 16)
    later = "2026-11-14T09:00:00Z"
    assert not ratings.rendered_again(given, {"inputs": "a" * 16, "rendered_at": later})
    assert ratings.rendered_again(given, {"inputs": "b" * 16, "rendered_at": AT})
    assert ratings.rendered_again(given, {"rendered_at": later})  # no inputs: by the time
    assert not ratings.rendered_again(given, {"rendered_at": "2026-11-12T09:00:00Z"})


@pytest.mark.req("REQ-FBK-02")
def test_a_whole_benchmark_rating_is_stored_once_per_take(tmp_path: Path) -> None:
    with closing(db.connect(tmp_path / db.FILENAME)) as conn:
        asked = ratings.asked({"id": "BM-DRUM-02", "scope": "all", "stars": 4, "notes": " ok "})
        rated = [
            {"score_hash": "1" * 16, "inputs": "a" * 16},
            {"score_hash": "2" * 16},  # rendered before inputs were recorded
        ]
        take_of = {"1" * 16: "renoise", "2" * 16: "synth-kit"}
        saved = ratings.save(conn, asked, rated, take_of=take_of, when=AT)
        assert saved == {
            "score_hashes": ["1" * 16, "2" * 16],
            "stars": 4,
            "status": None,
            "state": "resolved",
            "notes": "ok",
            "created_at": AT,
            "scope": "all",
        }
        stored = ratings.history(conn, "BM-DRUM-02")
        assert sorted((r.take, r.inputs, r.scope) for r in stored) == [
            ("renoise", "a" * 16, "all"),
            ("synth-kit", "", "all"),
        ]
        cleared = ratings.asked({"id": "BM-DRUM-02", "score_hash": "1" * 16, "stars": 0})
        with pytest.raises(ratings.RatingError, match="is no rating"):
            ratings.save(conn, cleared, rated[:1], take_of=take_of, when=AT)
