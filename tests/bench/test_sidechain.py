"""Sidechain ducking (REQ-BEN-12, BM-SC-01), rendered by the fake scsynth."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from mph.bench import audio, checks, commands, kit, render, sc, scmix, score
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
    assert loaded.sidechain == Sidechain("kick", ("pad",))  # 6 dB, 2 ms, no hold, 200 ms
    assert loaded.supercollider_only == "a melodic part (glides and filter envelopes)"
    drums = score.load_text(DUCKED.replace(PAD, HAT))
    assert drums.supercollider_only == "a sidechain"
    assert [v.name for v in score.all_variants(drums)] == ["supercollider", "bypass"]
    bypass, variant = score.variant_of(loaded, "bypass")
    assert bypass.sidechain is not None and not bypass.sidechain.active
    assert variant.as_scored  # it changes what happens to the voices, not what they play
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
        ('trigger = "kick"\ntargets = ["pad"]\nhold_ms = -1', "a hold of 0 ms or more"),
    ],
)
def test_sidechain_mistakes_are_reported(table: str, message: str) -> None:
    text = DUCKED.split("[sidechain]", maxsplit=1)[0] + "[sidechain]\n" + table + "\n"
    with pytest.raises(ScoreError, match=message):
        score.load_text(text)


@pytest.mark.req("REQ-BEN-07")
@pytest.mark.req("REQ-BEN-12")
def test_a_variant_can_change_the_depth_attack_hold_and_release() -> None:
    text = DUCKED + "[variants.deep]\nsidechain = { depth_db = 24, release_ms = 430 }\n"
    text += "[variants.held]\nsidechain = { depth_db = 48, hold_ms = 150, release_ms = 290 }\n"
    loaded = score.load_text(text)
    deep, variant = score.variant_of(loaded, "deep")
    assert deep.sidechain == Sidechain("kick", ("pad",), 24.0, 2.0, 430.0)
    assert variant.as_scored
    held = score.variant_of(loaded, "held")[0].sidechain
    assert held == Sidechain("kick", ("pad",), 48.0, 2.0, 290.0, hold_ms=150.0)
    assert loaded.sidechain == Sidechain("kick", ("pad",))  # the score's own is unchanged
    digests = {score.variant_of(loaded, v.name)[0].digest() for v in score.all_variants(loaded)}
    assert len(digests) == 4  # supercollider, bypass, deep and held each render on their own
    for change, message in [
        ("{ ratio = 4 }", "it can change depth_db, attack_ms, hold_ms, release_ms"),
        ("{ release_ms = -1 }", "variant deep's sidechain needs .* positive attack and release"),
        ("{ hold_ms = -1 }", "variant deep's sidechain needs .* a hold of 0 ms or more"),
    ]:
        with pytest.raises(ScoreError, match=message):
            score.load_text(DUCKED + f"[variants.deep]\nsidechain = {change}\n")
    no_sidechain = DUCKED.split("[sidechain]", maxsplit=1)[0]
    with pytest.raises(ScoreError, match="changes a sidechain the score doesn't have"):
        score.load_text(no_sidechain + "[variants.deep]\nsidechain = { depth_db = 3 }\n")


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
        (scmix.VOICES_GROUP, scmix.TAIL, 0),
        (scmix.SIDECHAIN_GROUP, scmix.AFTER, scmix.VOICES_GROUP),
        ("mph_ducker", scmix.AUTO_NODE, scmix.TAIL, scmix.SIDECHAIN_GROUP),
    ]
    ducks = [(b.time_s, m) for b in bundles for m in b.messages if m.args[:1] == ("mph_duck",)]
    kicks = [e.sample(RATE) / RATE for e in score.events(loaded) if e.voice == "kick"]
    assert [t for t, _ in ducks] == kicks
    args = dict(zip(ducks[0][1].args[4::2], ducks[0][1].args[5::2], strict=True))
    assert args["depth"] == pytest.approx(sc.reduction(6.0)) == pytest.approx(0.4988, abs=1e-4)
    assert (args["attack"], args["hold"], args["release"]) == (0.002, 0.0, 0.2)
    held = replace(loaded, sidechain=Sidechain("kick", ("pad",), hold_ms=150.0))
    bundles = sc.build(replace(plan, score=held), score.events(held))
    duck = next(m for b in bundles for m in b.messages if m.args[:1] == ("mph_duck",))
    assert dict(zip(duck.args[4::2], duck.args[5::2], strict=True))["hold"] == 0.15
    bypassed = replace(plan, score=score.variant_of(loaded, "bypass")[0])
    names = {m.args[0] for b in sc.build(bypassed, score.events(loaded)) for m in b.messages}
    assert not {"mph_duck", "mph_ducker"} & names


def _gain(wet_db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A steady tone as the dry target, and it scaled by ``wet_db`` as the ducked one."""
    t = np.arange(len(wet_db)) / RATE
    dry = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)[:, None].repeat(2, axis=1)
    return (dry * (10 ** (wet_db / 20))[:, None]).astype(np.float32), dry


