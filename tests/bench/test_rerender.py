"""A pass is a pass: when a take renders again, and what it keeps (REQ-BEN-14, REQ-BEN-15)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from mph.bench import kit, rerender, score

NOW = "0123456789abcdef"
RENDERED: dict[str, Any] = {"inputs": NOW, "passed": True, "score_hash": "feedfacefeedface"}
PASS = {"verdict": "pass", "notes": ""}


@pytest.mark.req("REQ-BEN-14")
@pytest.mark.parametrize(
    ("report", "verdict", "renders", "why"),
    [
        (None, None, True, "never rendered"),
        ({"passed": True}, PASS, True, "rendered before mph recorded what shapes a render"),
        ({**RENDERED, "inputs": "0" * 16}, PASS, True, "its score, engine, audio settings"),
        ({**RENDERED, "passed": False}, PASS, True, "a measurement failed"),
        (RENDERED, {"verdict": "fail", "notes": "hat"}, True, "your verdict is fail"),
        (RENDERED, {"verdict": "close", "notes": ""}, True, "your verdict is close"),
        (RENDERED, {"verdict": "pass", "notes": "one more level"}, True, "still to act on"),
        (RENDERED, None, False, "waiting for your verdict"),
        (RENDERED, PASS, False, "passed, and nothing changed"),
    ],
)
def test_a_take_renders_only_when_something_is_new(
    report: dict[str, Any] | None, verdict: dict[str, str] | None, renders: bool, why: str
) -> None:
    decision = rerender.decide(report, verdict, NOW)
    assert decision.render is renders
    assert why in decision.why
    assert rerender.decide(report, verdict, NOW, force=True).render  # --force-rerender


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
def test_only_a_passed_take_keeps_its_catalogue_sounds() -> None:
    made = {
        "score_hash": "feedfacefeedface",
        "kit": [
            {"voice": "kick", "sound": "Kick.wav", "hash": "ab" * 32, "why": "catalogue: best"},
            {"voice": "chh", "sound": "mph_hat", "hash": "", "why": "synth fallback"},
        ],
    }
    kept = rerender.kept_sounds(made, PASS)
    assert list(kept) == ["kick"]  # synth voices are made, not chosen
    assert kept["kick"] == kit.Kept("ab" * 32, "Kick.wav", "catalogue: best", "feedfacefeedface")
    assert rerender.kept_sounds(made, {"verdict": "fail", "notes": ""}) == {}
    assert rerender.kept_sounds(made, None) == {}
    assert rerender.kept_sounds(None, PASS) == {}
