"""Ideas Dean adds on the listening page (REQ-FBK-08)."""

from __future__ import annotations

import json
from contextlib import closing
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from mph.bench import feedback, ideas
from mph.catalogue import browse, db
from tests.bench.test_feedback import LOCAL, PORT, POST, fake_render
from tests.fakes import FakeRunner

AT = "2026-11-13T09:00:00Z"


def bm(area: str, number: int) -> str:
    """A benchmark id no spec item has yet, as an idea gets one."""
    return f"BM-{area}-{number:02d}"


TOMS, SUB = bm("TRIBA", 1), bm("BASS", 10)
DRUM_11, DRUM_12, AFRO_05, AFRO_06, FX_05 = (
    bm("DRUM", 11),
    bm("DRUM", 12),
    bm("AFRO", 5),
    bm("AFRO", 6),
    bm("FX", 5),
)


@pytest.fixture
def app(tmp_path: Path) -> browse.App:
    data = tmp_path / "data"
    (data / "renders").mkdir(parents=True)
    return browse.App(data / db.FILENAME, data, FakeRunner(), PORT)


def add(app: browse.App, **payload: object) -> browse.Response:
    return app.handle_post("/api/idea", POST, json.dumps(payload).encode())


def added(app: browse.App, **payload: object) -> dict[str, Any]:
    response = add(app, **payload)
    assert response.status == HTTPStatus.OK, response.body
    found: dict[str, Any] = json.loads(response.body)
    return found


def drop(app: browse.App, idea: int, *, undo: bool = False) -> browse.Response:
    body = json.dumps({"idea": idea, "undo": undo}).encode()
    return app.handle_post("/api/idea-drop", POST, body)


def board(app: browse.App) -> dict[str, Any]:
    found: dict[str, Any] = json.loads(app.handle("/api/ideas", LOCAL).body)
    return found


@pytest.mark.req("REQ-FBK-08")
@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Tribal percussion", "tribal-percussion"),
        ("Hats up 3 dB", "hats-up-3-db"),
        ("  Café -- Ñandú! ", "cafe-nandu"),
        ("x" * 50, "x" * 40),
        ("!!!", ""),
    ],
)
def test_ids_are_lower_case_words_joined_by_hyphens(text: str, slug: str) -> None:
    assert ideas.slug(text) == slug


@pytest.mark.req("REQ-FBK-08")
def test_filled_in_ids_follow_the_spec(app: browse.App) -> None:
    project = added(app, kind="project", title="Tribal percussion", notes="drums from home")
    assert (project["ref"], project["ref_given"], project["status"]) == (
        "tribal-percussion",
        False,
        "open",
    )
    # A benchmark id takes the area its project's benchmarks use most, and the next number
    # in that area, counting the spec too (BM-AFRO-03 and 04 are specced, not scored).
    assert added(app, kind="benchmark", parent="north-star", title="Shekere")["ref"] == AFRO_05
    assert added(app, kind="benchmark", parent="north-star", title="Bell")["ref"] == AFRO_06
    assert added(app, kind="benchmark", parent="drum-patterns", title="Shuffle")["ref"] == DRUM_11
    assert added(app, kind="benchmark", parent="effects", title="Tape stop")["ref"] == FX_05
    # A project with no benchmarks yet: the first word of its id, cut to five letters.
    tribal = added(app, kind="benchmark", parent="tribal-percussion", title="Toms")
    assert tribal["ref"] == TOMS
    # A variant name comes from its title, made unique within the benchmark.
    hats = added(app, kind="variant", parent="BM-DRUM-02", title="Hats up 3 dB")
    assert hats["ref"] == "hats-up-3-db"
    again = added(app, kind="variant", parent="BM-DRUM-02", title="Hats up 3 dB!")
    assert again["ref"] == "hats-up-3-db-2"
    assert added(app, kind="variant", parent="BM-DRUM-02", title="Renoise")["ref"] == "renoise-2"
    assert added(app, kind="variant", parent=TOMS, title="Dry")["ref"] == "dry"


