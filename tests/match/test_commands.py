"""`mph match analyse|check|run` end to end, with a stand-in scsynth
(spec/components/matching.md, REQ-MAT-01, REQ-MAT-05, REQ-MAT-06)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mph import cli, config
from mph.bench import audio, score
from mph.catalogue import db, provenance
from mph.components import Context
from mph.match import runs
from mph.match.matchfile import Play
from mph.platform_info import Host
from mph.proc import Runner
from tests.bench.fake_sc import fake_sclang
from tests.fakes import FakeRunner
from tests.match.fake_synth import play

RATE = 22050
HIDDEN = {"brass": 900.0, "swell": 0.3}  # the made-up horn the reference is
MATCH = """
synth = "mph_horn"
why = "a test"
budget = 360
seed = 3
tail_s = 0.5

[reference]
path = "horn.wav"
start_s = 0.5
end_s = 1.5
what = "a made-up horn"
licence = "made by the test"

[play]
notes = ["E3"]
at_s = 0.1
length_s = 0.8

[knobs]
brass = [300, 3000, "log"]
swell = [0.05, 0.8]
"""
SCORE = """
id = "BM-T"
bpm = 120
bars = 1

[voices.horn]
notes = "E3 -  -  -  -  -  -  -  -  -  -  -  -  -  -  -"
gate  = "x  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -"
sound = { synth = "mph_horn" }
params = { brass = 900, swell = 0.3 }
"""


def runner(tools: bool = True) -> FakeRunner:
    from tests.match.fake_synth import knob_scsynth  # noqa: PLC0415

    found = FakeRunner(responses={"scsynth": knob_scsynth, "sclang": fake_sclang})
    if tools:
        found.on_path = {"scsynth": "/usr/bin/scsynth", "sclang": "/usr/bin/sclang"}
    return found


def cfg_for(tmp_path: Path) -> config.Config:
    raw = config.deep_merge(config._defaults(), {"paths": {"data_dir": str(tmp_path / "data")}})
    return config.parse(raw)


def made_up_horn(path: Path, patch: dict[str, float] = HIDDEN) -> None:
    """Half a second of silence, then the span the match fits, as the fake plays it.

    Its controls go through float32 as OSC sends them, so a candidate given
    the same patch plays exactly the same samples.
    """
    notes = Play((score_hz("E3"),), at_s=0.1, length_s=0.8)
    data = np.zeros((round(1.6 * RATE), 2), dtype=np.float32)
    first = round((0.5 + notes.at_s) * RATE)
    frames = round(notes.length_s * RATE)
    sent = {k: float(np.float32(v)) for k, v in {"freq": notes.notes[0], **patch}.items()}
    data[first : first + frames] += play(sent, frames, RATE)[:, None]
    audio.write_float(path, data, RATE)


def score_hz(name: str) -> float:
    from mph.bench import sc  # noqa: PLC0415

    return sc.hz(score.midi(name))


def project(tmp_path: Path, text: str = MATCH) -> Path:
    folder = tmp_path / "proj" / "matches"
    folder.mkdir(parents=True, exist_ok=True)
    made_up_horn(folder / "horn.wav")
    (folder / "horn.toml").write_text(text)
    return folder / "horn.toml"


def mph(cfg: config.Config, found: Runner, host: Host, *argv: str) -> int:
    args = cli.build_parser().parse_args(list(argv))
    return cli.dispatch(args, cfg, Context(found, host), None)


@pytest.mark.req("REQ-MAT-01")
def test_analyse_describes_the_references_span(
    tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    path = project(tmp_path)
    assert mph(cfg_for(tmp_path), runner(), mac_host, "match", "analyse", str(path)) == 0
    out = capsys.readouterr().out
    assert "a made-up horn: 0.5 to 1.5 s of it, at 22050 Hz" in out
    lines = out.splitlines()
    quarters = lines[2:6]
    assert all("E3" in line for line in quarters[1:])  # a quarter in, it's sounding
    partials = lines[lines.index("Its strongest partials where it is loudest:") + 1 :]
    assert float(partials[0].split()[0]) == pytest.approx(164.8, abs=1.0)  # its fundamental, E3
    assert "E4" in partials[1]  # and its octave


@pytest.mark.req("REQ-MAT-05")
def test_check_finds_a_hidden_patch(
    tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    path = project(tmp_path)
    assert mph(cfg_for(tmp_path), runner(), mac_host, "match", "check", str(path)) == 0
    out = capsys.readouterr().out
    assert "Known-answer check of horn: passed." in out
    assert "brass" in out and "swell" in out
    assert not (tmp_path / "data" / runs.RUNS / "proj" / "horn" / "check-work").exists()


@pytest.mark.req("REQ-MAT-05")
def test_check_renders_at_the_production_rate_when_the_reference_is_elsewhere(
    tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    path = project(tmp_path, MATCH.replace('path = "horn.wav"', 'path = "elsewhere.wav"'))
    path.with_name("horn.toml").write_text(path.read_text().replace("budget = 360", "budget = 24"))
    cfg = cfg_for(tmp_path)
    mph(cfg, runner(), mac_host, "match", "check", str(path))
    rate = cfg.audio.production_sample_rate
    assert f"isn't on this computer, so the check renders at {rate} Hz" in capsys.readouterr().out


@pytest.mark.req("REQ-MAT-06")
def test_run_leaves_the_closest_candidates_to_hear_and_what_to_pin(
    tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, path = cfg_for(tmp_path), project(tmp_path)
    assert mph(cfg, runner(), mac_host, "match", "run", str(path), "--workers", "2") == 0
    out = capsys.readouterr().out
    (folder,) = runs.folder_of(cfg, "proj", "horn").iterdir()
    names = sorted(p.name for p in folder.iterdir())
    assert names[:2] == ["baseline.wav", "candidate-1.wav"] and names[-2:] == [
        "reference.wav",
        "report.json",
    ]
    report = json.loads((folder / runs.REPORT).read_text())
    assert report["reference"]["path"] == "horn.wav" and report["reference"]["rate"] == RATE
    assert report["workers"] == 2 and report["renders"] <= 360
    assert report["found"]["knobs"]["brass"] == pytest.approx(900.0, rel=0.05)
    assert report["found"]["knobs"]["swell"] == pytest.approx(0.3, rel=0.05)
    assert report["found"]["total"] < report["baseline"]["total"]
    assert report["pin"] == report["candidates"][0]["knobs"]
    assert report["baseline"]["params"] == {}  # no score: the synth's defaults
    reference, rate = audio.read(folder / "reference.wav")
    assert rate == RATE and len(reference) == RATE  # the span, exactly
    level = np.sqrt(np.mean(audio.mono(reference) ** 2))
    candidate = audio.read(folder / "candidate-1.wav")[0]
    assert np.sqrt(np.mean(audio.mono(candidate) ** 2)) == pytest.approx(level, rel=1e-3)
    assert "closer than the baseline's" in out and "params = { brass = 9" in out
    # A second run leaves the first with its report alone.
    assert mph(cfg, runner(), mac_host, "match", "run", str(path)) == 0
    first, second = sorted(runs.folder_of(cfg, "proj", "horn").iterdir())
    assert [p.name for p in first.iterdir()] == [runs.REPORT]
    assert (second / "candidate-1.wav").is_file()


@pytest.mark.req("REQ-MAT-06")
def test_a_run_that_cant_beat_the_baseline_leaves_nothing_to_pin(
    tmp_path: Path,
    mac_host: Host,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(score, "load", lambda _: score.load_text(SCORE))
    path = project(tmp_path, MATCH.replace('why = "a test"', 'score = "BM-T"\nvoice = "horn"'))
    cfg = cfg_for(tmp_path)
    assert mph(cfg, runner(), mac_host, "match", "run", str(path)) == 1
    assert "nothing to pin" in capsys.readouterr().out
    (folder,) = runs.folder_of(cfg, "proj", "horn").iterdir()
    report = json.loads((folder / runs.REPORT).read_text())
    assert report["baseline"]["params"] == HIDDEN and report["baseline"]["total"] == 0.0
    assert report["candidates"] == [] and report["pin"] == {}
    assert not any(folder.glob("candidate-*.wav"))


@pytest.mark.req("REQ-MAT-06")
def test_the_baseline_must_play_the_matched_synth(
    tmp_path: Path,
    mac_host: Host,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = SCORE.replace('synth = "mph_horn"', 'synth = "mph_pad"')
    monkeypatch.setattr(score, "load", lambda _: score.load_text(other))
    path = project(tmp_path, MATCH.replace('why = "a test"', 'score = "BM-T"\nvoice = "horn"'))
    assert mph(cfg_for(tmp_path), runner(), mac_host, "match", "run", str(path)) == 2
    assert "plays horn on mph_pad, not mph_horn" in capsys.readouterr().err
    path.write_text(MATCH.replace('why = "a test"', 'score = "BM-T"\nvoice = "nobody"'))
    assert mph(cfg_for(tmp_path), runner(), mac_host, "match", "run", str(path)) == 2
    assert "has no voice 'nobody'" in capsys.readouterr().err


@pytest.mark.req("REQ-MAT-01")
@pytest.mark.parametrize(
    ("change", "says"),
    [
        (('path = "horn.wav"', 'path = "gone.wav"'), "gone.wav isn't there"),
        (("end_s = 1.5", "end_s = 2.5"), "is 1.60 s long, so it has no span to 2.5 s"),
        (("start_s = 0.5\nend_s = 1.5", "start_s = 0.0\nend_s = 0.5"), "silent from 0 to 0.5 s"),
        (("[knobs]", "[nothing]"), "unknown keys ['nothing']"),
    ],
)
def test_a_match_that_cant_run_says_why(
    tmp_path: Path,
    mac_host: Host,
    capsys: pytest.CaptureFixture[str],
    change: tuple[str, str],
    says: str,
) -> None:
    text = MATCH.replace(*change)
    if change[0] == "start_s = 0.5\nend_s = 1.5":
        text = text.replace("length_s = 0.8", "length_s = 0.3")
    path = project(tmp_path, text)
    assert mph(cfg_for(tmp_path), runner(), mac_host, "match", "run", str(path)) == 2
    assert says in capsys.readouterr().err


@pytest.mark.req("REQ-MAT-02")
def test_run_needs_supercollider(
    tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    path = project(tmp_path)
    assert mph(cfg_for(tmp_path), runner(tools=False), mac_host, "match", "run", str(path)) == 2
    assert "run `mph install supercollider`" in capsys.readouterr().err


@pytest.mark.req("REQ-MAT-01")
def test_a_reference_can_be_a_catalogue_sound(
    tmp_path: Path, mac_host: Host, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = cfg_for(tmp_path)
    digest = "4e" + "d" * 62
    by_hash = MATCH.replace('path = "horn.wav"', f'hash = "{digest[:12]}"')
    path = project(tmp_path, by_hash)
    assert mph(cfg, runner(), mac_host, "match", "analyse", str(path)) == 2
    assert "catalogue.sqlite is missing" in capsys.readouterr().err
    conn = db.connect(cfg.data_dir / db.FILENAME)
    with conn:
        conn.execute(
            "INSERT INTO asset (hash, bytes, container, sample_rate, channels, duration_s, "
            "origin) VALUES (?, 1, 'wav', ?, 2, 1.6, 'source')",
            (digest, RATE),
        )
    conn.close()
    assert mph(cfg, runner(), mac_host, "match", "analyse", str(path)) == 2
    assert "not on this computer; `mph catalogue refresh`" in capsys.readouterr().err
    cached = provenance.cache_path(cfg.data_dir, digest)
    cached.parent.mkdir(parents=True)
    made_up_horn(cached)
    assert mph(cfg, runner(), mac_host, "match", "analyse", str(path)) == 0
    assert f"{digest[:12]}: 0.5 to 1.5 s" in capsys.readouterr().out.replace(
        "a made-up horn", digest[:12]
    )
    path.write_text(by_hash.replace(digest[:12], "ffffff"))
    assert mph(cfg, runner(), mac_host, "match", "analyse", str(path)) == 2
    assert "matches no sounds" in capsys.readouterr().err
