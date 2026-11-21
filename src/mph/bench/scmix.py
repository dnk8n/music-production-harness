"""The synth graph of a SuperCollider render, and the mix in it
(spec/components/mix.md, REQ-MIX-01 to REQ-MIX-04).

A render's synths run in groups, in this order: automation, the voices, taps
of the sidechain's targets before their dip, the sidechain (REQ-BEN-12), the
strips, the sends and the master. A score without a mix uses only the voice
and sidechain groups, and each voice writes straight to its own pair of
output channels, as before.

In a mixed render each voice writes to a private pair of buses instead. Its
strip (``mph_strip``) takes the pair after the sidechain and feeds the master
bus and the send buses. Each send's effect (``mph_delay`` or ``mph_reverb``)
reads its bus and returns through a strip of its own, which may feed sends
listed after it, and the master strip writes the master to the output. In
the first pass, taps (``mph_tap``) also copy each voice as it enters its
strip (its stem) and each sidechain target before its dip to output channels
of their own. The second pass writes the master alone, for the determinism
check (REQ-AUD-05).

A moving control reads a control bus (``/n_map``). ``/c_set`` sets the bus at
the start and at each jump, and ``mph_line`` (levels, in dB) or ``mph_xline``
(frequencies, evenly in octaves) moves it from one point to the next. Each
line ends one control block early, so it never overwrites a jump.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from mph.bench import mix
from mph.bench.mix import Lane, Send, Strip
from mph.bench.osc import Arg, Bundle, Message
from mph.bench.score import Score

HEAD, TAIL, AFTER = 0, 1, 3  # scsynth add actions
AUTO_NODE = -1  # scsynth picks the id, for synths nothing addresses again
AUTOMATION_GROUP = 90  # first, so controls move before anything reads them
VOICES_GROUP = 100
BYPASS_GROUP = 102  # sidechain targets, copied before their dip
SIDECHAIN_GROUP = 101  # runs after the voices, so the duckers see their output
STRIPS_GROUP = 103
SENDS_GROUP = 104
MASTER_GROUP = 105
DUCK_BUS = 512  # a private audio bus, well past the output and input channels
VOICE_BUS = 600  # voice i's private pair in a mixed render: 600 + 2i
SEND_BUS = 800  # send j's input pair is 800 + 4j, and its return pair the next two
MASTER_BUS = 1000  # below scsynth's 1024 audio buses
CONTROL_BUS = 100  # the first control bus automation moves
STRIP_NODE = 500  # voice i's strip is node 500 + i
EFFECT_NODE = 700  # send j's effect is node 700 + j
RETURN_NODE = 800  # and its return strip node 800 + j
MASTER_NODE = 900
MAX_VOICES = (SEND_BUS - VOICE_BUS) // 2  # voices a mixed render has room for
MAX_SENDS = (MASTER_BUS - SEND_BUS) // 4
BLOCK = 64  # samples in a control block (scsynth's default)
EFFECTS = {mix.DELAY: "mph_delay", mix.REVERB: "mph_reverb"}
VOICE_MEMORY_KB = 65536  # scsynth's real-time memory for the voices: 8 times its default
# The longest delay of each delay line in a send's effect (s): mph_delay's two
# channels each have a first echo and a loop; mph_reverb has a predelay per
# side, four allpasses per side and eight lines (synthdefs.scd).
DELAY_LINES = {
    mix.DELAY: (mix.MAX_DELAY_S,) * 4,
    mix.REVERB: (0.5,) * 2 + (0.05,) * 8 + (0.1,) * 8,
}


@dataclass(frozen=True, slots=True)
class Layout:
    """Where one pass's signals go."""

    score: Score
    master_only: bool = False  # the second pass of a mix: the master alone

    @property
    def mixed(self) -> bool:
        return self.score.mixed

    @property
    def targets(self) -> tuple[str, ...]:
        """The sidechain targets tapped before their dip: in a mix's first pass."""
        chain = self.score.sidechain
        if not self.mixed or self.master_only or chain is None or not chain.active:
            return ()
        return chain.targets

    def out(self, index: int) -> int:
        """Where voice ``index`` writes: its private pair in a mix, else its channels."""
        return VOICE_BUS + 2 * index if self.mixed else 2 * index

    @property
    def master_out(self) -> int:
        """The master's first output channel in a mix: after the stems and targets."""
        return 0 if self.master_only else 2 * (len(self.score.voices) + len(self.targets))

    @property
    def channels(self) -> int:
        if not self.mixed:
            return 2 * len(self.score.voices)
        return self.master_out + 2


