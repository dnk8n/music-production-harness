"""Projects: folders of scores, Dean's examples and the user's own
(spec/components/projects.md)."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest

from mph import cli, config, doctor
from mph.bench import feedback, ideas, project_commands, projects, score
from tests.fakes import FakeRunner

SCORE = """id = "{id}"
bpm = 120
bars = 1
{project}
[voices.kick]
grid = "x . . . x . . . x . . . x . . ."
sound = {{ role = "kick" }}
"""


def make_project(root: Path, folder: str, name: str, *ids: str, line: str = "") -> Path:
    path = root / folder
    path.mkdir(parents=True)
    (path / projects.PROJECT_FILE).write_text(f'name = "{name}"\nabout = "about {name}"\n')
    for benchmark_id in ids:
        (path / f"{benchmark_id}.toml").write_text(SCORE.format(id=benchmark_id, project=line))
    return path


def cfg_with(tmp_path: Path, extra: Mapping[str, object]) -> config.Config:
    raw = config.deep_merge(
        config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}, **extra}
    )
    return config.parse(raw)


def args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["projects", *argv])


def run(cfg: config.Config, *argv: str) -> int:
    return project_commands.dispatch(args(*argv), cfg, FakeRunner(), None)


# --- a project is a folder (REQ-PRJ-01) --------------------------------------------------


@pytest.mark.req("REQ-PRJ-01")
def test_a_project_is_a_folder_of_scores(tmp_path: Path) -> None:
    root = tmp_path / "examples"
    make_project(root, "grooves", "My grooves", "BM-GRVB-00", "BM-GRV-00")
    chosen = projects.discover(projects.Settings(root=root))
    (found,) = chosen.projects
    assert (found.id, found.name, found.about) == ("grooves", "My grooves", "about My grooves")
    assert found.scores == ("BM-GRV-00", "BM-GRVB-00")
    assert chosen.available() == ["BM-GRV-00", "BM-GRVB-00"]
    projects.use(projects.Settings(root=root))
    assert score.available() == ["BM-GRV-00", "BM-GRVB-00"]
    assert score.load("BM-GRV-00").project == "My grooves"  # the score leaves it out
    assert score.source("BM-GRVB-00").startswith('id = "BM-GRVB-00"')


@pytest.mark.req("REQ-PRJ-01")
def test_a_score_naming_another_project_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "examples"
    make_project(root, "grooves", "My grooves", "BM-GRV-00", line='project = "Other"')
    make_project(root, "same", "Same", "BM-SAM-00", line='project = "Same"')
    projects.use(projects.Settings(root=root))
    with pytest.raises(score.ScoreError, match="says it is in project 'Other'"):
        score.load("BM-GRV-00")
    assert score.load("BM-SAM-00").project == "Same"
    with pytest.raises(score.ScoreError, match="no score for BM-NONE-00"):
        score.load("BM-NONE-00")


@pytest.mark.req("REQ-PRJ-01")
def test_broken_projects_are_reported_and_the_rest_still_work(tmp_path: Path) -> None:
    root = tmp_path / "examples"
    make_project(root, "ok", "OK", "BM-OK-00")
    bad = make_project(root, "bad", "Bad", "BM-BAD-00")
    (bad / projects.PROJECT_FILE).write_text("name = 3\n")
    odd = make_project(root, "odd", "Odd")
    (odd / projects.PROJECT_FILE).write_text('name = "Odd"\ncolour = "red"\n')
    broken = make_project(root, "broken", "Broken")
    (broken / projects.PROJECT_FILE).write_text("name = \n")
    loose = root / "loose"
    loose.mkdir()
    (loose / "BM-LOO-00.toml").write_text(SCORE.format(id="BM-LOO-00", project=""))
    (root / ".hidden").mkdir()
    (root / "notes").mkdir()
    chosen = projects.discover(projects.Settings(root=root))
    assert chosen.available() == ["BM-OK-00"]
    problems = "\n".join(chosen.problems)
    assert "name must be text" in problems
    assert "unknown keys ['colour']" in problems
    assert "broken" in problems
    assert f"{loose} has scores but no project.toml" in problems
    assert "notes" not in problems
    with pytest.raises(projects.ProjectError, match=r"project bad has a project\.toml"):
        chosen.owner("BM-BAD-00")


@pytest.mark.req("REQ-PRJ-01")
def test_one_benchmark_in_two_projects_of_a_kind_is_a_problem(tmp_path: Path) -> None:
    root, mine, more = tmp_path / "examples", tmp_path / "mine", tmp_path / "more"
    make_project(root, "a", "A", "BM-DUP-00")
    make_project(root, "b", "B", "BM-DUP-00")
    make_project(mine, "c", "C", "BM-OWN-00")
    make_project(more, "d", "D", "BM-OWN-00")
    single = make_project(tmp_path, "single", "Single", "BM-ONE-00")  # a folder that is one
    settings = projects.Settings(folders=(mine, more, single), root=root)
    chosen = projects.discover(settings)
    duplicated = [p for p in chosen.problems if "is in both" in p]
    assert len(duplicated) == 2
    assert chosen.owner("BM-DUP-00").id == "a"  # the first is used
    assert chosen.owner("BM-ONE-00").folder == single
    assert chosen.find("Single").id == "single"
    with pytest.raises(projects.ProjectError, match="no project 'zz'"):
        chosen.find("zz")


# --- Dean's projects are examples (REQ-PRJ-02) -------------------------------------------


@pytest.mark.req("REQ-PRJ-02")
def test_deans_projects_are_examples_named_by_their_ids() -> None:
    chosen = projects.discover()
    assert chosen.problems == ()
    assert chosen.projects and all(p.example and p.on for p in chosen.projects)
    for project in chosen.projects:
        assert project.id == ideas.slug(project.name)
        assert project.about and project.spec and project.needs
        for benchmark_id in project.scores:
            raw = tomllib.loads(project.path(benchmark_id).read_text("utf-8"))
            assert raw["id"] == benchmark_id
            assert raw.get("project", project.name) == project.name
    assert len(chosen.available()) == sum(len(p.scores) for p in chosen.projects)


@pytest.mark.req("REQ-PRJ-02")
def test_every_spec_section_with_benchmarks_has_an_example_project() -> None:
    names = {p.name for p in projects.discover().projects}
    sections = {section for _, _, section in ideas.specced()}
    assert sections and sections <= names


@pytest.mark.req("REQ-PRJ-02")
def test_the_examples_hold_only_text() -> None:
    examples = projects.EXAMPLES.parent
    found = [p for p in examples.rglob("*") if p.is_file()]
    assert found
    for path in found:
        assert path.suffix in {".toml", ".md"}, path
        path.read_text("utf-8")  # text, not a binary that happens to have that name


# --- turning them on and off (REQ-PRJ-03) ---------------------------------------------------


@pytest.mark.req("REQ-PRJ-03")
def test_examples_can_be_turned_off_or_chosen() -> None:
    none = projects.discover(projects.Settings(examples=False))
    assert none.available() == [] and none.projects
    assert not any(p.on for p in none.projects)
    assert none.specced() == []
    assert none.hidden("BM-DRUM-01") and not none.hidden("BM-NONE-00")
    with pytest.raises(projects.ProjectError, match="turned off"):
        none.owner("BM-DRUM-01")
    some = projects.discover(projects.Settings(examples=("sidechain", "nope")))
    assert some.available() == ["BM-SC-01"]
    assert any("'nope', which isn't an example project" in p for p in some.problems)
    assert {b for b, _, _ in some.specced()} == {"BM-SC-01"}
    every = projects.discover()
    assert {b for b, _, _ in every.specced()} == {b for b, _, _ in ideas.specced()}


@pytest.mark.req("REQ-PRJ-03")
def test_your_own_project_is_used_instead_of_an_example(tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    make_project(mine, "my-drums", "My drums", "BM-DRUM-01", "BM-MINE-00")
    chosen = projects.discover(projects.Settings(folders=(mine,)))
    assert chosen.owner("BM-DRUM-01").name == "My drums"
    assert chosen.owner("BM-DRUM-02").name == "Drum patterns"
    assert "BM-MINE-00" in chosen.available()
    assert chosen.find("drum-patterns").example
    nowhere = projects.discover(projects.Settings(folders=(tmp_path / "nowhere",)))
    assert "isn't a folder" in nowhere.problems[0]
    gone = projects.discover(projects.Settings(root=tmp_path / "no-examples"))
    assert "aren't here" in gone.problems[0] and gone.available() == []
    quiet = projects.discover(projects.Settings(examples=False, root=tmp_path / "no-examples"))
    assert quiet.problems == ()


@pytest.mark.req("REQ-PRJ-03")
def test_only_projects_that_are_on_are_listed_rendered_and_served(tmp_path: Path) -> None:
    projects.use(projects.Settings(examples=("sidechain",)))
    assert score.available() == ["BM-SC-01"]
    with pytest.raises(score.ScoreError, match="turned off"):
        score.load("BM-DRUM-01")
    renders = tmp_path / "renders"
    old = renders / "BM-DRUM-01" / "0123456789abcdef"
    old.mkdir(parents=True)
    made = {"benchmark": "BM-DRUM-01", "variant": "main", "files": {"delivery": "x.aiff"}}
    (old / "report.json").write_text(json.dumps(made))
    assert feedback.rendered(renders) == ["BM-DRUM-01"]
    assert [b["id"] for b in feedback.listing(renders, None)] == ["BM-SC-01"]
    assert [b["id"] for b in feedback.listing(renders, None, elsewhere=True)] == ["BM-SC-01"]


@pytest.mark.req("REQ-PRJ-03")
def test_main_turns_on_the_projects_the_config_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    user = tmp_path / "c.toml"
    user.write_text(f'[paths]\ndata_dir = "{tmp_path / "data"}"\n[projects]\nexamples = ["bass"]\n')
    assert cli.main(["--config", str(user), "bench", "list"]) == 0
    out = capsys.readouterr().out
    assert "BM-BASS-01" in out and "BM-DRUM-01" not in out


@pytest.mark.req("REQ-PRJ-03")
def test_list_shows_every_project_on_or_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_with(tmp_path, {"projects": {"examples": ["bass", "gone"]}})
    assert run(cfg, "list") == 1  # "gone" isn't an example
    out = capsys.readouterr().out
    assert re.search(r"on\s+bass\s+1 benchmark\s+Bass\s+\(example\)", out)
    assert re.search(r"off\s+drum-patterns\s+10 benchmarks", out)
    assert "! [projects].examples names 'gone'" in out
    assert "1 benchmark on." in out
    assert run(cfg_with(tmp_path, {}), "list", "--json") == 0
    listed = json.loads(capsys.readouterr().out)
    assert "BM-SC-01" in listed["benchmarks_on"] and listed["problems"] == []
    sidechain = next(p for p in listed["projects"] if p["id"] == "sidechain")
    assert sidechain["benchmarks"] == ["BM-SC-01"] and sidechain["example"] is True


@pytest.mark.req("REQ-PRJ-03")
def test_show_says_what_a_project_is_and_needs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_with(tmp_path, {})
    assert run(cfg, "show", "Sidechain") == 0
    out = capsys.readouterr().out
    assert "Sidechain  (sidechain: Dean's example, on)" in out
    assert "BM-SC-01  Kick-triggered ducking" in out and "Needs: " in out
    assert run(cfg, "show", "effects") == 0
    out = capsys.readouterr().out
    assert "No scores yet." in out and "BM-FX-01  Delay throw" in out
    assert run(cfg, "show", "nothing-like-it") == 2
    assert "no project 'nothing-like-it'" in capsys.readouterr().err
    mine = tmp_path / "mine"
    own = make_project(mine, "own", "Own", "BM-OWN-00")
    (own / "BM-OWNB-00.toml").write_text("not = [toml")
    (own / projects.PROJECT_FILE).write_text('name = "Own"\nspec = "nowhere"\n')
    assert run(cfg_with(tmp_path, {"projects": {"folders": [str(mine)]}}), "show", "own") == 0
    out = capsys.readouterr().out
    assert "your own" in out and "BM-OWNB-00  (can't be read)" in out


# --- doctor and status (REQ-PRJ-06) --------------------------------------------------------


@pytest.mark.req("REQ-PRJ-06")
def test_doctor_counts_projects_and_reports_their_problems(tmp_path: Path) -> None:
    root = tmp_path / "examples"
    make_project(root, "ok", "OK", "BM-OK-00")
    make_project(root, "named", "Named", "BM-NAM-00", line='project = "Else"')
    broken = make_project(root, "unreadable", "Unreadable", "BM-UNR-00")
    (broken / "BM-UNR-00.toml").write_text("not = [toml")
    settings = projects.Settings(folders=(tmp_path / "gone",), root=root)
    found = list(doctor.check_projects(projects.discover(settings)))
    assert found[0].status is doctor.Status.OK
    assert found[0].detail == "3 of 3 examples on, 0 of your own: 3 benchmarks"
    warned = [f.detail for f in found if f.status is doctor.Status.WARN]
    assert any("isn't a folder" in d for d in warned)
    assert "BM-NAM-00 says it is in another project than 'Named'" in warned
    assert any(d.startswith("BM-UNR-00 can't be read") for d in warned)
    assert all(f.fix for f in found if f.status is doctor.Status.WARN)


@pytest.mark.req("REQ-PRJ-06")
def test_doctor_and_status_see_the_repos_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    examples = len(projects.discover().projects)  # one more with each new project
    line = doctor.projects_line(projects.discover())
    assert re.fullmatch(
        rf"{examples} of {examples} examples on, 0 of your own: \d+ benchmarks", line
    )
    off = doctor.projects_line(projects.discover(projects.Settings(examples=("bass",))))
    assert off == f"1 of {examples} examples on, 0 of your own: 1 benchmark"
    cfg = cfg_with(tmp_path, {})
    from mph.components import Context  # noqa: PLC0415
    from mph.platform_info import Host  # noqa: PLC0415

    host = Host("Darwin", "arm64", tmp_path, (tmp_path,))
    report = doctor.run(cfg, Context(FakeRunner(), host), None)
    assert any(f.check == "projects" and f.status is doctor.Status.OK for f in report.findings)
    cli.cmd_status(cfg, Context(FakeRunner(), host), None)
    shown = capsys.readouterr().out
    assert re.search(rf"^projects\s+{examples} of {examples} examples on", shown, re.MULTILINE)
