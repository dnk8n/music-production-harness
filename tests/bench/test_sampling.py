"""What a melodic voice's sampled instrument needs (spec/components/renoise.md, REQ-REN-09)."""

from __future__ import annotations

import re
from importlib import resources

import pytest

from mph.bench import sampling
from mph.bench.score import Event

SYNTHDEFS = resources.files("mph.bench").joinpath("synthdefs.scd").read_text("utf-8")


def note(
    at: float,
    pitch: int,
    length: float = 0.1,
    *,
    accent: bool = False,
    tie: bool = False,
    syllable: str = "",
) -> Event:
    return Event(
        "bass", 0, at, 1.0, pitch=pitch, accent=accent, tie=tie, length_s=length, syllable=syllable
    )


@pytest.mark.req("REQ-REN-09")
def test_each_sound_is_held_for_its_longest_note() -> None:
    found = sampling.sounds(
        [
            note(0.0, 36),
            note(0.5, 36, 0.3),  # longer: the C2 sample holds 0.3 s
            note(1.0, 43, accent=True),
            note(1.5, 41, 0.25, tie=True),  # tied into the next two: one sound,
            note(1.75, 43, 0.25, tie=True),  # held from the F2's start to
            note(2.0, 48, 0.2),  # the C3's end
            note(3.0, 48),
        ]
    )
    assert found == {
        (36, False, ""): pytest.approx(0.3),
        (43, True, ""): pytest.approx(0.1),
        (43, False, ""): pytest.approx(0.1),  # every accented pitch has a plain twin
        (41, False, ""): pytest.approx(0.7),
        (48, False, ""): pytest.approx(0.1),  # the tied-into C3 isn't a sound of its own
    }
    # A tie on the last note has nothing to glide into: it's held as it is.
    assert sampling.sounds([note(0.0, 36, 0.4, tie=True)]) == {(36, False, ""): pytest.approx(0.4)}


@pytest.mark.req("REQ-REN-09")
@pytest.mark.req("REQ-BEN-19")
def test_each_syllable_is_a_sound_of_its_own() -> None:
    found = sampling.sounds(
        [
            note(0.0, 60, 0.2, syllable="o"),
            note(0.5, 60, 0.4, syllable="ye"),  # the same pitch, sung on another syllable
            note(1.0, 60, 0.1, accent=True, syllable="o"),
            note(1.5, 62, 0.25, tie=True, syllable="la"),  # glides into E4, still on la
            note(1.75, 64, 0.25, syllable="la"),
        ]
    )
    assert found == {
        (60, False, "o"): pytest.approx(0.2),
        (60, False, "ye"): pytest.approx(0.4),
        (60, True, "o"): pytest.approx(0.1),
        (62, False, "la"): pytest.approx(0.5),
    }


def _synthdef(name: str) -> tuple[dict[str, float], str]:
    """A synth's control defaults, and the release of the envelope its level follows."""
    start = SYNTHDEFS.index(f"SynthDef(\\{name},")
    body = SYNTHDEFS[start : SYNTHDEFS.index("SynthDef(", start + 1)]
    controls = body[body.index("|") + 1 : body.index("|", body.index("|") + 1)]
    defaults = {k: float(v) for k, v in re.findall(r"(\w+)\s*=\s*(-?[\d.]+)", controls)}
    level = re.search(r"\blevel = EnvGen\.ar\(Env\.(asr|adsr)\(([^)]*)\)", body)
    assert level is not None, name
    args = [a.strip() for a in level.group(2).split(",")]
    return defaults, args[2] if level.group(1) == "asr" else args[3]


@pytest.mark.req("REQ-REN-09")
def test_release_and_glide_tables_match_the_synthdefs() -> None:
    melodic = re.findall(r"SynthDef\(\\(\w+), \{ \|[^|]*\bacc = 0", SYNTHDEFS)
    assert sorted(melodic) == sorted({*sampling.FIXED_RELEASE_S, *sampling.RELEASE_DEFAULT_S})
    for name in melodic:
        defaults, release = _synthdef(name)
        if name in sampling.FIXED_RELEASE_S:
            assert float(release) == sampling.FIXED_RELEASE_S[name], name
        else:
            assert release == "release", name
            assert defaults["release"] == sampling.RELEASE_DEFAULT_S[name], name
        assert defaults.get("glide", 0.0) == sampling.GLIDE_DEFAULT_S.get(name, 0.0), name
    # A score's own settings win over the defaults; a fixed release can't be set.
    assert sampling.release_s("mph_stab", {"release": 0.5}) == 0.5
    assert sampling.release_s("mph_bass", {"release": 0.5}) == 0.02
    assert sampling.release_s("mph_bell", {}) == sampling.OTHER_RELEASE_S
    assert (sampling.glide_s("mph_acid", {}), sampling.glide_s("mph_acid", {"glide": 0.2})) == (
        0.06,
        0.2,
    )
    assert sampling.glide_s("mph_stab", {}) == 0.0