def _dip(
    depth_db: float, attack_s: float, release_s: float, length_s: float = 0.5, hold_s: float = 0.0
) -> np.ndarray:
    n = round(length_s * RATE)
    taken = np.zeros(n)
    a, h, r = round(attack_s * RATE), round(hold_s * RATE), round(release_s * RATE)
    depth = sc.reduction(depth_db)
    taken[:a] = depth * np.arange(1, a + 1) / a
    taken[a : a + h] = depth
    taken[a + h : a + h + r] = depth * (0.5 + 0.5 * np.cos(np.pi * np.arange(1, r + 1) / r))
    return 20 * np.log10(1 - taken)


@pytest.mark.req("REQ-BEN-12")
def test_the_dip_is_measured_against_the_dry_target() -> None:
    chain = Sidechain("kick", ("pad",))
    good = checks.sidechain(chain, "pad", *_gain(_dip(6, 0.002, 0.2)), [0.0], rate=RATE)
    assert [c.passed for c in good] == [True, True, True, True]
    assert good[0].value == pytest.approx(6.0, abs=0.05)
    assert good[2].name == "sidechain: pad level 10 ms after the release (dB)"
    shallow = checks.sidechain(chain, "pad", *_gain(_dip(3, 0.002, 0.2)), [0.0], rate=RATE)
    assert [c.passed for c in shallow] == [False, False, True, True]
    slow = checks.sidechain(chain, "pad", *_gain(_dip(6, 0.002, 0.4)), [0.0], rate=RATE)
    assert [c.passed for c in slow] == [
        True,
        True,
        False,
        True,
    ]  # still down when it should be back
    declared = replace(chain, release_ms=400.0)  # the same curve, when the score asks for it
    assert all(c.passed for c in checks.sidechain(declared, "pad", *_gain(_dip(6, 0.002, 0.4)),
                                                  [0.0], rate=RATE))  # fmt: skip
    held = _gain(_dip(36, 0.002, 0.29, hold_s=0.15))  # BM-SC-01's over-the-top take
    over = replace(chain, depth_db=36.0, hold_ms=150.0, release_ms=290.0)
    assert all(c.passed for c in checks.sidechain(over, "pad", *held, [0.0], rate=RATE))
    unheld = replace(over, hold_ms=0.0)  # the same dip, but the score says it doesn't hold
    late = checks.sidechain(unheld, "pad", *held, [0.0], rate=RATE)
    assert [c.passed for c in late] == [True, True, False, True]
    stepped = _dip(6, 0.002, 0.2)
    stepped[round(0.1 * RATE) :] = 0.0  # snaps back half way: a click, not a curve
    jumpy = checks.sidechain(chain, "pad", *_gain(stepped), [0.0], rate=RATE)
    assert [c.passed for c in jumpy] == [True, True, True, False]
    wet, dry = _gain(_dip(6, 0.002, 0.2))
    silent = checks.sidechain(chain, "pad", wet * 0, dry * 0, [0.0], rate=RATE)
    assert [(c.passed, c.note) for c in silent] == [(False, "silent at every hit")]