@pytest.mark.req("REQ-FBK-08")
def test_a_dropped_ideas_id_is_never_filled_in_again(app: browse.App) -> None:
    first = added(app, kind="benchmark", parent="drum-patterns", title="Shuffle")
    assert drop(app, first["id"]).status == HTTPStatus.OK
    second = added(app, kind="benchmark", parent="drum-patterns", title="Shuffle")
    assert (first["ref"], second["ref"]) == (DRUM_11, DRUM_12)
    hats = added(app, kind="variant", parent="BM-DRUM-02", title="Hats")
    assert drop(app, hats["id"]).status == HTTPStatus.OK
    assert added(app, kind="variant", parent="BM-DRUM-02", title="Hats")["ref"] == "hats-2"
    typed = added(app, kind="variant", parent="BM-DRUM-02", title="Hats", ref="hats")
    assert typed["ref_given"] is True  # typed, a dropped one's name is free again


@pytest.mark.req("REQ-FBK-08")
@pytest.mark.parametrize(
    ("payload", "says"),
    [
        ({"kind": "song", "title": "x"}, "kind must be project, benchmark or variant"),
        ({"kind": "project", "title": "  "}, "give it a title"),
        ({"kind": "project", "title": "x" * 121}, "give it a title"),
        ({"kind": "project", "title": "x", "notes": "n" * 4001}, "notes are limited"),
        ({"kind": "project", "title": "!!!"}, "give the project a name"),
        ({"kind": "project", "title": "Drum patterns"}, "already a project 'drum-patterns'"),
        ({"kind": "project", "title": "Again", "ref": "effects"}, "already a project"),
        ({"kind": "project", "title": "x", "ref": "Tribal Drums"}, "like tribal-percussion"),
        ({"kind": "benchmark", "title": "x", "parent": "jazz"}, "no project 'jazz'"),
        ({"kind": "benchmark", "title": "x", "parent": "bass", "ref": "BM-DRUM-01"},
         "BM-DRUM-01 is taken"),
        ({"kind": "benchmark", "title": "x", "parent": "bass", "ref": "BM-AFRO-03"},
         "is taken"),  # specced, though not scored yet
        ({"kind": "benchmark", "title": "x", "parent": "bass", "ref": "bm-bass-9"},
         "like BM-DRUM-01"),
        ({"kind": "variant", "title": "x", "parent": "BM-NOPE"}, "no benchmark 'BM-NOPE'"),
        ({"kind": "variant", "title": "x", "parent": "BM-DRUM-02", "ref": "renoise"},
         "already has a take or idea called 'renoise'"),
        ({"kind": "variant", "title": "x", "parent": "BM-SC-01", "ref": "bypass"},
         "already has a take"),
        ({"kind": "variant", "title": "x", "parent": "BM-DRUM-02", "ref": "Loud_Hats"},
         "like hats-up"),
    ],
)  # fmt: skip
def test_bad_ideas_are_refused_with_a_reason(
    app: browse.App, payload: dict[str, object], says: str
) -> None:
    response = add(app, **payload)
    assert response.status == HTTPStatus.BAD_REQUEST
    assert says in response.body.decode()


@pytest.mark.req("REQ-FBK-08")
def test_a_typed_id_must_be_free_of_open_ideas_too(app: browse.App) -> None:
    added(app, kind="benchmark", parent="bass", title="Sub", ref=SUB)
    refused = add(app, kind="benchmark", parent="drum-patterns", title="Sub", ref=SUB)
    assert refused.status == HTTPStatus.BAD_REQUEST and "is taken" in refused.body.decode()
    added(app, kind="project", title="Tribal percussion")
    refused = add(app, kind="project", title="Tribal Percussion")
    assert refused.status == HTTPStatus.BAD_REQUEST and "already a project" in refused.body.decode()


