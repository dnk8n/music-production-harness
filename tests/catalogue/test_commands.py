from __future__ import annotations

import shutil
import struct
import wave
from contextlib import closing
from pathlib import Path

import pytest

from mph import config
from mph.catalogue import commands, db
from mph.proc import SubprocessRunner
from tests.catalogue import audiofiles as af
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


def test_analyse_before_any_scan(tmp_path: Path) -> None:
    assert (
        commands.cmd_analyse(cfg_for(tmp_path, tmp_path / "s"), [], [], None, 1, force=False) == 1
    )


@pytest.mark.req("REQ-CAT-04")
@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffprobe")
def test_scan_then_analyse_selected_role(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "samples"
    tone(root / "Pack/Kicks/Kick 1.wav")
    tone(root / "Pack/Pads/Pad 1.wav", frames=44100)
    cfg = cfg_for(tmp_path, root)
    commands.cmd_scan(cfg, SubprocessRunner(), [], 1)
    with closing(db.connect(commands.db_path(cfg))) as conn:
        assert len(commands.select_assets(conn, ["kick"], [], None)) == 1
        assert len(commands.select_assets(conn, [], ["Pack"], 1)) == 1
        assert commands.select_assets(conn, ["kick"], ["Other"], None) == []
    assert commands.cmd_analyse(cfg, ["kick"], [], None, 1, force=False) == 0
    assert "1 analysed" in capsys.readouterr().out
    assert commands.cmd_analyse(cfg, [], [], None, 1, force=False) == 0
    assert "1 analysed, 1 already up to date" in capsys.readouterr().out


@pytest.mark.req("REQ-CAT-12")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg and ffprobe")
def test_scan_reads_what_real_files_say_about_themselves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "samples"
    loop = root / "Pack/Loops/Loop 07.wav"
    loop.parent.mkdir(parents=True)
    tags = af.riff_chunk(b"LIST", af.info(INAM="Loop 07", IART="Someone", IKEY="Am"))
    loop.write_bytes(af.wav(4.0, tags, af.riff_chunk(b"acid", af.acid(beats=8, tempo=120.0))))
    (root / "Pack/broken.wav").write_bytes(b"not audio")
    song = root / "full_tracks/07 song.flac"
    song.parent.mkdir(parents=True)
    described = {"title": "Night Drive", "artist": "The Somebodies", "date": "2026", "track": "7/7"}
    made = SubprocessRunner().run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1"]
        + [arg for key, value in described.items() for arg in ("-metadata", f"{key}={value}")]
        + [str(song)]
    )
    assert made.ok, made.stderr
    cfg = cfg_for(tmp_path, root)
    assert commands.cmd_scan(cfg, SubprocessRunner(), [], 2) == 0
    assert (
        "  read the metadata inside 3 files; it labels 1 with a tempo, 1 with a key, "
        "1 as loop or one-shot, 2 with an artist, 2 with a title; 1 couldn't be read"
    ) in capsys.readouterr().out.splitlines()
    with closing(db.connect(commands.db_path(cfg))) as conn:
        labels = conn.execute("SELECT name, value FROM tag WHERE origin = 'embedded'")
        assert {tuple(row) for row in labels} == {
            ("title", "Loop 07"),
            ("artist", "Someone"),
            ("key", "A:minor"),
            ("bpm", "120"),
            ("form", "loop"),
            ("title", "Night Drive"),
            ("artist", "The Somebodies"),
            ("year", "2026"),
            ("track", "7/7"),
        }
    assert commands.cmd_scan(cfg, SubprocessRunner(), [], 2) == 0
    again = capsys.readouterr().out  # read once; only the broken file is tried again
    assert "  read the metadata inside 1 file; 1 couldn't be read" in again.splitlines()


@pytest.mark.req("REQ-CAT-12")
def test_analyse_reports_agreement_with_names_and_files_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from mph.catalogue import analysis  # noqa: PLC0415 (heavy, as in the command)

    cfg = cfg_for(tmp_path, tmp_path / "samples")
    with closing(db.connect(commands.db_path(cfg))) as conn, conn:
        conn.execute("INSERT INTO asset (hash, bytes, origin) VALUES ('a', 1, 'source')")
        conn.executemany(
            "INSERT INTO tag VALUES ('a', 'bpm', ?, ?, ?)",
            [("128", "filename", 0.6), ("120", "embedded", 0.5), ("128.1", "analysis", 0.9)],
        )
    monkeypatch.setattr(analysis, "run", lambda *args, **kwargs: analysis.AnalysisRun(cached=1))
    assert commands.cmd_analyse(cfg, [], [], None, 1, force=False) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == [
        "  bpm: analysis agrees with names on 1/1 (100%)",
        "  bpm: analysis agrees with the files' own labels on 0/1 (0%)",
    ]
