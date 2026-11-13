"""A check of the bridge tool in the real Renoise, for scripts/renoise-check (REQ-REN-08).

    python scripts/renoise_check.py

Run it on Dean's Mac with Renoise closed. It installs this checkout's bridge
tool, then, in one Renoise that mph starts:

1. a job builds, renders and saves two tiny throwaway songs, A and B;
2. A is opened the way "Open in Renoise" opens a render's song, and a second
   job must keep it as a copy in mph's scratch folder before it builds C;
3. B is opened the same way, and Renoise is asked to quit.

Renoise has to do all of that without asking to save anything: a question
would hold up a job, or keep Renoise open after it was asked to quit. The
songs live in a temporary folder inside mph's scratch folder and are deleted
at the end, with A's copy. The bridge tool that was installed before is put
back. Sources, renders and the catalogue aren't touched.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from mph import config, proc
from mph.bench import audio
from mph.config import Config
from mph.proc import Runner
from mph.renoise import bridge, luadata

RATE = 48000
JOB_TIMEOUT_S = 300.0  # longer than the tool waits for a new song, so its own error shows
LOAD_S = 5.0  # time for Renoise to open a song
QUIT_S = 30.0  # time for Renoise to quit
QUIT = ("osascript", "-e", 'tell application "Renoise" to quit')


class CheckFailed(RuntimeError):
    pass


@contextmanager
def tool_put_back(home: Path) -> Iterator[None]:
    """Whatever bridge tool was installed before is installed again afterwards."""
    tools = bridge.tools_folder(home)
    installed = tools / bridge.TOOL_FOLDER if tools else None
    with tempfile.TemporaryDirectory(prefix="mph-tool-") as spare:
        before = Path(spare) / bridge.TOOL_FOLDER
        if installed and installed.is_dir():
            shutil.copytree(installed, before)
        try:
            yield
        finally:
            if installed:
                shutil.rmtree(installed, ignore_errors=True)
                if before.is_dir():
                    shutil.copytree(before, installed)


def blip(path: Path) -> Path:
    """A tenth of a second of decaying sine to build songs from."""
    t = np.arange(RATE // 10) / RATE
    audio.write_float(path, 0.5 * np.sin(2 * np.pi * 440 * t) * np.exp(-30 * t), RATE)
    return path


def song(folder: Path, sample: Path, name: str) -> dict[str, luadata.Data]:
    """One bar at 120 BPM, a blip on each beat, rendered as a mix and then solo."""
    notes: list[luadata.Data] = [
        {"line": line, "delay": 0, "volume": 0x80} for line in (0, 4, 8, 12)
    ]
    track: dict[str, luadata.Data] = {
        "name": "blip",
        "sample": str(sample),
        "volume": 1.0,
        "transpose": 0,
        "fine_tune": 0,
        "slices": [],
        "notes": notes,
    }
    return {
        "bpm": 120,
        "lpb": 4,
        "pattern_lines": 16,
        "lines": 16,
        "rate": RATE,
        "save": str(folder / f"{name}.xrns"),
        "tracks": [track],
        "renders": [
            {"file": str(folder / f"{name}.wav")},
            {"file": str(folder / f"{name} solo.wav"), "solo": 1},
        ],
    }


class Check:
    def __init__(self, cfg: Config, runner: Runner, home: Path, clock: bridge.Clock) -> None:
        self.cfg, self.runner, self.home, self.clock = cfg, runner, home, clock
        self.session = bridge.Session()

    def job(self, name: str, songs: Sequence[luadata.Data]) -> None:
        bridge.run_job(
            self.cfg,
            self.runner,
            self.home,
            name,
            {"songs": list(songs)},
            timeout_s=JOB_TIMEOUT_S,
            clock=self.clock,
            session=self.session,
        )

    def open(self, path: Path) -> None:
        bridge.open_song(self.runner, path)
        self.clock.sleep(LOAD_S)

    def run(self, folder: Path) -> None:
        sample = blip(folder / "blip.wav")
        self.job("renoise-check-1", [song(folder, sample, "A"), song(folder, sample, "B")])
        print("job 1: built, rendered and saved A and B")
        self.open(folder / "A.xrns")
        print('opened A, as "Open in Renoise" does')
        self.job("renoise-check-2", [song(folder, sample, "C")])
        if [kept.song.name for kept in self.session.kept] != ["A.xrns"]:
            raise CheckFailed("job 2 didn't keep a copy of A, the song opened by hand")
        copy = self.session.kept[0].copy
        if not copy.is_file():
            raise CheckFailed(f"job 2 said it kept A as {copy}, but that file isn't there")
        print(f"job 2: kept A as {copy.name}, then built, rendered and saved C")
        self.open(folder / "B.xrns")
        print("opened B")
        self.quit()

    def quit(self) -> None:
        asked = self.runner.run(QUIT)
        if not asked.ok:
            raise CheckFailed(
                f"couldn't ask Renoise to quit ({(asked.stderr or asked.stdout).strip()}). "
                "Quit it by hand: if it asks to save, the check failed"
            )
        deadline = self.clock.now() + QUIT_S
        while bridge.running(self.runner):
            if self.clock.now() > deadline:
                raise CheckFailed(
                    "Renoise is still open after being asked to quit, so it's asking "
                    "whether to save. Answer it, then say what it asked"
                )
            self.clock.sleep(1.0)
        print("asked Renoise to quit: it quit without asking to save")


def check(cfg: Config, runner: Runner, home: Path, clock: bridge.Clock) -> int:
    if bridge.running(runner):
        print("Renoise is open. Save your work and quit it, then run the check again.")
        return 2
    scratch = bridge.scratch_dir(cfg)
    scratch.mkdir(parents=True, exist_ok=True)
    run = Check(cfg, runner, home, clock)
    with (
        tool_put_back(home),
        tempfile.TemporaryDirectory(prefix="renoise-check-", dir=scratch) as folder,
    ):
        try:
            bridge.install(cfg, home)
            run.run(Path(folder))
        except (bridge.BridgeError, CheckFailed) as exc:
            print(f"FAIL: {exc}")
            return 1
        finally:
            for kept in run.session.kept:
                kept.copy.unlink(missing_ok=True)
    print("PASS: Renoise never asked to save")
    return 0


def main() -> int:
    return check(config.load(), proc.SubprocessRunner(), Path.home(), bridge.Clock())


if __name__ == "__main__":
    sys.exit(main())
