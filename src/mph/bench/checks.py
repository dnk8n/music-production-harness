"""The automated half of a benchmark verdict (REQ-BEN-01, REQ-BEN-03).

Each check is a named number with a limit and a pass flag, written to
``report.json`` so the feedback page and reviewers read results, not code.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace

from mph.bench import audio
from mph.bench.audio import Audio, Match
from mph.bench.score import Event, Score, Sidechain, events, feel_shares

TIMING_LIMIT_MS = 1.0  # REQ-BEN-01
VELOCITY_LIMIT = 0.1
SWING_LIMIT = 0.005
FEEL_LIMIT = 0.005  # of a beat (REQ-BEN-13)
CHOKE_LIMIT_DB = -60.0
CHOKE_SETTLE_S = 0.008  # a choke fades: 5 ms in SuperCollider, about 7 ms in Renoise
EDGE_LIMIT = 3.0  # a hard cut is many times bigger than the jumps around it
LEAD_IN_LIMIT_MS = 10.0  # REQ-AUD-06
HUMANISE_SLACK = 0.02
ACCENT_LOUDER_DB = 2.0
SLIDE_DIP_DB = -3.0
SLIDE_CENTS = 10.0
ALIASING_LIMIT_DB = -60.0
DUCK_WINDOW_S = 0.0005  # REQ-BEN-12: the gain is measured in windows this long
DUCK_DEPTH_SLACK_DB = 1.0
DUCK_REACH_MS = 5.0
DUCK_RECOVERED_DB = -0.5
DUCK_RECOVER_AFTER_S = 0.01  # back to full level this long after the release ends
DUCK_STEP_DB = 0.5
DUCK_DIP_SEARCH_S = 0.02  # after the attack, where the deepest point can be
MIX_UNDER_KICK_S = (0.005, 0.035)  # REQ-BEN-12: the whole mix, under the kick
MIX_AFTER_KICK_S = (0.1, 0.2)  # and after it, where the pump is heard
MIX_AUDIBLE_DB = 2.0  # quieter than bypass after the kick: twice a just-noticeable change


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    value: float
    limit: str
    passed: bool
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        plain = asdict(self)  # numpy scalars become plain JSON types
        return {**plain, "value": float(self.value), "passed": bool(self.passed)}


def timing(matches: Mapping[str, Sequence[Match]]) -> Check:
    found = [m for hits in matches.values() for m in hits]
    furthest = max(found, key=lambda m: abs(m.error_ms), default=None)
    worst = abs(furthest.error_ms) if furthest else 0.0
    note = f"{len(found)} hits checked"
    if furthest and worst:
        note += f"; furthest off: {furthest.event.voice} step {furthest.event.step}"
    return Check(
        "timing: worst hit error (ms)",
        round(worst, 3),
        f"<= {TIMING_LIMIT_MS}",
        worst <= TIMING_LIMIT_MS,
        note,
    )


def velocity(matches: Mapping[str, Sequence[Match]]) -> Check:
    errors = [(abs(m.level - m.event.velocity), m) for found in matches.values() for m in found]
    worst, where = max(errors, key=lambda pair: pair[0], default=(0.0, None))
    note = f"{where.event.voice} step {where.event.step}" if where else ""
    return Check(
        "velocity: worst level error",
        round(worst, 3),
        f"<= {VELOCITY_LIMIT}",
        worst <= VELOCITY_LIMIT,
        note,
    )


def swing(score: Score, matches: Mapping[str, Sequence[Match]]) -> list[Check]:
    found = []
    triplets = {v.name for v in score.voices if v.triplets}  # they get a feel check instead
    for name, voice_matches in matches.items():
        if name in triplets:
            continue
        measured = audio.swing_of(voice_matches, score.step_s)
        if measured is None:
            continue
        error = abs(measured - score.swing)
        found.append(
            Check(
                f"swing: {name}",
                round(measured, 4),
                f"{score.swing} +/- {SWING_LIMIT}",
                error <= SWING_LIMIT,
            )
        )
    return found


def feel(score: Score, matches: Mapping[str, Sequence[Match]]) -> list[Check]:
    """REQ-BEN-13: each triplet voice's later pulses start where the feel puts them."""
    scored = feel_shares(score.feel)
    found = []
    for voice in (v for v in score.voices if v.triplets):
        measured = audio.feel_of(matches[voice.name], score.beat_s)
        if not measured:
            continue  # it only plays on the beat
        errors = {pulse: abs(at - scored[pulse]) for pulse, at in measured.items()}
        worst = max(errors.values())
        note = "; ".join(
            f"pulse {pulse + 1} at {at:.3f} of the beat, scored {scored[pulse]:.3f}"
            for pulse, at in measured.items()
        )
        found.append(
            Check(
                f"feel: {voice.name} worst pulse error (share of a beat)",
                round(worst, 4),
                f"<= {FEEL_LIMIT}",
                worst <= FEEL_LIMIT,
                note,
            )
        )
    return found


