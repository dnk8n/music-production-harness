from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph import cli, config
from mph.catalogue import commands, db, provenance
from mph.catalogue.provenance import ProvenanceError, Workshop
from mph.proc import Result, SubprocessRunner
from tests.fakes import FakeRunner

PROBE = json.dumps(
    {
        "streams": [{"codec_name": "pcm_f32le", "sample_rate": "44100", "channels": 2}],
        "format": {"format_name": "wav"},
    }
)


class FakeFfmpeg:
    """Writes deterministic bytes derived from the input and the cut points."""

    def __init__(self) -> None:
        self.version = "a"
        self.runs = 0

    def __call__(self, argv: Sequence[str]) -> Result:
        self.runs += 1
        source = Path(argv[argv.index("-i") + 1]).read_bytes()
        cut = f"{argv[argv.index('-ss') + 1]}/{argv[argv.index('-t') + 1]}/{self.version}"
        Path(argv[-1]).write_bytes(hashlib.sha256(source + cut.encode()).digest())
        return Result(0)


@pytest.fixture
def ffmpeg() -> FakeFfmpeg:
    return FakeFfmpeg()


@pytest.fixture
def setup(tmp_path: Path, ffmpeg: FakeFfmpeg) -> Iterator[tuple[sqlite3.Connection, Workshop]]:
    root = tmp_path / "samples"
    root.mkdir()
    (root / "vox.wav").write_bytes(b"spoken word")
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    with conn:
        conn.execute(
            "INSERT INTO source VALUES ('dj', 'local', ?, 'commercial-pack', NULL)", (str(root),)
        )
        conn.execute("INSERT INTO asset (hash, bytes, origin) VALUES ('src1aaaaaa', 11, 'source')")
        conn.execute("INSERT INTO source_file VALUES ('dj', 'vox.wav', 11, 1, 'src1aaaaaa', 1)")
    runner = FakeRunner(responses={"ffmpeg": ffmpeg, "ffprobe": Result(0, PROBE)})
    yield conn, Workshop(conn, runner, tmp_path / "data", clock=lambda: "t")
    conn.close()


def trim(start: float = 1.0, end: float = 2.0) -> provenance.Recipe:
    return provenance.recipe("trim", {"start": start, "end": end})


@pytest.mark.req("REQ-CAT-06")
def test_derive_records_recipe_inputs_and_reuses(
    setup: tuple[sqlite3.Connection, Workshop], ffmpeg: FakeFfmpeg, tmp_path: Path
) -> None:
    conn, workshop = setup
    made = workshop.derive(trim(), ["src1aaaaaa"])
    assert not made.reused
    assert made.path == provenance.cache_path(tmp_path / "data", made.digest)
    row = conn.execute("SELECT * FROM derivation").fetchone()
    assert json.loads(row["inputs_json"]) == ["src1aaaaaa"]
    assert conn.execute("SELECT origin FROM asset WHERE hash = ?", (made.digest,)).fetchone()[
        0
    ] == ("derivative")
    again = workshop.derive(trim(), ["src1aaaaaa"])
    assert (again.digest, again.reused, ffmpeg.runs) == (made.digest, True, 1)
    assert provenance.lineage(conn, made.digest) == [
        f"{made.digest[:12]} trim(start=1, end=2)",
        "  src1aaaaaa source dj:vox.wav",
    ]


@pytest.mark.req("REQ-CAT-01")
def test_derive_never_writes_under_the_source(
    setup: tuple[sqlite3.Connection, Workshop], tmp_path: Path
) -> None:
    _, workshop = setup
    workshop.derive(trim(), ["src1aaaaaa"])
    assert sorted(p.name for p in (tmp_path / "samples").iterdir()) == ["vox.wav"]


