"""Talking to Renoise through the bridge tool's job folder (REQ-REN-03, REQ-REN-07).

``install`` copies the Lua tool into Renoise's Tools folder, with a
``settings.lua`` naming the job folder. ``run_job`` writes a job, starts
Renoise in the background if needed, and waits for the tool's result file.
Every job also says where scratch songs go, so the tool never leaves Renoise
with anything to ask about saving (REQ-REN-08). Processes are spawned only
through ``mph.proc.Runner``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from mph.config import Config
from mph.proc import Runner
from mph.renoise import luadata

TOOL_ID = "com.mph.bridge"
TOOL_FOLDER = f"{TOOL_ID}.xrnx"
TOOL_FILES = ("manifest.xml", "main.lua", "build.lua", "json.lua")
SETTINGS = "settings.lua"
APP = "/Applications/Renoise.app"
PROCESS = "Renoise"
JOBS = Path("renoise") / "jobs"
SCRATCH = Path("renoise") / "scratch"
SCRATCH_SONG = "mph-scratch.xrns"  # the song mph leaves open in Renoise (REQ-REN-08)
POLL_S = 0.25
# Where Renoise keeps its per-version preferences: macOS first, then Linux.
PREFERENCE_ROOTS = ("Library/Preferences/Renoise", ".renoise")


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Clock:
    """Time, injectable so tests never wait."""

    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def _version_key(folder: Path) -> tuple[int, ...]:
    digits = folder.name.removeprefix("V").split(".")
    return tuple(int(d) if d.isdigit() else 0 for d in digits)


def tools_folder(home: Path) -> Path | None:
    """Renoise's Tools folder for its newest version on this machine."""
    for root in PREFERENCE_ROOTS:
        versions = sorted(
            (p for p in (home / root).glob("V*") if p.is_dir()), key=_version_key, reverse=True
        )
        if versions:
            return versions[0] / "Scripts" / "Tools"
    return None


def jobs_dir(cfg: Config) -> Path:
    return cfg.data_dir / JOBS


def scratch_dir(cfg: Config) -> Path:
    return cfg.data_dir / SCRATCH


def _packaged(name: str) -> bytes:
    return resources.files("mph.renoise").joinpath("tool", TOOL_FOLDER, name).read_bytes()


def tool_digest() -> str:
    digest = hashlib.blake2b(digest_size=8)
    for name in TOOL_FILES:
        digest.update(_packaged(name))
    return digest.hexdigest()


def installed_digest(home: Path) -> str | None:
    folder = tools_folder(home)
    if folder is None or not (folder / TOOL_FOLDER).is_dir():
        return None
    digest = hashlib.blake2b(digest_size=8)
    for name in TOOL_FILES:
        path = folder / TOOL_FOLDER / name
        if not path.is_file():
            return None
        digest.update(path.read_bytes())
    return digest.hexdigest()


def app_installed() -> bool:
    return Path(APP).is_dir()


def status(home: Path) -> str:
    """'ready', 'outdated', 'missing' (tool not installed) or 'no-renoise'."""
    if tools_folder(home) is None:
        return "no-renoise"
    found = installed_digest(home)
    if found is None:
        return "missing"
    return "ready" if found == tool_digest() else "outdated"


def install(cfg: Config, home: Path) -> Path:
    """Copy the tool into Renoise's Tools folder; returns where it went."""
    folder = tools_folder(home)
    if folder is None:
        raise BridgeError(
            "Renoise's preferences folder wasn't found: open Renoise once, then try again"
        )
    target = folder / TOOL_FOLDER
    target.mkdir(parents=True, exist_ok=True)
    for name in TOOL_FILES:
        (target / name).write_bytes(_packaged(name))
    jobs = jobs_dir(cfg)
    jobs.mkdir(parents=True, exist_ok=True)
    (target / SETTINGS).write_text(luadata.document({"jobs": str(jobs)}), "utf-8")
    return target


def running(runner: Runner) -> bool:
    return runner.run(["pgrep", "-x", PROCESS]).ok


def launch(runner: Runner) -> None:
    """Start Renoise in the background (-g: don't bring it to the front)."""
    result = runner.run(["open", "-g", "-a", APP])
    if not result.ok:
        raise BridgeError(f"couldn't start Renoise: {(result.stderr or result.stdout).strip()}")