def chokes(
    score: Score,
    events: Sequence[Event],
    stems: Mapping[str, Audio],
    peaks: Mapping[str, float],
    rate: int,
) -> list[Check]:
    """After a choke, the choked voice must be silent until it plays again."""
    found = []
    for voice in score.voices:
        for target in voice.chokes:
            worst = 0.0
            target_times = [e.time_s for e in events if e.voice == target]
            for event in (e for e in events if e.voice == voice.name):
                if not any(t < event.time_s for t in target_times):
                    continue
                later = [t for t in target_times if t >= event.time_s]
                end = min([*later, event.time_s + 0.05])
                residue = audio.residue_after(
                    stems[target], event.time_s + CHOKE_SETTLE_S, end, rate
                )
                worst = max(worst, residue)
            level = audio.db(worst / peaks[target]) if peaks.get(target) else float("-inf")
            found.append(
                Check(
                    f"choke: {voice.name} cuts {target} (dB left)",
                    max(round(level, 1), -200.0),
                    f"<= {CHOKE_LIMIT_DB}",
                    level <= CHOKE_LIMIT_DB,
                )
            )
    return found


def humanise(score: Score, matches: Mapping[str, Sequence[Match]]) -> list[Check]:
    """Humanised velocities vary, by no more than the score allows, and render as
    scored. Each hit is compared with its velocity before humanising, so a part
    with accents (BM-AFRO-02's shaker) is judged on the humanising alone."""
    plain_score = replace(score, voices=tuple(replace(v, humanise=0.0) for v in score.voices))
    plain = {(e.voice, e.step): e.velocity for e in events(plain_score)}
    found = []
    for voice in score.voices:
        if not voice.humanise:
            continue
        own = matches[voice.name]
        ratios = [m.level / m.event.velocity for m in own if m.event.velocity]
        factors = [
            m.event.velocity / plain[voice.name, m.event.step]
            for m in own
            if plain.get((voice.name, m.event.step))
        ]
        spread = (max(factors) - min(factors)) / max(factors) if factors else 0.0
        measured = (max(ratios) - min(ratios)) if ratios else 0.0
        found.append(
            Check(
                f"humanise: {voice.name} velocity spread",
                round(spread, 3),
                f"0 < spread <= {2 * voice.humanise}, rendered as scored",
                0 < spread <= 2 * voice.humanise + HUMANISE_SLACK and measured <= VELOCITY_LIMIT,
            )
        )
    return found


def edges(name: str, stem: Audio, edges_s: Sequence[float], rate: int) -> Check:
    jump = audio.edge_jumps(stem, edges_s, rate)
    return Check(
        f"clicks: {name} worst slice-edge jump against its surroundings",
        round(jump, 4),
        f"<= {EDGE_LIMIT}",
        jump <= EDGE_LIMIT,
    )


def lead_in(levels: Mapping[str, float]) -> Check:
    value = levels["lead_in_ms"]
    return Check(
        "lead-in before the first sound (ms)",
        value,
        f"<= {LEAD_IN_LIMIT_MS}",
        value <= LEAD_IN_LIMIT_MS,
    )


def determinism(first: Audio, second: Audio, *, null_limit_db: float | None = None) -> Check:
    """REQ-AUD-05: the same score rendered twice gives the same samples.

    SuperCollider must be bit-identical. Renoise passes a null test instead:
    the two renders may differ only below ``null_limit_db`` (-120 dBFS)."""
    if null_limit_db is None:
        same_shape = first.shape == second.shape
        differing = int((first != second).sum()) if same_shape else -1
        return Check(
            "determinism: samples differing in a second render",
            float(differing),
            "0 (bit-identical)",
            differing == 0,
            "" if same_shape else "the renders differ in length",
        )
    if first.shape != second.shape:
        return Check("determinism: null test of a second render (dBFS)", 0.0, "same length", False)
    differing = int((first != second).sum())
    peak = float(abs(first - second).max()) if first.size else 0.0
    level = max(round(audio.db(peak), 1), -200.0) if peak else -200.0
    return Check(
        "determinism: null test of a second render (dBFS)",
        level,
        f"<= {null_limit_db:g}",
        level <= null_limit_db,
        f"{differing} samples differ" if differing else "bit-identical",
    )


def accents(
    name: str, pairs: Sequence[tuple[Audio, Audio]], rate: int, frames: int, *, brighter: bool
) -> list[Check]:
    """Accented notes against the same notes unaccented: louder, and (when
    ``brighter``) brighter."""
    if not pairs:
        return []
    louder = min(audio.db(audio.rms(a[:frames]) / audio.rms(p[:frames])) for a, p in pairs)
    found = [
        Check(f"accent: {name} louder (dB)", round(louder, 2), f">= {ACCENT_LOUDER_DB}",
              louder >= ACCENT_LOUDER_DB),
    ]  # fmt: skip
    if brighter:
        ratio = min(
            audio.centroid(a[:frames], rate) / audio.centroid(p[:frames], rate) for a, p in pairs
        )
        found.append(
            Check(f"accent: {name} brighter (centroid ratio)", round(ratio, 3), "> 1", ratio > 1)
        )
    return found