@pytest.mark.req("REQ-CAT-06")
def test_purge_then_refresh_rebuilds_identical_bytes(
    setup: tuple[sqlite3.Connection, Workshop], tmp_path: Path
) -> None:
    conn, workshop = setup
    first = workshop.derive(trim(), ["src1aaaaaa"])
    second = workshop.derive(trim(0, 0.5), [first.digest])  # a derivative of a derivative
    data = tmp_path / "data"
    assert provenance.purge_cache(data) == 2
    assert provenance.locate(conn, data, second.digest) is None
    report = provenance.refresh(workshop, conn, data)
    assert (report.present, sorted(report.rebuilt), report.failed) == (
        0,
        sorted([first.digest, second.digest]),
        [],
    )
    assert provenance.locate(conn, data, second.digest) == second.path
    assert provenance.refresh(workshop, conn, data).present == 2


@pytest.mark.req("REQ-CAT-06")
def test_stash_survives_purge(setup: tuple[sqlite3.Connection, Workshop], tmp_path: Path) -> None:
    conn, workshop = setup
    data = tmp_path / "data"
    made = workshop.derive(trim(), ["src1aaaaaa"])
    kept = provenance.stash(conn, data, made.digest, "track-1")
    provenance.purge_cache(data)
    assert provenance.locate(conn, data, made.digest) == kept
    assert provenance.purge_cache(data) == 0


@pytest.mark.req("REQ-CAT-06")
def test_changed_tool_output_is_caught_not_silently_accepted(
    setup: tuple[sqlite3.Connection, Workshop], ffmpeg: FakeFfmpeg, tmp_path: Path
) -> None:
    conn, workshop = setup
    workshop.derive(trim(), ["src1aaaaaa"])
    provenance.purge_cache(tmp_path / "data")
    ffmpeg.version = "b"  # e.g. a new ffmpeg that writes different bytes
    report = provenance.refresh(workshop, conn, tmp_path / "data")
    assert len(report.failed) == 1
    assert "tool's output changed" in report.failed[0]
    assert not list((tmp_path / "data" / "cache").rglob("*.wav"))


@pytest.mark.parametrize(
    ("kind", "params", "message"),
    [
        ("reverse", {}, "unknown recipe"),
        ("trim", {"start": 1}, "trim takes start, end"),
        ("trim", {"start": 1, "end": 2, "gain": 3}, "trim takes start, end"),
    ],
)
def test_bad_recipes(kind: str, params: dict[str, float], message: str) -> None:
    with pytest.raises(ProvenanceError, match=message):
        provenance.recipe(kind, params)


def test_errors_are_explained(setup: tuple[sqlite3.Connection, Workshop], tmp_path: Path) -> None:
    conn, workshop = setup
    data = tmp_path / "data"
    with pytest.raises(ProvenanceError, match="start < end"):
        workshop.derive(trim(2, 1), ["src1aaaaaa"])
    with pytest.raises(ProvenanceError, match="takes 1 input"):
        workshop.derive(trim(), [])
    with pytest.raises(ProvenanceError, match="not available"):
        workshop.derive(trim(), ["nowhere"])
    with pytest.raises(ProvenanceError, match="already permanent"):
        provenance.stash(conn, data, "src1aaaaaa", "x")
    with pytest.raises(ProvenanceError, match="unknown sound"):
        provenance.stash(conn, data, "nope", "x")
    with pytest.raises(ProvenanceError, match="bad stash name"):
        provenance.stash(conn, data, "src1aaaaaa", "../escape")
    with pytest.raises(ProvenanceError, match="not a derivative"):
        workshop.rebuild("src1aaaaaa")
    made = workshop.derive(trim(), ["src1aaaaaa"])
    provenance.purge_cache(data)
    with pytest.raises(ProvenanceError, match="refresh"):
        provenance.stash(conn, data, made.digest, "x")


def test_tool_failure_leaves_no_scratch(tmp_path: Path) -> None:
    root = tmp_path / "samples"
    root.mkdir()
    (root / "a.wav").write_bytes(b"x")
    with db.connect(tmp_path / "data" / db.FILENAME) as conn:
        conn.execute("INSERT INTO source VALUES ('dj', 'local', ?, 'x', NULL)", (str(root),))
        conn.execute("INSERT INTO asset (hash, bytes, origin) VALUES ('h1aaaaaaaa', 1, 'source')")
        conn.execute("INSERT INTO source_file VALUES ('dj', 'a.wav', 1, 1, 'h1aaaaaaaa', 1)")
    runner = FakeRunner(responses={"ffmpeg": Result(1, stderr="Invalid argument")})
    workshop = Workshop(conn, runner, tmp_path / "data")
    with pytest.raises(ProvenanceError, match="Invalid argument"):
        workshop.derive(trim(), ["h1aaaaaaaa"])
    assert not list((tmp_path / "data" / "cache").iterdir())
    conn.close()


