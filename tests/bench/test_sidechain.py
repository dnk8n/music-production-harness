"""Sidechain ducking (REQ-BEN-12, BM-SC-01), rendered by the fake scsynth."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from mph.bench import checks, commands, kit, render, sc, score
from mph.bench.score import ScoreError, Sidechain
from tests.bench.test_render import cfg_for, renoise_ready, runner

RATE = 48000
PAD = """[voices.pad]
notes = "C3 - - - - - - - - - - - - - - -"
gate  = "x  - - - - - - - - - - - - - - ."
sound = { synth = "mph_pad" }"""
HAT = """[voices.pad]
grid = "x . . . . . . . . . . . . . . ."
sound = { synth = "mph_hat" }"""
DUCKED = f"""
id = "BM-TEST-SC"
bpm = 120
bars = 1

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = {{ synth = "mph_kick" }}

{PAD}

[sidechain]
trigger = "kick"
targets = ["pad"]

[variants.bypass]
sidechain = {{ depth_db = 0.0 }}
"""


@pytest.mark.req("REQ-BEN-12")
def test_a_sidechain_is_read_from_the_score_and_a_variant_can_bypass_it() -> None:
    loaded = score.load_text(DUCKED)
    assert loaded.sidechain == Sidechain("kick", ("pad",))  # 6 dB, 2 ms, 200 ms by default
    assert loaded.supercollider_only == "a melodic part (glides and filter envelopes)"
    drums = score.load_text(DUCKED.replace(PAD, HAT))
    assert drums.supercollider_only == "a sidechain"
    assert [v.name for v in score.all_variants(drums)] == ["supercollider", "bypass"]
    bypass, variant = score.variant_of(loaded, "bypass")
    assert bypass.sidechain is not None and not bypass.sidechain.active
    assert not variant.as_scored
    assert bypass.digest() != score.variant_of(loaded, "supercollider")[0].digest()
    # Scores without a sidechain hash as they did before sidechains existed, so
    # their renders and Dean's verdicts on them still match.
    detroit = score.load("BM-DRUM-01")
    assert score.variant_of(detroit, "renoise")[0].digest() == "b8523f323a31f25b"


@pytest.mark.req("REQ-BEN-12")
@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('trigger = "snare"\ntargets = ["pad"]', "unknown voices"),
        ('trigger = "kick"\ntargets = []', "targets other than"),
        ('trigger = "kick"\ntargets = ["kick"]', "targets other than"),
        ('trigger = "kick"\ntargets = ["pad"]\nrelease_ms = 0', "positive attack"),
        ('trigger = "kick"\ntargets = ["pad"]\ndepth_db = -3', "0 dB or more"),
    ],
)
def test_sidechain_mistakes_are_reported(table: str, message: str) -> None:
    text = DUCKED.split("[sidechain]", maxsplit=1)[0] + "[sidechain]\n" + table + "\n"
    with pytest.raises(ScoreError, match=message):
        score.load_text(text)


@pytest.mark.req("REQ-BEN-12")
def test_held_notes_last_through_their_hold_steps() -> None:
    loaded = score.load_text(DUCKED)
    pad = [e for e in score.events(loaded) if e.voice == "pad"]
    assert [e.step for e in pad] == [0]
    assert pad[0].length_s == pytest.approx(15 * loaded.step_s)
    assert not pad[0].tie
    held = PAD.replace("C3 - - - - - - - -", "- - - - - - - - C3")
    held = held.replace('"x  - - - - - - - - - - - - - - ."', '"-  - - - - - - - x - - - - - - -"')
    held_over_the_bar = DUCKED.replace(PAD, held).replace("bars = 1", "bars = 2")
    notes = [e for e in score.events(score.load_text(held_over_the_bar)) if e.voice == "pad"]
    # Held from step 8 across the bar line to step 23; the next bar's note to the end.
    assert [(e.step, round(e.length_s / loaded.step_s)) for e in notes] == [(8, 16), (24, 8)]
    with pytest.raises(ScoreError, match="not a note"):
        score.load_text(DUCKED.replace('notes = "C3 -', 'notes = "- -'))
    with pytest.raises(ScoreError, match="symbols from"):
        score.load_text(DUCKED.replace('grid = "X . . .', 'grid = "X - . .'))  # drums can't hold


@pytest.mark.req("REQ-BEN-12")
def test_every_kick_starts_a_dip_at_its_own_sample(tmp_path: Path) -> None:
    loaded = score.load_text(DUCKED)
    choices = [kit.synth_for(v, "test") for v in loaded.voices]
    plan = sc.Plan(loaded, choices, RATE, tmp_path)
    bundles = sc.build(plan, score.events(loaded))
    setup = [m for m in bundles[0].messages if m.address in ("/g_new", "/s_new")]
    assert [m.args[:4] for m in setup] == [
        (sc.VOICES_GROUP, sc.TAIL, 0),
        (sc.SIDECHAIN_GROUP, sc.AFTER, sc.VOICES_GROUP),
        ("mph_ducker", sc.AUTO_NODE, sc.TAIL, sc.SIDECHAIN_GROUP),
    ]
    ducks = [(b.time_s, m) for b in bundles for m in b.messages if m.args[:1] == ("mph_duck",)]
    kicks = [e.sample(RATE) / RATE for e in score.events(loaded) if e.voice == "kick"]
    assert [t for t, _ in ducks] == kicks
    args = dict(zip(ducks[0][1].args[4::2], ducks[0][1].args[5::2], strict=True))
    assert args["depth"] == pytest.approx(sc.reduction(6.0)) == pytest.approx(0.4988, abs=1e-4)
    assert (args["attack"], args["release"]) == (0.002, 0.2)
    bypassed = replace(plan, score=score.variant_of(loaded, "bypass")[0])
    names = {m.args[0] for b in sc.build(bypassed, score.events(loaded)) for m in b.messages}
    assert not {"mph_duck", "mph_ducker"} & names


def _gain(wet_db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A steady tone as the dry target, and it scaled by ``wet_db`` as the ducked one."""
    t = np.arange(len(wet_db)) / RATE
    dry = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)[:, None].repeat(2, axis=1)
    return (dry * (10 ** (wet_db / 20))[:, None]).astype(np.float32), dry


