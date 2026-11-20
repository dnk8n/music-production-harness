"""What a melodic voice's sampled instrument needs (spec/components/renoise.md, REQ-REN-09).

A Renoise take plays each melodic voice from samples of its SuperCollider
synth: one sample for each sound the voice makes, a pitch plain or
accented, held for the longest note of that sound, with notes tied together
counted as one, since the first note's sample bends through the rest. Every
accented pitch also gets a plain sample, so the plain instrument has every
pitch. The sampler's release and glide follow the synth's: these tables
hold the times that synthdefs.scd fixes, or the defaults of the controls a
score can set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mph.bench.score import Event

FIXED_RELEASE_S = {"mph_acid": 0.008, "mph_bass": 0.02, "mph_organ": 0.03}  # in the synth's Env
RELEASE_DEFAULT_S = {"mph_pad": 0.06, "mph_stab": 0.12, "mph_strings": 1.1}  # `release` control
GLIDE_DEFAULT_S = {"mph_acid": 0.06, "mph_bass": 0.05, "mph_organ": 0.0}  # `glide` control
OTHER_RELEASE_S = 0.1  # a synth not listed here

Sound = tuple[int, bool]  # pitch, accented


def release_s(synthdef: str, params: Mapping[str, float]) -> float:
    """How long the synth takes to fall silent after its gate closes."""
    if synthdef in FIXED_RELEASE_S:
        return FIXED_RELEASE_S[synthdef]
    return float(params.get("release", RELEASE_DEFAULT_S.get(synthdef, OTHER_RELEASE_S)))


def glide_s(synthdef: str, params: Mapping[str, float]) -> float:
    """How long the synth takes to slide to a tied note's pitch; 0 is at once."""
    return float(params.get("glide", GLIDE_DEFAULT_S.get(synthdef, 0.0)))


def sounds(own: Sequence[Event]) -> dict[Sound, float]:
    """Each sound a voice's notes make and the longest it is held, in seconds.
    A tied chain is held from its first note to the end of its last."""
    found: dict[Sound, float] = {}
    first: Event | None = None
    ordered = sorted(own, key=lambda e: e.time_s)
    for n, event in enumerate(ordered):
        first = first or event
        if event.tie and n + 1 < len(ordered):
            continue
        sound = (first.pitch, first.accent)
        found[sound] = max(found.get(sound, 0.0), event.time_s + event.length_s - first.time_s)
        first = None
    for (pitch, accent), held in list(found.items()):
        if accent:
            found[(pitch, False)] = max(found.get((pitch, False), 0.0), held)
    return found