def test_resolve(setup: tuple[sqlite3.Connection, Workshop]) -> None:
    conn, _ = setup
    assert provenance.resolve(conn, "vox.wav") == "src1aaaaaa"
    assert provenance.resolve(conn, "src1aa") == "src1aaaaaa"
    with pytest.raises(ProvenanceError, match="at least 6"):
        provenance.resolve(conn, "src")
    with pytest.raises(ProvenanceError, match="no sounds"):
        provenance.resolve(conn, "ffffff")


@pytest.mark.req("REQ-AUD-05")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_trim_is_bit_exact_and_keeps_native_rate(tmp_path: Path) -> None:
    root = tmp_path / "samples"
    root.mkdir()
    tone = np.sin(np.linspace(0, 2000, 44100 * 3)).astype(np.float32)
    soundfile.write(root / "tone.wav", np.stack([tone, tone], axis=1), 44100, subtype="PCM_16")
    digests = []
    for run in ("one", "two"):
        with db.connect(tmp_path / run / db.FILENAME) as conn:
            conn.execute("INSERT INTO source VALUES ('dj', 'local', ?, 'x', NULL)", (str(root),))
            conn.execute(
                "INSERT INTO asset (hash, bytes, origin) VALUES ('t1aaaaaaaa', 1, 'source')"
            )
            conn.execute("INSERT INTO source_file VALUES ('dj', 'tone.wav', 1, 1, 't1aaaaaaaa', 1)")
        workshop = Workshop(conn, SubprocessRunner(timeout_s=60), tmp_path / run)
        made = workshop.derive(trim(0.5, 1.5), ["t1aaaaaaaa"])
        info = soundfile.info(str(made.path))
        assert (info.samplerate, info.subtype, info.channels) == (44100, "FLOAT", 2)
        assert info.frames == pytest.approx(44100, abs=2)
        digests.append(made.digest)
        conn.close()
    assert digests[0] == digests[1]


def cfg_for(tmp_path: Path) -> config.Config:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    return config.parse(raw)


def args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["catalogue", *argv])


