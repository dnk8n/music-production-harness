"""When a take renders again, and what it keeps (REQ-BEN-14, REQ-BEN-15, REQ-FBK-06)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mph.bench import kit, rerender, score
from mph.bench.ratings import RESOLVED, UNRESOLVED, Rating

NOW = "0123456789abcdef"
RENDERED: dict[str, Any] = {"inputs": NOW, "passed": True, "score_hash": "feedfacefeedface"}
AT = "2026-11-13T09:00:00Z"


def rated(stars: int, status: str | None = None, notes: str = "") -> Rating:
    return Rating(1, "BM-DRUM-01", "feedfacefeedface", "take", stars, status, notes, AT)


FOUR = rated(4)  # a pass: resolved by its stars
TWO = rated(2)  # a fail: unresolved by its stars


@pytest.mark.req("REQ-BEN-14")
@pytest.mark.req("REQ-FBK-06")
@pytest.mark.parametrize(
    ("report", "rating", "renders", "why"),
    [
        (None, None, True, "never rendered"),
        (None, FOUR, True, "never rendered"),
        ({"passed": True}, TWO, True, "rendered before mph recorded what shapes a render"),
        ({"passed": True}, FOUR, False, "resolved (4 stars)"),
        ({**RENDERED, "inputs": "0" * 16}, TWO, True, "its score, engine, audio settings"),
        ({**RENDERED, "inputs": "0" * 16}, None, True, "its score, engine, audio settings"),
        # Resolved stays put whatever changed: only --force-rerender renders it.
        ({**RENDERED, "inputs": "0" * 16}, FOUR, False, "only --force-rerender renders it"),
        ({**RENDERED, "inputs": "0" * 16}, rated(0, RESOLVED), False, "resolved (0 stars)"),
        ({**RENDERED, "inputs": "0" * 16}, rated(1, RESOLVED), False, "resolved (1 star)"),
        ({**RENDERED, "inputs": "0" * 16}, rated(5, UNRESOLVED), True, "render code changed"),
        # Nothing changed: the render would come out the same, so it waits.
        (RENDERED, TWO, False, "unresolved (2 stars), waiting for a change"),
        (RENDERED, rated(0, UNRESOLVED, "kick"), False, "unresolved (0 stars), waiting"),
        (RENDERED, rated(5, UNRESOLVED), False, "unresolved (5 stars), waiting for a change"),
        ({**RENDERED, "passed": False}, TWO, False, "waiting for a change"),
        (RENDERED, None, False, "not rated yet, and nothing changed"),
        (RENDERED, FOUR, False, "resolved (4 stars)"),
    ],
)
def test_a_take_renders_only_when_it_could_come_out_different(
    report: dict[str, Any] | None, rating: Rating | None, renders: bool, why: str
) -> None:
    decision = rerender.decide(report, rating, NOW)
    assert decision.render is renders
    assert why in decision.why
    assert rerender.decide(report, rating, NOW, force=True).render  # --force-rerender


@pytest.mark.req("REQ-BEN-14")
@pytest.mark.req("REQ-BEN-15")
def test_a_take_that_chooses_afresh_renders_when_its_sounds_would_change() -> None:
    asked: list[str] = []

    def sounds() -> str:
        asked.append("asked")
        return "it would choose other sounds now (kick)"

    assert rerender.decide(RENDERED, TWO, NOW, sounds=sounds).render  # a fail chooses afresh
    assert rerender.decide(RENDERED, None, NOW, sounds=sounds).render  # so does an unrated take
    kept = rerender.decide(RENDERED, rated(4, UNRESOLVED), NOW, sounds=sounds)
    assert not kept.render  # a pass keeps its sounds, so they can't change
    assert not rerender.decide(RENDERED, FOUR, NOW, sounds=sounds).render  # resolved
    assert rerender.decide({**RENDERED, "inputs": "x"}, TWO, NOW, sounds=sounds).render
    assert len(asked) == 2  # only asked when the answer matters: choosing searches
    assert not rerender.decide(RENDERED, TWO, NOW, sounds=lambda: "").render


@pytest.mark.req("REQ-BEN-14")
def test_changed_sounds_compares_what_a_take_would_choose_now() -> None:
    made = {
        "kit": [
            {"voice": "kick", "sound": "Pack/Kick.wav", "hash": "ab" * 32},
            {"voice": "chh", "sound": "mph_hat", "hash": ""},
        ]
    }
    same = [
        kit.Choice("kick", "mph_play1", "best", path="/moved/Kick.wav", digest="ab" * 32),
        kit.Choice("chh", "mph_hat", "synth fallback", label="mph_hat"),
    ]
    assert rerender.changed_sounds(made, same) == ""  # the same sound, wherever it lives now
    other = [replace(same[0], digest="cd" * 32), replace(same[1], label="mph_hat2")]
    assert rerender.changed_sounds(made, other) == "it would choose other sounds now (kick, chh)"
    assert rerender.changed_sounds({}, same) == "it would choose other sounds now (kick, chh)"


@pytest.mark.req("REQ-BEN-14")
def test_tidying_the_code_is_not_a_change() -> None:
    before = 'def f(x):\n    """Twice x."""\n    return x * 2  # double\n'
    tidied = "def f(x):\n\n    return (x *\n            2)\n"
    changed = "def f(x):\n    return x * 3\n"
    assert rerender.shape("a.py", before) == rerender.shape("a.py", tidied)
    assert rerender.shape("a.py", before) != rerender.shape("a.py", changed)
    assert rerender.shape("a.scd", "x; // y") == "x; // y"  # other languages: as written
    assert len(rerender.code_digest()) == 16


@pytest.mark.req("REQ-BEN-14")
def test_inputs_follow_the_score_engine_and_audio_settings() -> None:
    loaded = score.load("BM-DRUM-01")
    audio = {"production_sample_rate": 96000}
    base = rerender.inputs(loaded, "renoise", audio)
    assert base == rerender.inputs(loaded, "renoise", audio)
    assert base != rerender.inputs(replace(loaded, bpm=131.0), "renoise", audio)
    assert base != rerender.inputs(loaded, "supercollider", audio)
    assert base != rerender.inputs(loaded, "renoise", {"production_sample_rate": 48000})


@pytest.mark.req("REQ-BEN-15")
def test_only_a_take_rated_a_pass_keeps_its_catalogue_sounds() -> None:
    made = {
        "score_hash": "feedfacefeedface",
        "kit": [
            {"voice": "kick", "sound": "Kick.wav", "hash": "ab" * 32, "why": "catalogue: best"},
            {"voice": "chh", "sound": "mph_hat", "hash": "", "why": "synth fallback"},
        ],
    }
    kept = rerender.kept_sounds(made, FOUR)
    assert list(kept) == ["kick"]  # synth voices are made, not chosen
    assert kept["kick"] == kit.Kept("ab" * 32, "Kick.wav", "catalogue: best", "feedfacefeedface")
    assert list(rerender.kept_sounds(made, rated(3, UNRESOLVED))) == ["kick"]  # still a pass
    assert rerender.kept_sounds(made, TWO) == {}
    assert rerender.kept_sounds(made, rated(0, RESOLVED)) == {}  # resolved, but not a pass
    assert rerender.kept_sounds(made, None) == {}
    assert rerender.kept_sounds(None, FOUR) == {}
