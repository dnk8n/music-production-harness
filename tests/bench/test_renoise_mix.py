"""A Renoise take of an arranged, mixed track with melodic parts (spec/components/renoise.md,
REQ-REN-03, REQ-REN-09 to REQ-REN-12), rendered with the fake Renoise."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mph.bench import render, score
from mph.renoise import bridge
from tests.bench.fake_renoise import FakeRenoise
from tests.bench.test_render import cfg_for, renoise_ready, runner
from tests.renoise.test_mixer import ARRANGED


@pytest.mark.lua
@pytest.mark.req("REQ-REN-03")
@pytest.mark.req("REQ-REN-09")
@pytest.mark.req("REQ-REN-10")
@pytest.mark.req("REQ-REN-11")
def test_renoise_takes_an_arranged_mixed_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = score.load_text(ARRANGED)
    assert [v.name for v in score.all_variants(loaded)][:2] == ["renoise", "supercollider"]
    monkeypatch.setattr(render, "load", lambda _: loaded)
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    outcome = render.run("BM-ARR", cfg, runner(), None, home=home, clock=fake.clock())
    report = outcome.report
    assert report["engine"] == "renoise"
    measured = {m["name"]: m for m in report["measurements"]}  # type: ignore[attr-defined]
    assert [name for name, m in measured.items() if not m["passed"]] == []
    # The sampled bass keeps its accents and slide; its Duck dips it 12 dB at every
    # kick, measured on its stem against the same stem rendered dry.
    assert {"accent: bass louder (dB)", "slide: bass level change into a slid note (dB)"} <= set(
        measured
    )
    assert measured["sidechain: bass dip (dB)"]["value"] == pytest.approx(12.0, abs=0.5)
    assert "sidechain: whole mix 100-200 ms after the kick (dB)" in measured
    main = fake.performed[0]["songs"][0]
    renders = [(r["file"].rsplit("/", 1)[1], r.get("solo"), r.get("mode")) for r in main["renders"]]
    assert renders == [
        ("warm-up.wav", None, None),
        ("render.wav", None, None),
        ("again.wav", None, None),
        ("stem-kick.wav", 1, "stem"),
        ("stem-bass.wav", 2, "stem"),
        ("stem-stab.wav", 3, "stem"),
        ("dry-bass.wav", 2, "dry"),
    ]
    assert (main["lpb"], main["tpl"], main["headroom_db"]) == (8, 16, 0.0)
    assert set(main["modes"]) == {"mix", "stem", "dry"}
    # The synths' samples stay with the render, beside what Renoise showed of its
    # devices; the WAVs Renoise rendered are gone once read.
    files = {p.relative_to(outcome.folder).as_posix() for p in outcome.folder.rglob("*")}
    bass = {f.removeprefix("sounds/bass/") for f in files if f.startswith("sounds/bass/")}
    assert bass == {
        "29.wav",
        "31.wav",
        "36.wav",
        "41.wav",
        "41-accent.wav",
        "48.wav",
        "48-accent.wav",
    }
    assert "sounds/stab/60.wav" in files  # each chord whole, at its root
    assert not any(f.startswith("work/") and f.endswith(".wav") for f in files)
    devices = json.loads((outcome.folder / "renoise-devices.json").read_text("utf-8"))
    assert "Audio/Effects/Native/Gainer" in devices["kinds"]
    assert [song["song"] for song in devices["built"]] == ["BM-ARR"]
    assert report["files"]["renoise_devices"] == "renoise-devices.json"  # type: ignore[index]