@pytest.mark.req("REQ-CAT-06")
def test_cli_derive_stash_purge_refresh_lineage(
    setup: tuple[sqlite3.Connection, Workshop],
    ffmpeg: FakeFfmpeg,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = cfg_for(tmp_path)
    runner = FakeRunner(responses={"ffmpeg": ffmpeg, "ffprobe": Result(0, PROBE)})
    run = commands.cmd_provenance
    assert run(cfg, runner, args("derive", "vox.wav", "trim", "--end", "1", "--stash", "t1")) == 0
    out = capsys.readouterr().out
    assert out.startswith("made ")
    assert "stashed:" in out
    digest = out.split()[1].rstrip(":")
    assert run(cfg, runner, args("derive", "vox.wav", "trim", "--end", "1")) == 0
    assert capsys.readouterr().out.startswith("reused ")
    assert run(cfg, runner, args("purge-cache")) == 0
    assert "removed 1 cached files" in capsys.readouterr().out
    assert run(cfg, runner, args("refresh")) == 0
    assert "1 present, 0 rebuilt" in capsys.readouterr().out  # the stash still has it
    assert run(cfg, runner, args("lineage", digest)) == 0
    assert "trim(start=0, end=1)" in capsys.readouterr().out
    assert run(cfg, runner, args("stash", digest, "t2")) == 0
    assert run(cfg, runner, args("stash", "vox.wav", "t2")) == 2


def test_cli_refresh_failures_and_missing_catalogue(
    setup: tuple[sqlite3.Connection, Workshop],
    ffmpeg: FakeFfmpeg,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = cfg_for(tmp_path)
    runner = FakeRunner(responses={"ffmpeg": ffmpeg, "ffprobe": Result(0, PROBE)})
    assert (
        commands.cmd_provenance(cfg, runner, args("derive", "vox.wav", "trim", "--end", "1")) == 0
    )
    commands.cmd_provenance(cfg, runner, args("purge-cache"))
    ffmpeg.version = "b"
    assert commands.cmd_provenance(cfg, runner, args("refresh")) == 1
    assert "! " in capsys.readouterr().out
    assert commands.cmd_provenance(cfg_for(tmp_path / "x"), runner, args("refresh")) == 1


class FakeDemucs:
    """Writes every stem the model makes, as bytes made from the input and its name."""

    def __init__(self) -> None:
        self.version = "4.1.0"
        self.runs = 0
        self.argv: list[str] = []

    def __call__(self, argv: Sequence[str]) -> Result:
        self.runs += 1
        self.argv = list(argv)
        model = argv[argv.index("-n") + 1]
        out = Path(argv[argv.index("-o") + 1]) / model
        out.mkdir(parents=True)
        source = Path(argv[-1]).read_bytes()
        for stem in provenance.DEMUCS_MODELS[model]:
            made = hashlib.sha256(source + f"{model}/{stem}/{self.version}".encode()).digest()
            (out / f"{stem}.wav").write_bytes(made)
        return Result(0, stderr="100%|##########| 337.5/337.5 seconds")


@pytest.fixture
def demucs() -> FakeDemucs:
    return FakeDemucs()


@pytest.fixture
def separating(
    setup: tuple[sqlite3.Connection, Workshop],
    demucs: FakeDemucs,
    ffmpeg: FakeFfmpeg,
    tmp_path: Path,
) -> Workshop:
    responses = {"demucs": demucs, "ffmpeg": ffmpeg, "ffprobe": Result(0, PROBE)}
    runner = FakeRunner(responses=responses)  # type: ignore[arg-type]
    return Workshop(setup[0], runner, tmp_path / "data", clock=lambda: "t")


def stem(part: str, model: str = "htdemucs_ft") -> provenance.Recipe:
    return provenance.recipe("stems", {"model": model, "part": part})


@pytest.mark.req("REQ-CAT-11")
def test_one_separation_makes_every_stem_each_with_its_own_recipe(
    setup: tuple[sqlite3.Connection, Workshop], separating: Workshop, demucs: FakeDemucs
) -> None:
    conn = setup[0]
    vocals = separating.derive(stem("vocals"), ["src1aaaaaa"])
    assert not vocals.reused
    flags = " ".join(demucs.argv)
    for flag in ("-n htdemucs_ft", "-d cpu", "--shifts 0", "--float32", "--clip-mode none"):
        assert flag in flags  # deterministic, unscaled float stems at the track's rate
    recorded = {
        json.loads(row["params_json"])["part"]
        for row in conn.execute("SELECT params_json FROM recipe WHERE kind = 'stems'")
    }
    assert recorded == {"drums", "bass", "other", "vocals"}
    drums = separating.derive(stem("drums"), ["src1aaaaaa"])
    assert (drums.reused, demucs.runs) == (True, 1)  # made by the same run
    assert drums.digest != vocals.digest
    assert provenance.lineage(conn, vocals.digest) == [
        f"{vocals.digest[:12]} stems(model=htdemucs_ft, part=vocals)",
        "  src1aaaaaa source dj:vox.wav",
    ]
    trimmed = separating.derive(trim(0, 1), [vocals.digest])  # a phrase cut from the stem
    assert provenance.lineage(conn, trimmed.digest)[1].endswith("part=vocals)")


@pytest.mark.req("REQ-CAT-11")
@pytest.mark.req("REQ-CAT-06")
def test_one_separation_rebuilds_every_purged_stem(
    setup: tuple[sqlite3.Connection, Workshop],
    separating: Workshop,
    demucs: FakeDemucs,
    tmp_path: Path,
) -> None:
    conn, data = setup[0], tmp_path / "data"
    made = {part: separating.derive(stem(part), ["src1aaaaaa"]) for part in ("vocals", "bass")}
    assert provenance.purge_cache(data) == 4
    report = provenance.refresh(separating, conn, data)
    assert (len(report.rebuilt), report.present, report.failed, demucs.runs) == (1, 3, [], 2)
    assert all(provenance.locate(conn, data, m.digest) == m.path for m in made.values())


@pytest.mark.req("REQ-CAT-11")
@pytest.mark.req("REQ-CAT-06")
def test_a_new_demucs_that_changes_the_stems_is_reported_not_swapped_in(
    setup: tuple[sqlite3.Connection, Workshop],
    separating: Workshop,
    demucs: FakeDemucs,
    tmp_path: Path,
) -> None:
    conn, data = setup[0], tmp_path / "data"
    separating.derive(stem("vocals"), ["src1aaaaaa"])
    provenance.purge_cache(data)
    demucs.version = "4.2.0"
    report = provenance.refresh(separating, conn, data)
    assert len(report.failed) == 4
    assert "a new stems tool?" in report.failed[0]
    assert not list((data / "cache").rglob("*.wav"))


@pytest.mark.req("REQ-CAT-11")
@pytest.mark.req("REQ-AUD-01")
def test_a_track_demucs_would_resample_is_refused(
    setup: tuple[sqlite3.Connection, Workshop], separating: Workshop, demucs: FakeDemucs
) -> None:
    with setup[0] as conn:
        conn.execute("UPDATE asset SET sample_rate = 48000 WHERE hash = 'src1aaaaaa'")
    with pytest.raises(ProvenanceError, match="works at 44100 Hz and src1aaaaaa is 48000 Hz"):
        separating.derive(stem("vocals"), ["src1aaaaaa"])
    assert demucs.runs == 0


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"model": "mdx_extra", "part": "vocals"}, "unknown Demucs model 'mdx_extra'"),
        ({"model": "htdemucs", "part": "piano"}, "htdemucs makes drums, bass, other, vocals"),
        ({"model": "htdemucs"}, "stems takes model, part"),
    ],
)
def test_bad_stem_recipes(params: dict[str, str], message: str) -> None:
    with pytest.raises(ProvenanceError, match=message):
        provenance.recipe("stems", params)
    assert provenance.parts("stems", {"model": "htdemucs_6s"})[-2:] == ("guitar", "piano")


