"""Triplet grids and feel through the checks and both engines (REQ-BEN-13).

The North star benchmarks (BM-AFRO-01, BM-AFRO-02) render here with the fake
SuperCollider and the fake Renoise, so CI covers the feel check, Renoise's
12 lines per beat and the West African synth voices without real apps.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mph.bench import audio, checks, kit, render, sc, score
from mph.bench.audio import Match
from mph.catalogue import db
from mph.catalogue.names import priors_for
from mph.renoise import bridge
from tests.bench.fake_renoise import FakeRenoise
from tests.bench.test_audio import RATE, blip, place
from tests.bench.test_render import cfg_for, renoise_ready, runner

FEEL = """
id = "BM-F"
bpm = 120
bars = 1
feel = [41, 31, 28]

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = { role = "kick" }

[voices.shaker]
grid = "X x x X x x X x x X x x"
sound = { role = "shaker" }
humanise = 0.08
"""


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "data" / db.FILENAME)
    yield connection
    connection.close()


def shaker_matches(loaded: score.Score) -> dict[str, list[Match]]:
    """The shaker's hits found in a stem played exactly as scored."""
    hits = [e for e in score.events(loaded) if e.voice == "shaker"]
    stem = place([(e.time_s, e.velocity) for e in hits])
    return {"kick": [], "shaker": audio.match_hits(stem, blip(), hits, RATE)}


@pytest.mark.req("REQ-BEN-13")
def test_the_feel_check_measures_where_each_pulse_lands() -> None:
    loaded = score.load_text(FEEL)
    found = checks.feel(loaded, shaker_matches(loaded))
    assert [(c.name, c.value, c.passed) for c in found] == [
        ("feel: shaker worst pulse error (share of a beat)", 0.0, True)
    ]
    assert "pulse 2 at 0.410 of the beat, scored 0.410" in found[0].note
    # Played in even thirds, a 41:31:28 score misses by 0.077 of a beat.
    thirds = score.load_text(FEEL.replace("feel = [41, 31, 28]", "feel = [1, 1, 1]"))
    wrong = checks.feel(loaded, shaker_matches(thirds))
    assert wrong[0].value == pytest.approx(0.0767, abs=1e-3) and not wrong[0].passed
    # Triplets aren't swung: a triplet voice gets a feel check instead of a swing one.
    assert checks.swing(loaded, shaker_matches(loaded)) == []


@pytest.mark.req("REQ-BEN-13")
def test_a_voice_only_on_the_beat_has_no_feel_to_check() -> None:
    loaded = score.load_text(FEEL.replace('"X x x X x x X x x X x x"', '"X . . X . . X . . X . ."'))
    assert checks.feel(loaded, shaker_matches(loaded)) == []


def test_humanising_is_judged_against_each_hits_own_accent() -> None:
    loaded = score.load_text(FEEL)
    found = checks.humanise(loaded, shaker_matches(loaded))
    # Accents (X) against plain hits (x) differ by 20%, more than the +/-8%
    # humanising allows, but only the humanising counts.
    assert found[0].passed and 0 < found[0].value <= 0.16


@pytest.mark.req("REQ-BEN-05")
@pytest.mark.req("REQ-BEN-13")
def test_west_african_voices_fall_back_to_synths(conn: sqlite3.Connection) -> None:
    loaded = score.load("BM-AFRO-02")
    choices = {c.voice: c for c in kit.choose(loaded, conn, Path("/nowhere"))}
    assert {name: choices[name].synthdef for name in ("bell", "tone", "slap", "dunun")} == {
        "bell": "mph_bell",
        "tone": "mph_djembe",
        "slap": "mph_djembe",
        "dunun": "mph_dunun",
    }


@pytest.mark.req("REQ-BEN-13")
def test_a_synth_drum_takes_its_voices_params() -> None:
    loaded = score.load("BM-AFRO-02")
    choices = [kit.synth_for(v, "no catalogue") for v in loaded.voices]
    plan = sc.Plan(loaded, choices, 48000, Path("/defs"))
    slap = next(n for n, v in enumerate(loaded.voices) if v.name == "slap")
    found = [e for e in score.events(loaded) if e.voice == "slap"]
    bundles = sc.build(plan, found)
    started = [m for b in bundles for m in b.messages if m.address == "/s_new"]
    assert started and all(m.args[0] == "mph_djembe" for m in started)
    args = dict(zip(started[0].args[4::2], started[0].args[5::2], strict=True))
    assert args["stroke"] == 2.0 and args["out"] == 2 * slap


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("Agogo High 01.wav", "bell"),
        ("Gankogui_open.wav", "bell"),
        ("Jembe Slap 3.wav", "djembe"),
        ("Dununba stick.wav", "dunun"),
        ("Sangban open.wav", "dunun"),
        ("Shekere roll.wav", "shaker"),
        ("Caxixi 120.wav", "shaker"),
    ],
)
def test_west_african_instrument_names_give_roles(name: str, role: str) -> None:
    assert {p.value for p in priors_for(f"Freesound/{name}") if p.name == "role"} == {role}


def test_a_yoruba_dundun_is_not_a_bass_drum() -> None:
    # In Yoruba, dundun is the talking drum (BM-AFRO-04), not the Mande dunun.
    assert not [p for p in priors_for("Freesound/dundun phrase.wav") if p.name == "role"]


@pytest.mark.req("REQ-BEN-13")
@pytest.mark.req("REQ-BEN-01")
@pytest.mark.parametrize(
    ("benchmark_id", "take"),
    [("BM-AFRO-01", "supercollider"), ("BM-AFRO-01", "flattened"), ("BM-AFRO-02", "even")],
)
def test_north_star_takes_render_in_supercollider(
    tmp_path: Path, benchmark_id: str, take: str
) -> None:
    outcome = render.run(
        benchmark_id, cfg_for(tmp_path), runner(), None, home=tmp_path, variant=take
    )
    measured = outcome.report["measurements"]
    assert [m for m in measured if not m["passed"]] == []  # type: ignore[attr-defined]
    names = {m["name"] for m in measured}  # type: ignore[attr-defined]
    if take == "flattened":  # on sixteenths now: swing checks, no feel ones
        assert "swing: bell" in names and not any(n.startswith("feel:") for n in names)
    else:
        assert "feel: bell worst pulse error (share of a beat)" in names
        assert "swing: bell" not in names


@pytest.mark.lua
@pytest.mark.req("REQ-BEN-13")
@pytest.mark.req("REQ-BEN-10")
def test_the_jembe_feel_renders_in_renoise(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    home = renoise_ready(tmp_path, cfg)
    fake = FakeRenoise(bridge.jobs_dir(cfg))
    outcome = render.run("BM-AFRO-02", cfg, runner(), None, home=home, clock=fake.clock())
    report = outcome.report
    assert report["engine"] == "renoise"
    assert [m for m in report["measurements"] if not m["passed"]] == []  # type: ignore[attr-defined]
    main, _ = fake.performed[0]["songs"]
    assert (main["lpb"], main["patterns"][0]) == (12, {"lines": 192})
    tone = next(t for t in main["tracks"] if t["name"] == "tone")
    # The tone's first strokes: beat 1's second and third pulses, 41% and 72% in.
    assert [(n["line"], n["delay"]) for n in tone["notes"][:2]] == [(4, 236), (8, 164)]
    kick = next(t for t in main["tracks"] if t["name"] == "kick")
    assert all(n["delay"] == 0 and n["line"] % 12 == 0 for n in kick["notes"])
