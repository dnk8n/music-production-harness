"""Projects: folders of scores judged together (spec/components/projects.md).

A project is a folder that holds a ``project.toml`` and one
``<benchmark id>.toml`` score per benchmark (REQ-PRJ-01). Dean's projects ship
with the repo in ``examples/projects/`` (REQ-PRJ-02). The config says which
of them are on and where the user's own projects are (REQ-PRJ-03,
REQ-CFG-08), and a project of the user's own wins over an example that has
the same benchmark id.

Whatever lists, loads or renders scores asks ``current()``: what is on now,
found afresh each time, so a score added while the listening page runs
shows up. ``use()`` sets the config's choice once when mph starts; until
then (in tests and scripts) every example is on and there are no folders of
the user's own.

Problems are collected rather than raised, so one broken project doesn't
hide the rest. Doctor reports them (REQ-PRJ-06), and loading a score from a
project with a problem raises ``ProjectError``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mph.bench import ideas
from mph.config import Config

CHECKOUT = Path(__file__).resolve().parents[3]  # src/mph/bench/projects.py -> the repo
EXAMPLES = CHECKOUT / "examples" / "projects"
PROJECT_FILE = "project.toml"
TEXT_KEYS = ("name", "about", "spec", "needs")
SUFFIX = ".toml"


class ProjectError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Project:
    id: str  # the folder's name
    name: str  # what scores and the listening page call it (REQ-BEN-11)
    folder: Path
    example: bool  # one of Dean's, from examples/projects/
    on: bool
    about: str = ""
    spec: str = ""  # where its benchmarks are described
    needs: str = ""  # what it needs that the repo can't hold
    scores: tuple[str, ...] = ()  # benchmark ids, from the file names
    problem: str = ""  # why it can't be used, or ""

    def path(self, benchmark_id: str) -> Path:
        return self.folder / f"{benchmark_id}{SUFFIX}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "folder": str(self.folder),
            "example": self.example,
            "on": self.on,
            "about": self.about,
            "spec": self.spec,
            "needs": self.needs,
            "benchmarks": list(self.scores),
            "problem": self.problem,
        }


@dataclass(frozen=True, slots=True)
class Settings:
    """The config's choice (REQ-CFG-08), and where the examples are."""

    examples: bool | tuple[str, ...] = True
    folders: tuple[Path, ...] = ()
    root: Path = EXAMPLES


@dataclass(frozen=True, slots=True)
class Found:
    """A score's text and the project it belongs to."""

    text: str
    project: Project


@dataclass(frozen=True, slots=True)
class Selection:
    """Every project found, on or off: examples first, then the user's own."""

    projects: tuple[Project, ...]
    problems: tuple[str, ...] = ()
    owners: Mapping[str, Project] = field(default_factory=dict)  # benchmark id -> project on

    def on(self) -> list[Project]:
        return [p for p in self.projects if p.on]

    def available(self) -> list[str]:
        return sorted(self.owners)

    def owner(self, benchmark_id: str) -> Project:
        found = self.owners.get(benchmark_id)
        if found is not None:
            return found
        off = [p for p in self.projects if benchmark_id in p.scores and not p.on]
        if off:
            raise ProjectError(
                f"{benchmark_id} is in project {off[0].id}, which is turned off: add it to "
                "[projects].examples in your config (`mph projects list` shows them all)"
            )
        broken = next((p for p in self.projects if benchmark_id in p.scores), None)
        if broken is not None and broken.problem:
            raise ProjectError(f"{benchmark_id}: project {broken.id} {broken.problem}")
        have = ", ".join(self.available()) or "none: turn a project on with [projects]"
        raise ProjectError(f"no score for {benchmark_id} (have: {have})")

    def score(self, benchmark_id: str) -> Found:
        project = self.owner(benchmark_id)
        try:
            return Found(project.path(benchmark_id).read_text("utf-8"), project)
        except OSError as exc:
            raise ProjectError(f"can't read {project.path(benchmark_id)}: {exc}") from exc

    def hidden(self, benchmark_id: str) -> bool:
        """A benchmark mph knows but leaves out, because its project is off."""
        return benchmark_id not in self.owners and any(
            benchmark_id in p.scores for p in self.projects
        )

    def specced(self) -> list[tuple[str, str, str]]:
        """The spec's benchmarks (``ideas.specced``) of the example projects that
        are on: the section a benchmark is written under names its project."""
        shown = {p.name for p in self.projects if p.example and p.on}
        return [item for item in ideas.specced() if item[2] in shown]

    def find(self, ref: str) -> Project:
        """A project by its id or name; the user's own before an example."""
        matches = [p for p in self.projects if ref in (p.id, p.name)]
        if not matches:
            have = ", ".join(sorted({p.id for p in self.projects})) or "none"
            raise ProjectError(f"no project {ref!r} (have: {have})")
        return next((p for p in matches if not p.example), matches[0])