def scratch_paths(cfg: Config) -> dict[str, luadata.Data]:
    """Where the tool saves songs so Renoise never asks to (REQ-REN-08): the
    scratch song it leaves open, the folder for copies of mph songs someone
    opened, and mph's own folder, which tells mph's songs from anyone else's."""
    folder = scratch_dir(cfg)
    return {"song": str(folder / SCRATCH_SONG), "folder": str(folder), "ours": str(cfg.data_dir)}


def submit(cfg: Config, name: str, job: Mapping[str, luadata.Data]) -> Path:
    """Write a job for the tool; returns the result file to wait for."""
    jobs = jobs_dir(cfg)
    jobs.mkdir(parents=True, exist_ok=True)
    scratch_dir(cfg).mkdir(parents=True, exist_ok=True)
    result = jobs / f"{name}.result.json"
    result.unlink(missing_ok=True)
    temporary = jobs / f"{name}.job.tmp"
    temporary.write_text(luadata.document({**job, "scratch": scratch_paths(cfg)}), "utf-8")
    temporary.replace(jobs / f"{name}.job.lua")
    return result


def wait(result: Path, timeout_s: float, clock: Clock) -> dict[str, Any]:
    """The tool's result, once it's there; failed or not."""
    deadline = clock.now() + timeout_s
    while not result.is_file():
        if clock.now() > deadline:
            raise BridgeError(
                f"Renoise didn't finish within {timeout_s:g} s. Check that it's open and "
                "that the mph bridge tool is listed under Tools, then try again"
            )
        clock.sleep(POLL_S)
    outcome: dict[str, Any] = json.loads(result.read_text("utf-8"))
    result.unlink()
    return outcome


@dataclass(frozen=True, slots=True)
class Kept:
    """An mph song someone had open, which the tool saved as a copy before a job."""

    song: Path  # where it was opened from (left as it was)
    copy: Path  # the copy, with any changes made to it by hand

    def note(self) -> str:
        return (
            f"Renoise had {self.song.name} open, so mph saved it, with any changes, "
            f"as {self.copy} before rendering. {self.song.name} itself is unchanged."
        )


def kept_songs(outcome: Mapping[str, Any]) -> list[Kept]:
    found = outcome.get("kept") or []
    return [Kept(Path(str(k["from"])), Path(str(k["to"]))) for k in found if isinstance(k, dict)]


@dataclass(slots=True)
class Session:
    """One mph command's use of Renoise: remembers whether mph started it (REQ-REN-03).

    mph only renders in a Renoise it started itself, so a song someone is
    working on is never touched. Later jobs in the same command reuse it.
    ``kept`` lists the songs the tool saved out of the way first (REQ-REN-08).
    """

    launched: bool = False
    kept: list[Kept] = field(default_factory=list)


ALREADY_OPEN = (
    "Renoise is already open. Save your work and quit Renoise, then render again: "
    "mph starts its own fresh Renoise so your song isn't touched"
)


def run_job(
    cfg: Config,
    runner: Runner,
    home: Path,
    name: str,
    job: Mapping[str, luadata.Data],
    *,
    timeout_s: float,
    clock: Clock | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    state = status(home)
    if state != "ready":
        what = {"no-renoise": "isn't installed", "missing": "isn't installed"}.get(
            state, "is out of date"
        )
        raise BridgeError(
            f"the mph bridge tool for Renoise {what}: run `mph renoise install`, "
            "then restart Renoise"
        )
    session = Session() if session is None else session
    is_running = running(runner)
    if is_running and not session.launched:
        raise BridgeError(ALREADY_OPEN)
    result = submit(cfg, name, job)
    if not is_running:
        launch(runner)
        session.launched = True
    outcome = wait(result, timeout_s, clock or Clock())
    session.kept += kept_songs(outcome)
    if not outcome.get("ok"):
        raise BridgeError(f"Renoise: {outcome.get('error', 'unknown error')}")
    return outcome


def open_song(runner: Runner, song: Path) -> None:
    """Open a song in Renoise, in the foreground (REQ-REN-07)."""
    result = runner.run(["open", "-a", APP, str(song)])
    if not result.ok:
        raise BridgeError(f"couldn't open {song.name}: {(result.stderr or result.stdout).strip()}")