def check(score: Score) -> None:
    """A mix fits the buses set aside for it."""
    if len(score.voices) > MAX_VOICES or len(score.sends) > MAX_SENDS:
        raise ValueError(
            f"{score.id}: a mix has room for {MAX_VOICES} voices and {MAX_SENDS} sends"
        )


def memory_kb(score: Score, rate: int) -> int:
    """scsynth's real-time memory for a render (its ``-m``, in KB): the voices'
    share, and room for every delay line of every send. scsynth rounds each
    line up to a power of two samples, four bytes each."""
    lines = [s for send in score.sends for s in DELAY_LINES[send.kind]]
    samples = sum(1 << math.ceil(math.log2(math.ceil(s * rate) + 1)) for s in lines)
    return VOICE_MEMORY_KB + math.ceil(samples * 4 / 1024)


def groups(layout: Layout) -> list[Message]:
    """The groups, created in the order they run."""
    if not layout.mixed:
        return [
            Message("/g_new", (VOICES_GROUP, TAIL, 0)),
            Message("/g_new", (SIDECHAIN_GROUP, AFTER, VOICES_GROUP)),
        ]
    order = (VOICES_GROUP, BYPASS_GROUP, SIDECHAIN_GROUP, STRIPS_GROUP, SENDS_GROUP, MASTER_GROUP)
    return [
        Message("/g_new", (AUTOMATION_GROUP, HEAD, 0)),
        Message("/g_new", (VOICES_GROUP, TAIL, 0)),
        *(Message("/g_new", (group, AFTER, before)) for before, group in itertools.pairwise(order)),
    ]


def _db(value: float) -> float:
    """A level or send as the strip takes it: silence is -90 dB, never -inf."""
    return max(value, mix.SILENT_DB)


def strip_args(own: Strip, names: Sequence[str]) -> list[Arg]:
    """An ``mph_strip``'s controls as a strip sets them before anything moves.
    ``names`` are the score's sends, in order, which place each send's bus."""
    args: list[Arg] = ["hp", int(own.uses("highpass")), "lp", int(own.uses("lowpass"))]
    for control in mix.FREQUENCIES:
        if own.uses(control):
            args += [control, own.start(control)]
    args += ["resonance", own.resonance, "level", _db(own.start("level")), "pan", own.pan]
    for k, (name, _) in enumerate(own.sends, start=1):
        args += [f"bus{k}", SEND_BUS + 4 * names.index(name), f"send{k}", _db(own.start(name))]
    return args


def effect_args(send: Send, beat_s: float) -> list[Arg]:
    """A send's effect settings as its synth takes them (REQ-MIX-02)."""
    if send.kind == mix.DELAY:
        return [
            *("time", send.setting("beats") * beat_s, "feedback", send.setting("feedback")),
            *("lowpass", send.setting("lowpass"), "pingpong", int(send.setting("pingpong"))),
        ]
    return [
        *("decay", send.setting("decay_s"), "damping", send.setting("damping")),
        *("predelay", send.setting("predelay_ms") / 1000),
    ]


def _strips(layout: Layout) -> list[tuple[int, Strip]]:
    """Every strip in the mix, by its node: the voices', the returns' and the master."""
    score = layout.score
    found = [(STRIP_NODE + n, v.strip or Strip()) for n, v in enumerate(score.voices)]
    found += [(RETURN_NODE + n, send.strip) for n, send in enumerate(score.sends)]
    return [*found, (MASTER_NODE, score.master or Strip())]