def _dip(depth_db: float, attack_s: float, release_s: float, length_s: float = 0.5) -> np.ndarray:
    n = round(length_s * RATE)
    taken = np.zeros(n)
    a, r = round(attack_s * RATE), round(release_s * RATE)
    depth = sc.reduction(depth_db)
    taken[:a] = depth * np.arange(1, a + 1) / a
    taken[a : a + r] = depth * (0.5 + 0.5 * np.cos(np.pi * np.arange(1, r + 1) / r))
    return 20 * np.log10(1 - taken)


@pytest.mark.req("REQ-BEN-12")
def test_the_dip_is_measured_against_the_dry_target() -> None:
    chain = Sidechain("kick", ("pad",))
    good = checks.sidechain(chain, "pad", *_gain(_dip(6, 0.002, 0.2)), [0.0], rate=RATE,
                            recover_s=0.23)  # fmt: skip
    assert [c.passed for c in good] == [True, True, True, True]
    assert good[0].value == pytest.approx(6.0, abs=0.05)
    shallow = checks.sidechain(chain, "pad", *_gain(_dip(3, 0.002, 0.2)), [0.0], rate=RATE,
                               recover_s=0.23)  # fmt: skip
    assert [c.passed for c in shallow] == [False, False, True, True]
    slow = checks.sidechain(chain, "pad", *_gain(_dip(6, 0.002, 0.4)), [0.0], rate=RATE,
                            recover_s=0.23)  # fmt: skip
    assert [c.passed for c in slow] == [True, True, False, True]
    stepped = _dip(6, 0.002, 0.2)
    stepped[round(0.1 * RATE) :] = 0.0  # snaps back half way: a click, not a curve
    jumpy = checks.sidechain(chain, "pad", *_gain(stepped), [0.0], rate=RATE, recover_s=0.23)
    assert [c.passed for c in jumpy] == [True, True, True, False]
    wet, dry = _gain(_dip(6, 0.002, 0.2))
    silent = checks.sidechain(chain, "pad", wet * 0, dry * 0, [0.0], rate=RATE, recover_s=0.23)
    assert [(c.passed, c.note) for c in silent] == [(False, "silent at every hit")]


@pytest.mark.req("REQ-BEN-12")
@pytest.mark.req("REQ-BEN-10")
def test_bm_sc_01_ducks_the_pad_and_acid_under_every_kick(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    cfg = replace(cfg, retention=replace(cfg.retention, keep_work=True))
    home = renoise_ready(tmp_path, cfg)  # Renoise is ready, but a sidechain needs SuperCollider
    outcome = render.run("BM-SC-01", cfg, runner(), None, home=home)
    measured = {m["name"]: m for m in outcome.report["measurements"]}  # type: ignore[attr-defined]
    assert outcome.report["engine"] == "supercollider"
    for target in ("pad", "acid"):
        assert measured[f"sidechain: {target} dip (dB)"]["value"] == pytest.approx(6.0, abs=0.1)
        assert measured[f"sidechain: {target} dip (dB)"]["note"].startswith("32 of 32")
    assert [m for m in measured.values() if not m["passed"]] == []
    assert (outcome.folder / "work" / "dry.wav").is_file()
    bypass = render.run("BM-SC-01", cfg, runner(), None, home=home, variant="bypass")
    names = [m["name"] for m in bypass.report["measurements"]]  # type: ignore[attr-defined]
    assert bypass.passed and not any(n.startswith("sidechain") for n in names)
    assert not (bypass.folder / "work" / "dry.wav").exists()
    with pytest.raises(sc.RenderError, match="only SuperCollider can play"):
        render.run("BM-SC-01", cfg, runner(), None, home=home, engine="renoise")
    assert commands._cannot("BM-SC-01", "supercollider", home) == ""


def test_bm_sc_01_plays_the_bm_bass_01_line() -> None:
    acid = score.load("BM-SC-01").voice("acid")
    assert acid == score.load("BM-BASS-01").voice("acid")
