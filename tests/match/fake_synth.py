"""A stand-in scsynth for the matcher's tests, so they never run SuperCollider.

It reads the OSC score the matcher writes and plays every note (an /s_new
with ``freq``) until its /n_set gate 0, as a tone whose harmonics fade above
its ``brass`` control and whose level rises over its ``swell`` control's
seconds, the two of mph_horn's knobs the tests turn. Anything else a note is
given is ignored, so a search has two knobs to find and nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from mph.bench import audio, osc
from mph.proc import Result

BRASS = 1300.0  # mph_horn's defaults
SWELL = 4.5
HARMONICS = 12


def knob_scsynth(argv: Sequence[str]) -> Result:
    score_file = Path(argv[argv.index("-N") + 1])
    out, rate = Path(argv[argv.index("-N") + 3]), int(argv[argv.index("-N") + 4])
    bundles = osc.decode_score(score_file.read_bytes())
    data = np.zeros((round(bundles[-1].time_s * rate), 2), dtype=np.float32)
    started: dict[int, tuple[int, dict[str, float]]] = {}
    for bundle in bundles:
        at = round(bundle.time_s * rate)
        for message in bundle.messages:
            if message.address == "/s_new":
                pairs = message.args[4:]
                args = {str(pairs[i]): float(pairs[i + 1]) for i in range(0, len(pairs), 2)}
                started[int(message.args[1])] = (at, args)
            elif message.address == "/n_set" and message.args[1] == "gate":
                begin, args = started.pop(int(message.args[0]))
                data[begin:at] += play(args, at - begin, rate)[:, None]
    audio.write_float(out, data, rate)
    return Result(0)


def play(args: dict[str, float], frames: int, rate: int) -> np.ndarray:
    """One note: its harmonics fading above ``brass`` Hz, rising over ``swell`` s."""
    t = np.arange(frames) / rate
    freq, brass, swell = args["freq"], args.get("brass", BRASS), args.get("swell", SWELL)
    tone = sum(
        np.exp(-n * freq / brass) * np.sin(2 * np.pi * n * freq * t)
        for n in range(1, HARMONICS + 1)
    )
    return np.asarray(np.minimum(1.0, (t + 1 / rate) / swell) * tone, dtype=np.float32)