def _text(raw: Mapping[str, Any], key: str, default: str = "") -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ProjectError(f"{PROJECT_FILE}: {key} must be text")
    return value


def read(folder: Path, *, example: bool, on: bool = True) -> Project:
    """The project in ``folder``; a broken one carries its problem."""
    scores = tuple(
        sorted(
            p.name.removesuffix(SUFFIX)
            for p in folder.iterdir()
            if p.is_file() and p.name.endswith(SUFFIX) and p.name != PROJECT_FILE
        )
    )
    plain = Project(folder.name, folder.name, folder, example, on, scores=scores)
    try:
        raw = tomllib.loads((folder / PROJECT_FILE).read_text("utf-8"))
        unknown = sorted(set(raw) - set(TEXT_KEYS))
        if unknown:
            raise ProjectError(f"{PROJECT_FILE}: unknown keys {unknown} (it takes {TEXT_KEYS})")
        return replace(
            plain,
            name=_text(raw, "name") or plain.name,
            about=_text(raw, "about"),
            spec=_text(raw, "spec"),
            needs=_text(raw, "needs"),
        )
    except (OSError, tomllib.TOMLDecodeError, ProjectError) as exc:
        return replace(plain, problem=f"has a {PROJECT_FILE} mph can't use: {exc}")


def _folders(root: Path) -> list[Path]:
    """The project folders in ``root``, or ``root`` itself when it is one."""
    if (root / PROJECT_FILE).is_file():
        return [root]
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def _projects_in(root: Path, *, example: bool, problems: list[str]) -> list[Project]:
    found = []
    for folder in _folders(root):
        if (folder / PROJECT_FILE).is_file():
            found.append(read(folder, example=example))
        elif any(p.suffix == SUFFIX for p in folder.iterdir()):
            problems.append(f'{folder} has scores but no {PROJECT_FILE}: add one with name = "..."')
    return found


def _examples(settings: Settings, problems: list[str]) -> list[Project]:
    if not settings.root.is_dir():
        if settings.examples:
            problems.append(
                f"the example projects aren't here ({settings.root}): mph runs them from a "
                "checkout of the repo"
            )
        return []
    found = _projects_in(settings.root, example=True, problems=problems)
    wanted = settings.examples
    if isinstance(wanted, bool):
        return [replace(p, on=wanted) for p in found]
    have = {p.id for p in found}
    for missing in sorted(set(wanted) - have):
        problems.append(
            f"[projects].examples names {missing!r}, which isn't an example project "
            f"(have: {', '.join(sorted(have))})"
        )
    return [replace(p, on=p.id in wanted) for p in found]


def _own(settings: Settings, problems: list[str]) -> list[Project]:
    found = []
    for folder in settings.folders:
        if not folder.is_dir():
            problems.append(
                f"{folder} isn't a folder: make it, or take it out of [projects].folders"
            )
            continue
        found += _projects_in(folder, example=False, problems=problems)
    return found


def _owners(group: Sequence[Project], problems: list[str]) -> dict[str, Project]:
    """Benchmark id -> the project on that holds it, within one kind of project."""
    owners: dict[str, Project] = {}
    for project in group:
        if project.problem:
            problems.append(f"project {project.id} ({project.folder}) {project.problem}")
            continue
        for benchmark_id in project.scores if project.on else ():
            if benchmark_id in owners:
                problems.append(
                    f"{benchmark_id} is in both {owners[benchmark_id].folder} and "
                    f"{project.folder}: keep one, or rename one (REQ-PRJ-01)"
                )
                continue
            owners[benchmark_id] = project
    return owners


def discover(settings: Settings | None = None) -> Selection:
    """Every project the settings name, and which benchmarks are on."""
    settings = settings or Settings()
    problems: list[str] = []
    examples = _examples(settings, problems)
    own = _own(settings, problems)
    owners = _owners(examples, problems) | _owners(own, problems)  # the user's own win
    return Selection((*examples, *own), tuple(problems), owners)


def settings_of(cfg: Config, root: Path | None = None) -> Settings:
    return Settings(cfg.projects.examples, cfg.projects.folders, root or EXAMPLES)


@dataclass(slots=True)
class _Chosen:
    settings: Settings = field(default_factory=Settings)


_CHOSEN = _Chosen()


def use(settings: Settings) -> None:
    """What ``current()`` finds from now on: the config's choice, set at startup."""
    _CHOSEN.settings = settings


def chosen() -> Settings:
    return _CHOSEN.settings


def current() -> Selection:
    return discover(_CHOSEN.settings)