def test_a_separation_that_makes_no_stems_fails_cleanly(
    setup: tuple[sqlite3.Connection, Workshop], tmp_path: Path
) -> None:
    runner = FakeRunner(responses={"demucs": Result(0), "ffprobe": Result(0, PROBE)})
    workshop = Workshop(setup[0], runner, tmp_path / "data")
    with pytest.raises(ProvenanceError, match="stems failed: made no drums, bass, other, vocals"):
        workshop.derive(stem("vocals"), ["src1aaaaaa"])
    assert not list((tmp_path / "data" / "cache").iterdir())


@pytest.mark.req("REQ-CAT-11")
def test_cli_derives_every_stem_in_one_run(
    setup: tuple[sqlite3.Connection, Workshop],
    demucs: FakeDemucs,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = cfg_for(tmp_path)
    runner = FakeRunner(responses={"demucs": demucs, "ffprobe": Result(0, PROBE)})
    run = commands.cmd_provenance
    assert run(cfg, runner, args("derive", "vox.wav", "stems", "--model", "htdemucs")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("separating with Demucs htdemucs")
    assert [line.split()[:2] for line in lines[1:]] == [
        ["made", part] for part in ("drums", "bass", "other", "vocals")
    ]
    assert run(cfg, runner, args("derive", "vox.wav", "stems", "--model", "htdemucs")) == 0
    assert capsys.readouterr().out.count("reused ") == 4
    assert demucs.runs == 1
    assert run(cfg, runner, args("derive", "vox.wav", "trim")) == 2
    assert "trim needs --end" in capsys.readouterr().err


def test_separating_and_rebuilding_may_take_an_hour() -> None:
    assert cli._timeout_s(args("derive", "vox.wav", "stems")) == cli.RECIPE_TIMEOUT_S
    assert cli._timeout_s(args("refresh")) == cli.RECIPE_TIMEOUT_S
    assert cli._timeout_s(args("stats")) == 30.0
