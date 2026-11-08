from __future__ import annotations

import shutil
import struct
import wave
from pathlib import Path

import pytest

from mph import config
from mph.catalogue import commands
from mph.proc import SubprocessRunner
from tests.fakes import FakeRunner


def cfg_for(tmp_path: Path, root: Path) -> config.Config:
    raw = config.deep_merge(
        config._defaults(),
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "sources": [
                {"id": "dj", "kind": "local", "path": str(root)},
                {"id": "ia", "kind": "remote", "url": "https://archive.org"},
            ],
        },
    )
    return config.parse(raw)


def tone(path: Path, rate: int = 44100, frames: int = 4410) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", (i * 97) % 2000) for i in range(frames)))


def test_unknown_or_remote_source_rejected(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path, tmp_path / "samples")
    assert commands.cmd_scan(cfg, FakeRunner(), ["nope"], 2) == 2
    assert commands.cmd_scan(cfg, FakeRunner(), ["ia"], 2) == 2


def test_missing_local_directory_reported(tmp_path: Path) -> None:
    assert commands.cmd_scan(cfg_for(tmp_path, tmp_path / "gone"), FakeRunner(), [], 2) == 1


def test_stats_before_any_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert commands.cmd_stats(cfg_for(tmp_path, tmp_path / "samples")) == 1
    assert "mph catalogue scan" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffprobe")
def test_scan_and_stats_with_real_ffprobe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "samples"
    tone(root / "Pack/Kicks/Kick 1.wav")
    tone(root / "Pack/Loops (Am, 124bpm)/Perc Loop.wav", rate=48000)
    cfg = cfg_for(tmp_path, root)
    assert commands.cmd_scan(cfg, SubprocessRunner(), [], 2) == 0
    assert "2 audio files" in capsys.readouterr().out
    assert commands.cmd_stats(cfg) == 0
    out = capsys.readouterr().out
    assert "2 unique sounds" in out
    assert "wav 44.1k/16b" in out
    assert "wav 48.0k/16b" in out
    assert "kick" in out