@pytest.mark.req("REQ-FBK-08")
def test_dropping_an_idea_drops_what_is_under_it_and_undo_brings_it_back(
    app: browse.App,
) -> None:
    project = added(app, kind="project", title="Tribal percussion")
    bench = added(app, kind="benchmark", parent="tribal-percussion", title="Toms")
    early = added(app, kind="variant", parent=bench["ref"], title="Dry")
    late = added(app, kind="variant", parent=bench["ref"], title="Wet")
    assert json.loads(drop(app, early["id"]).body) == {
        "undo": False,
        "ideas": [early["id"]],
        "refs": ["dry"],
    }
    dropped = json.loads(drop(app, project["id"]).body)
    assert dropped["refs"] == ["tribal-percussion", TOMS, "wet"]
    status = {i["id"]: i["status"] for i in board(app)["ideas"]}
    assert set(status.values()) == {"dropped"}
    back = json.loads(drop(app, project["id"], undo=True).body)
    assert back["refs"] == ["tribal-percussion", TOMS, "wet"]  # not the one before
    status = {i["id"]: i["status"] for i in board(app)["ideas"]}
    assert (status[late["id"]], status[early["id"]]) == ("open", "dropped")
    again = drop(app, project["id"], undo=True)
    assert again.status == HTTPStatus.BAD_REQUEST and "open already" in again.body.decode()
    assert drop(app, bench["id"]).status == HTTPStatus.OK  # takes "wet" with it
    homeless = drop(app, late["id"], undo=True)
    assert f"{TOMS} is dropped: bring it back first" in homeless.body.decode()
    assert json.loads(drop(app, bench["id"], undo=True).body)["refs"] == [TOMS, "wet"]
    assert "dropped already" in drop(app, early["id"]).body.decode()
    assert "no idea 999" in drop(app, 999).body.decode()
    bad = app.handle_post("/api/idea-drop", POST, json.dumps({"idea": "1"}).encode())
    assert bad.status == HTTPStatus.BAD_REQUEST and "by its number" in bad.body.decode()


@pytest.mark.req("REQ-FBK-08")
def test_undo_is_refused_when_another_open_idea_has_the_id(app: browse.App) -> None:
    first = added(app, kind="benchmark", parent="bass", title="Sub", ref=SUB)
    assert drop(app, first["id"]).status == HTTPStatus.OK
    added(app, kind="benchmark", parent="bass", title="Sub again", ref=SUB)
    refused = drop(app, first["id"], undo=True)
    assert refused.status == HTTPStatus.BAD_REQUEST
    assert f"another open idea is called {SUB}" in refused.body.decode()


@pytest.mark.req("REQ-FBK-08")
def test_an_idea_is_built_once_a_score_or_another_branchs_render_has_it(
    app: browse.App, tmp_path: Path
) -> None:
    variant = added(app, kind="variant", parent="BM-DRUM-02", title="Loud hats")
    bench = added(app, kind="benchmark", parent="bass", title="Branched", ref=SUB)
    data = tmp_path / "data"
    fake_render(data, "BM-DRUM-02", variant="loud-hats", render_hash="5" * 16)
    fake_render(data, SUB, variant="renoise", render_hash="6" * 16)
    status = {i["id"]: i["status"] for i in board(app)["ideas"]}
    assert status == {variant["id"]: "built", bench["id"]: "built"}
    known = ideas.Known({}, frozenset({"tribal-percussion"}), {}, {}, {})
    idea = ideas.Idea(1, "project", "tribal-drums", "", "Tribal percussion", "", False, AT)
    assert idea.status(known) == "built"  # its name became a project with a score


@pytest.mark.req("REQ-FBK-08")
def test_the_board_lists_where_ideas_can_go(app: browse.App) -> None:
    added(app, kind="project", title="Tribal percussion")
    projects = {p["id"]: p for p in board(app)["projects"]}
    assert projects["drum-patterns"]["from"] == "scores"
    assert projects["effects"]["from"] == "spec"  # specced, no scores yet
    assert projects["tribal-percussion"]["from"] == "idea"
    assert projects["north-star"]["next_benchmark"] == AFRO_05
    specced = [b["id"] for b in projects["north-star"]["specced"]]
    assert "BM-AFRO-03" in specced and "BM-AFRO-01" not in specced  # 01 has a score
    assert [p["name"] for p in board(app)["projects"]] == sorted(
        (p["name"] for p in board(app)["projects"]), key=str.lower
    )
    assert feedback.ELSEWHERE not in {p["name"] for p in board(app)["projects"]}


@pytest.mark.req("REQ-FBK-08")
def test_ideas_live_in_the_catalogue(app: browse.App, tmp_path: Path) -> None:
    added(app, kind="project", title="Tribal percussion", notes="  from the jembe takes  ")
    with closing(db.connect(tmp_path / "data" / db.FILENAME)) as conn:
        (idea,) = ideas.every(conn)
    assert (idea.kind, idea.ref, idea.notes, idea.dropped_at) == (
        "project",
        "tribal-percussion",
        "from the jembe takes",
        None,
    )
    assert idea.created_at.startswith("20")