def synths(layout: Layout) -> list[Message]:
    """The mix's synths, all started at the beginning: taps, strips, effects and
    the master, each in its group."""
    score = layout.score
    names = [send.name for send in score.sends]
    index = {voice.name: n for n, voice in enumerate(score.voices)}
    found = []
    for k, target in enumerate(layout.targets):  # before the dip (REQ-BEN-12)
        tap = ("in", layout.out(index[target]), "out", 2 * (len(score.voices) + k))
        found.append(Message("/s_new", ("mph_tap", AUTO_NODE, TAIL, BYPASS_GROUP, *tap)))
    if not layout.master_only:  # each voice's stem, as it enters its strip
        found += [
            Message(
                "/s_new",
                ("mph_tap", AUTO_NODE, TAIL, STRIPS_GROUP, "in", layout.out(n), "out", 2 * n),
            )
            for n in range(len(score.voices))
        ]
    strips = dict(_strips(layout))
    for n in range(len(score.voices)):
        args = ("in", layout.out(n), "out", MASTER_BUS, *strip_args(strips[STRIP_NODE + n], names))
        found.append(Message("/s_new", ("mph_strip", STRIP_NODE + n, TAIL, STRIPS_GROUP, *args)))
    for n, send in enumerate(score.sends):
        bus = SEND_BUS + 4 * n
        effect = ("in", bus, "out", bus + 2, *effect_args(send, score.beat_s))
        back = ("in", bus + 2, "out", MASTER_BUS, *strip_args(send.strip, names))
        found += [
            Message("/s_new", (EFFECTS[send.kind], EFFECT_NODE + n, TAIL, SENDS_GROUP, *effect)),
            Message("/s_new", ("mph_strip", RETURN_NODE + n, TAIL, SENDS_GROUP, *back)),
        ]
    master = ("in", MASTER_BUS, "out", layout.master_out, *strip_args(strips[MASTER_NODE], names))
    found.append(Message("/s_new", ("mph_strip", MASTER_NODE, TAIL, MASTER_GROUP, *master)))
    return found


# --- automation (REQ-MIX-03) ------------------------------------------------------------


def _value(lane: Lane, value: float) -> float:
    return value if lane.in_octaves else _db(value)


def _when(bar: float, bar_s: float, rate: int) -> float:
    """Where a place in the track falls, on a whole sample."""
    return round((bar - 1) * bar_s * rate) / rate


def moves(lane: Lane, bus: int, bar_s: float, rate: int) -> list[Bundle]:
    """A lane's moves on its control bus after the start: a line for each
    change between two points, and a ``/c_set`` for each jump."""
    block = BLOCK / rate
    found = []
    for (first, low), (last, high) in itertools.pairwise(lane.points):
        at = _when(first, bar_s, rate)
        start, end = _value(lane, low), _value(lane, high)
        span = _when(last, bar_s, rate) - at
        if span <= 2 * block:  # a jump, or as good as one
            found.append(Bundle(at, (Message("/c_set", (bus, end)),)))
        elif start != end:
            name = "mph_xline" if lane.in_octaves else "mph_line"
            args = ("bus", bus, "from", start, "to", end, "dur", span - block)
            found.append(
                Bundle(at, (Message("/s_new", (name, AUTO_NODE, TAIL, AUTOMATION_GROUP, *args)),))
            )
    return found


def automation(layout: Layout, rate: int) -> tuple[list[Message], list[Bundle]]:
    """Each lane of each strip: setting and mapping its control bus at the start,
    and its moves later."""
    now: list[Message] = []
    later: list[Bundle] = []
    bus = CONTROL_BUS
    for node, own in _strips(layout):
        sends = [name for name, _ in own.sends]
        for lane in own.lanes:
            control = (
                lane.target if lane.target in mix.MOVABLE else f"send{sends.index(lane.target) + 1}"
            )
            now += [
                Message("/c_set", (bus, _value(lane, lane.points[0][1]))),
                Message("/n_map", (node, control, bus)),
            ]
            later += moves(lane, bus, layout.score.bar_s, rate)
            bus += 1
    return now, later