def slides(name: str, stem: Audio, slid: Sequence[Event], rate: int) -> list[Check]:
    """A slid-into note keeps sounding: no retrigger dip where it starts."""
    if not slid:
        return []
    dips = []
    for event in slid:
        at = event.sample(rate)
        # Two cycles of the note either side of the slide, so the waveform's own
        # shape doesn't count as a dip.
        span = round(2 * rate / (440.0 * 2 ** ((event.pitch - 69) / 12)))
        before, after = audio.rms(stem[at - span : at]), audio.rms(stem[at : at + span])
        dips.append(audio.db(after / before) if before else float("-inf"))
    worst = min(dips)
    return [
        Check(f"slide: {name} level change into a slid note (dB)", round(worst, 2),
              f">= {SLIDE_DIP_DB}", worst >= SLIDE_DIP_DB),
    ]  # fmt: skip


def glide(name: str, cents: float) -> Check:
    """A note slid into, then held, settles on its own pitch."""
    return Check(f"slide: {name} pitch error once a glide settles (cents)", round(cents, 1),
                 f"<= {SLIDE_CENTS}", cents <= SLIDE_CENTS)  # fmt: skip


def aliasing(name: str, level_db: float) -> Check:
    """REQ-AUD-04: energy between the harmonics of a high, open, resonant note."""
    return Check(f"aliasing: {name} inharmonic energy (dB)", max(round(level_db, 1), -200.0),
                 f"<= {ALIASING_LIMIT_DB}", level_db <= ALIASING_LIMIT_DB)  # fmt: skip


def sidechain(
    sidechain: Sidechain,
    target: str,
    wet: Audio,
    dry: Audio,
    triggers_s: Sequence[float],
    *,
    rate: int,
) -> list[Check]:
    """REQ-BEN-12 and BM-SC-01: at each trigger hit, the target dips by the
    depth (within the slack) quickly, holds, and is back to full level just
    after the release ends, along a smooth curve. Measured as the ducked
    target against the same target rendered dry."""
    after_ms = DUCK_RECOVER_AFTER_S * 1000
    dip_ms = sidechain.attack_ms + sidechain.hold_ms + sidechain.release_ms
    dips = audio.dips(
        wet,
        dry,
        triggers_s,
        rate=rate,
        window_s=DUCK_WINDOW_S,
        search_s=sidechain.attack_ms / 1000 + DUCK_DIP_SEARCH_S,
        recover_s=(dip_ms + after_ms) / 1000,
        reached_db=sidechain.depth_db - DUCK_DEPTH_SLACK_DB,
    )
    name = f"sidechain: {target}"
    if not dips:
        return [Check(f"{name} dip (dB)", 0.0, "measurable", False, "silent at every hit")]
    worst_depth = max((d.depth_db for d in dips), key=lambda v: abs(v - sidechain.depth_db))
    reach = max(d.reach_ms for d in dips)
    recovered = [d.recovered_db for d in dips if not math.isnan(d.recovered_db)]
    level = min(recovered, default=0.0)
    step = max(d.step_db for d in dips)
    note = f"{len(dips)} of {len(triggers_s)} hits measured"
    return [
        Check(f"{name} dip (dB)", round(worst_depth, 2),
              f"{sidechain.depth_db:g} ± {DUCK_DEPTH_SLACK_DB:g}",
              abs(worst_depth - sidechain.depth_db) <= DUCK_DEPTH_SLACK_DB, note),
        Check(f"{name} dip reached (ms)", round(reach, 2), f"<= {DUCK_REACH_MS:g}",
              reach <= DUCK_REACH_MS),
        Check(f"{name} level {after_ms:g} ms after the release (dB)", round(level, 2),
              f">= {DUCK_RECOVERED_DB:g}", level >= DUCK_RECOVERED_DB),
        Check(f"{name} biggest step on the way back (dB per 0.5 ms)", round(step, 3),
              f"<= {DUCK_STEP_DB:g}", step <= DUCK_STEP_DB),
    ]  # fmt: skip


def sidechain_mix(under: Sequence[float], after: Sequence[float], hits: int) -> list[Check]:
    """REQ-BEN-12: how much the ducking changes the whole mix, against the
    same mix with the sidechain bypassed, K-weighted. ``under`` and ``after``
    hold one change (dB) per trigger hit, in the windows under the kick and
    after it. Only the one after the kick must be big enough to hear: under
    the kick, the kick itself covers the dip."""
    name = "sidechain: whole mix 100-200 ms after the kick (dB)"
    if not after:
        return [Check(name, 0.0, "measurable", False, "silent after every hit")]
    change = statistics.median(after)
    below = f"{statistics.median(under):+.1f} dB" if under else "silent"
    note = (
        f"against bypass, median of {len(after)} of {hits} hits, weighted as loudness; "
        f"under the kick (5-35 ms): {below}"
    )
    return [
        Check(name, round(change, 2), f"<= -{MIX_AUDIBLE_DB:g}", change <= -MIX_AUDIBLE_DB, note)
    ]
