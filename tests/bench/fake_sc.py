"""A stand-in for scsynth and sclang, so render tests never run SuperCollider.

The fake reads the real OSC score mph writes, and "plays" every /s_new as a
short decaying sine on the right channels at the right sample, honouring
amp, dur and choke (/n_set gate 0). That's enough for the matching, swing,
choke and determinism checks to mean something.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from mph.bench import audio, osc
from mph.proc import Result

HIT_S = 0.08
CHOKE_RELEASE_S = 0.005


def _args(message: osc.Message) -> dict[str, float]:
    pairs = message.args[4:]
    return {str(pairs[i]): float(pairs[i + 1]) for i in range(0, len(pairs), 2)}


def _hit(name: str, args: dict[str, float], rate: int) -> np.ndarray:
    key = f"{name}/{args.get('buf', 0)}/{args.get('start', 0)}/{args.get('decay', 0)}"
    freq = 300 + zlib.crc32(key.encode()) % 3000
    seconds = args.get("dur") or HIT_S
    t = np.arange(round(min(seconds, HIT_S) * rate)) / rate
    return (args.get("amp", 1.0) * np.sin(2 * np.pi * freq * t) * np.exp(-t * 40)).astype(
        np.float32
    )


def fake_scsynth(argv: Sequence[str]) -> Result:
    if "-v" in argv:
        return Result(0, stdout="scsynth 3.13.0 (fake)\n")
    channels = int(argv[argv.index("-o") + 1])
    score_file = Path(argv[argv.index("-N") + 1])
    out, rate = Path(argv[argv.index("-N") + 3]), int(argv[argv.index("-N") + 4])
    bundles = osc.decode_score(score_file.read_bytes())
    length = round(bundles[-1].time_s * rate)
    data = np.zeros((length, channels), dtype=np.float32)
    nodes: dict[int, tuple[int, int, int]] = {}  # node -> (start, end, channel)
    for bundle in bundles:
        at = round(bundle.time_s * rate)
        for message in bundle.messages:
            if message.address == "/s_new":
                args = _args(message)
                wave = _hit(str(message.args[0]), args, rate)[: max(length - at, 0)]
                channel = int(args["out"])
                data[at : at + len(wave), channel] += wave
                data[at : at + len(wave), channel + 1] += wave
                nodes[int(message.args[1])] = (at, at + len(wave), channel)
            elif message.address == "/n_set":
                start, end, channel = nodes[int(message.args[0])]
                cut = max(at + round(CHOKE_RELEASE_S * rate), start)
                if cut < end:
                    data[cut:end, channel : channel + 2] = 0.0
    audio.write_float(out, data, rate)
    return Result(0)


def fake_sclang(argv: Sequence[str]) -> Result:
    folder = Path(argv[2])
    (folder / "mph_kick.scsyndef").write_bytes(b"SCgf")
    return Result(0, stdout="mph: synthdefs written\n")