@pytest.mark.req("REQ-BEN-12")
def test_the_whole_mix_must_change_enough_to_hear() -> None:
    hits = [0.0, 0.5, 1.0]
    audible = checks.sidechain_mix([-0.4, -0.2, -0.3], [-2.5, -3.0, -2.0], len(hits))
    assert [(c.passed, c.value) for c in audible] == [(True, -2.5)]
    assert audible[0].name == "sidechain: whole mix 100-200 ms after the kick (dB)"
    assert audible[0].note.endswith("under the kick (5-35 ms): -0.3 dB")
    masked = checks.sidechain_mix([-0.1] * 3, [-0.5, -0.6, -0.4], len(hits))
    assert [(c.passed, c.value) for c in masked] == [(False, -0.5)]  # what Dean couldn't hear
    silent = checks.sidechain_mix([], [], len(hits))
    assert [(c.passed, c.note) for c in silent] == [(False, "silent after every hit")]


@pytest.mark.req("REQ-BEN-12")
def test_mix_levels_are_compared_in_windows_after_each_hit_weighted_as_loudness() -> None:
    t = np.arange(RATE) / RATE
    dry = (0.3 * np.sin(2 * np.pi * 220 * t))[:, None].repeat(2, axis=1)
    wet = dry.copy()
    wet[round(0.1 * RATE) : round(0.2 * RATE)] *= 0.5  # 6 dB down after the first hit only
    found = audio.window_changes(wet, dry, [0.0, 0.5, 0.99], rate=RATE, start_s=0.1, end_s=0.2)
    assert len(found) == 2  # the last hit's window runs off the end
    assert found[0] == pytest.approx(-6.02, abs=0.01)
    assert found[1] == pytest.approx(0.0, abs=1e-9)
    quiet = dry * 1e-4  # below -60 dBFS: nothing to compare
    assert audio.window_changes(quiet, quiet, [0.0], rate=RATE, start_s=0.1, end_s=0.2) == []
    rumble = (0.3 * np.sin(2 * np.pi * 20 * t))[:, None].repeat(2, axis=1)
    weighted = [audio.rms(audio.k_weighted(x, RATE)[RATE // 2 :]) for x in (dry, rumble)]
    assert audio.db(weighted[1] / weighted[0]) < -10  # sub-bass counts for less, as in loudness


@pytest.mark.req("REQ-BEN-12")
@pytest.mark.req("REQ-BEN-10")
def test_bm_sc_01_ducks_the_pad_and_acid_under_every_kick(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    cfg = replace(cfg, retention=replace(cfg.retention, keep_work=True))
    home = renoise_ready(tmp_path, cfg)  # Renoise is ready, but a sidechain needs SuperCollider
    changes = {}
    for take, depth in [("supercollider", 12.0), ("exaggerated", 24.0), ("over-the-top", 36.0)]:
        outcome = render.run("BM-SC-01", cfg, runner(), None, home=home, variant=take)
        assert outcome.report["engine"] == "supercollider"
        measured = {m["name"]: m for m in outcome.report["measurements"]}  # type: ignore[attr-defined]
        for target in ("pad", "acid"):
            dip = measured[f"sidechain: {target} dip (dB)"]
            assert dip["value"] == pytest.approx(depth, abs=0.1)
            assert dip["note"].startswith("32 of 32")
        mix = measured["sidechain: whole mix 100-200 ms after the kick (dB)"]
        assert "median of 32 of 32 hits" in mix["note"]
        changes[take] = mix["value"]
        assert [m for m in measured.values() if not m["passed"]] == []
        assert (outcome.folder / "work" / "dry.wav").is_file()
    assert changes["exaggerated"] < changes["supercollider"] <= -checks.MIX_AUDIBLE_DB
    assert changes["over-the-top"] < changes["exaggerated"]  # past exaggerated, for contrast
    bypass = render.run("BM-SC-01", cfg, runner(), None, home=home, variant="bypass")
    names = [m["name"] for m in bypass.report["measurements"]]  # type: ignore[attr-defined]
    assert bypass.passed and not any(n.startswith("sidechain") for n in names)
    assert "accent: acid brighter (centroid ratio)" in names  # its acid plays as scored
    assert not (bypass.folder / "work" / "dry.wav").exists()
    with pytest.raises(sc.RenderError, match="only SuperCollider can play"):
        render.run("BM-SC-01", cfg, runner(), None, home=home, engine="renoise")
    assert commands._cannot("BM-SC-01", "supercollider", home) == ""


def test_bm_sc_01_plays_the_bm_bass_01_line() -> None:
    acid = score.load("BM-SC-01").voice("acid")
    assert acid == score.load("BM-BASS-01").voice("acid")


@pytest.mark.req("REQ-BEN-12")
def test_a_dip_is_timed_from_the_targets_first_sound_when_it_starts_late() -> None:
    # A pad fading in on the kick: nothing to measure for 8 ms, then the dip
    # is already at its depth.
    chain = Sidechain("kick", ("pad",))
    wet, dry = _gain(_dip(6, 0.002, 0.2))
    silent = round(0.008 * RATE)
    wet[:silent], dry[:silent] = 0.0, 0.0
    found = checks.sidechain(chain, "pad", wet, dry, [0.0], rate=RATE)
    assert all(c.passed for c in found)
    assert found[1].value == 0.5  # reached in its first heard window
    # A dip that comes late after the pad starts still fails.
    slow_wet, slow_dry = _gain(_dip(6, 0.02, 0.2))
    slow_wet[:silent], slow_dry[:silent] = 0.0, 0.0
    late = checks.sidechain(chain, "pad", slow_wet, slow_dry, [0.0], rate=RATE)
    assert [c.passed for c in late] == [True, False, True, True]


@pytest.mark.req("REQ-BEN-12")
def test_a_dip_cut_short_by_the_next_hit_is_followed_up_to_it() -> None:
    # A kick pushed onto the last eighth: its dip is still on the way back
    # when the downbeat's dip starts, 250 ms later.
    chain = Sidechain("kick", ("pad",), release_ms=400.0)
    depth = sc.reduction(6)
    first = 1 - 10 ** (_dip(6, 0.002, 0.4, length_s=0.75) / 20)
    second = np.zeros_like(first)
    second[round(0.25 * RATE) :] = 1 - 10 ** (_dip(6, 0.002, 0.4) / 20)
    wet, dry = _gain(20 * np.log10(1 - np.minimum(first + second, depth)))
    found = checks.sidechain(chain, "pad", wet, dry, [0.0, 0.25], rate=RATE)
    assert all(c.passed for c in found)
    assert found[2].note == "1 of 2 dips ran their course before the next hit"


@pytest.mark.req("REQ-BEN-12")
def test_the_whole_mix_is_measured_after_hits_where_a_target_sounds() -> None:
    rate = RATE
    t = np.arange(rate) / rate
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)[:, None].repeat(2, axis=1)
    target = tone.copy()
    target[: rate // 2] = 0.0  # the target only comes in half way
    ducked = target * np.float32(10 ** (-6 / 20))
    hits = [0.0, 0.5]
    every = audio.window_changes(
        tone + ducked, tone + target, hits, rate=rate, start_s=0.1, end_s=0.2
    )
    assert every == pytest.approx([0.0, -2.5], abs=0.05)
    heard = audio.window_changes(
        tone + ducked, tone + target, hits, rate=rate, start_s=0.1, end_s=0.2, heard=target
    )
    assert heard == pytest.approx([-2.5], abs=0.05)


@pytest.mark.req("REQ-BEN-12")
def test_recovery_is_measured_as_soon_as_the_target_sounds_again() -> None:
    # A bass line between notes when the release ends: the level waits for
    # the next note, 5 ms later, instead of going unmeasured.
    chain = Sidechain("kick", ("bass",))
    wet, dry = _gain(_dip(6, 0.002, 0.2))
    gap = slice(round(0.205 * RATE), round(0.215 * RATE))
    wet[gap], dry[gap] = 0.0, 0.0
    found = checks.sidechain(chain, "bass", wet, dry, [0.0], rate=RATE)
    assert found[2].passed and found[2].note == "1 of 1 dips ran their course before the next hit"
    # Still down when it sounds again: a fail.
    slow_wet, slow_dry = _gain(_dip(6, 0.002, 0.4))
    slow_wet[gap], slow_dry[gap] = 0.0, 0.0
    assert not checks.sidechain(chain, "bass", slow_wet, slow_dry, [0.0], rate=RATE)[2].passed
