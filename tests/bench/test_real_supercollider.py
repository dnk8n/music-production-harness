"""Renders with the real SuperCollider (marker ``tools``; skipped without it).

Run with ``uv run pytest -m tools`` on a machine with SuperCollider installed.
CI skips these: unit tests never touch real apps (REQ-ENG-03, REQ-ENG-06).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mph.bench import audio, render, sc, score
from mph.catalogue import db
from mph.proc import SubprocessRunner
from tests.bench.test_render import cfg_for
from tests.bench.test_tracks import add_song, track

pytestmark = pytest.mark.tools


@pytest.fixture
def real() -> SubprocessRunner:
    runner = SubprocessRunner(timeout_s=300)
    if sc.find_tools(runner) is None:
        pytest.skip("SuperCollider isn't installed")
    return runner


@pytest.mark.req("REQ-BEN-01")
@pytest.mark.req("REQ-AUD-05")
@pytest.mark.req("REQ-AUD-04")
@pytest.mark.req("REQ-BEN-12")
@pytest.mark.parametrize(
    "benchmark_id", ["BM-DRUM-02", "BM-DRUM-06", "BM-DRUM-08", "BM-BASS-01", "BM-SC-01"]
)
def test_synth_kit_benchmarks_pass_with_real_supercollider(
    real: SubprocessRunner, tmp_path: Path, benchmark_id: str
) -> None:
    outcome = render.run(
        benchmark_id, cfg_for(tmp_path, rate=96000), real, None, engine="supercollider"
    )
    failures = [m for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []


@pytest.mark.req("REQ-BEN-12")
@pytest.mark.parametrize("take", ["exaggerated", "over-the-top", "bypass"])
def test_bm_sc_01_takes_pass_with_real_supercollider(
    real: SubprocessRunner, tmp_path: Path, take: str
) -> None:
    outcome = render.run(
        "BM-SC-01", cfg_for(tmp_path, rate=96000), real, None, engine="supercollider", variant=take
    )
    failures = [m for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []


@pytest.mark.req("REQ-BEN-13")
@pytest.mark.req("REQ-BEN-01")
@pytest.mark.parametrize(
    ("benchmark_id", "take"),
    [
        ("BM-AFRO-01", "supercollider"),
        ("BM-AFRO-01", "flattened"),
        ("BM-AFRO-02", "supercollider"),
        ("BM-AFRO-02", "even"),
    ],
)
def test_north_star_takes_pass_with_real_supercollider(
    real: SubprocessRunner, tmp_path: Path, benchmark_id: str, take: str
) -> None:
    outcome = render.run(
        benchmark_id,
        cfg_for(tmp_path, rate=96000),
        real,
        None,
        engine="supercollider",
        variant=take,
    )
    measured = outcome.report["measurements"]
    failures = [m for m in measured if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == []
    has_feel = any(m["name"].startswith("feel:") for m in measured)  # type: ignore[attr-defined]
    assert has_feel == (take != "flattened")  # the flattened take has no triplets left


# --- the mix (REQ-MIX-01 to REQ-MIX-04) --------------------------------------------------

RATE = 96000
ONE_HIT = " | ".join(["X" + " ." * 15] + [" ".join(["."] * 16)] * 3)
INTO = """
id = "BM-INTO"
title = "One rim hit into a send"
bpm = 120
bars = 4

[voices.rim]
grid = "{grid}"
sound = {{ role = "rim" }}

[voices.rim.mix]
send = {{ {send} = 0 }}

[sends.{send}]
{settings}
"""
ECHO = 'kind = "delay"\nbeats = 1\nfeedback = 0.5\nlowpass = 20000\npingpong = true'
HALL = 'kind = "reverb"\ndecay_s = 2'
HELD = " ".join(["C3"] * 16)
LEVELS = f"""
id = "BM-LEVELS"
title = "A held organ, turned down and back up"
bpm = 120
bars = 4

[voices.organ]
notes = "{HELD}"
gate  = "x - - - - - - - - - - - - - - ."
sound = {{ synth = "mph_organ" }}

[voices.organ.automate]
level = [[2, 0], [3, -12], [4, -12], [4, 0]]
lowpass = [[1, 20000]]
"""
NEW_SYNTHS = f"""
id = "BM-SYNTHS"
title = "The new synths, mixed"
bpm = 120
bars = 2
swing = 0.55

[voices.kick]
grid = "X . . . X . . . X . . . X . . ."
sound = {{ role = "kick" }}

[voices.bass]
notes  = "C2 C2 C3 C2 C2 C2 C3 C2 C2 C2 C3 C2 C2 C2 G2 G2"
gate   = "x x x x x x x x x x x x x x x x"
accent = ". . X . . . X . . . X . . . X ."
slide  = ". . . . . . . . . . . . . . . S"
sound = {{ synth = "mph_bass" }}

[voices.organ]
notes = "C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3 C3"
gate  = ". . x . . . x . . . x . . . x -"
sound = {{ synth = "mph_organ" }}

[voices.stab]
notes = "E4 E4 E4 E4 E4 E4 E4 E4 E4 E4 E4 E4 E4 E4 E4 E4"
gate  = ". . . x - . . . . . x - . . . ."
chord = [0, 3, 7, 10]
sound = {{ synth = "mph_stab" }}

[voices.strings]
notes = "{" ".join(["C4"] * 16)} | {" ".join(["C4"] * 16)}"
gate  = "x - - - - - - - - - - - - - - - | . . . . . . . . . . . . . . . ."
chord = [0, 4, 7, 11]
sound = {{ synth = "mph_strings" }}

[voices.bass.mix]
highpass = 35
lowpass = 900
resonance = 0.4

[voices.stab.mix]
pan = 0.3
send = {{ echo = -6, hall = -12 }}

[voices.strings.mix]
level = -4
send = {{ hall = -8 }}

[voices.stab.automate]
lowpass = [[1, 800], [3, 6000]]

[sends.echo]
kind = "delay"
beats = 0.75
feedback = 0.45
lowpass = 3000
pingpong = true
send = {{ hall = -10 }}

[sends.hall]
kind = "reverb"
decay_s = 3
level = -2

[master]
highpass = 25

[sidechain]
trigger = "kick"
targets = ["bass", "strings"]
depth_db = 8
release_ms = 400
"""


def _master(
    real: SubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> np.ndarray:
    """Render a score with the real SuperCollider; every check passes, and the
    master comes back."""
    loaded = score.load_text(text)
    monkeypatch.setattr(render, "load", lambda _: loaded)
    outcome = render.run(
        loaded.id, cfg_for(tmp_path, rate=RATE), real, None, engine="supercollider"
    )
    failures = [
        (m["name"], m["value"], m["note"])
        for m in outcome.report["measurements"]  # type: ignore[attr-defined]
        if not m["passed"]
    ]
    assert failures == []
    master, rate = audio.read(outcome.folder / "master.wav")
    assert rate == RATE
    return master


def _found(channel: np.ndarray, template: np.ndarray, at: int, reach: int) -> tuple[int, float]:
    """Where ``template`` lines up best with ``channel`` within ``reach`` of ``at``,
    and how loud the channel is there against the template (dB)."""
    segment = channel[at - reach : at + reach + len(template)]
    lag = int(np.argmax(np.abs(np.correlate(segment, template, mode="valid"))))
    piece = segment[lag : lag + len(template)]
    return at - reach + lag, audio.db(audio.rms(piece) / audio.rms(template))


@pytest.mark.req("REQ-MIX-02")
@pytest.mark.req("REQ-MIX-04")
def test_an_echo_repeats_on_the_beat_halving_and_swapping_sides(
    real: SubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master = _master(
        real, tmp_path, monkeypatch, INTO.format(grid=ONE_HIT, send="echo", settings=ECHO)
    )
    left, right = master[:, 0], master[:, 1]
    start = int(np.flatnonzero(np.abs(left) > 1e-3)[0])
    hit = left[start : start + RATE // 50]  # the rim, dry
    beat, reach = RATE // 2, RATE // 100
    levels = []
    for k, (heard, other) in enumerate([(left, right), (right, left), (left, right)], start=1):
        at, level = _found(heard, hit, start + k * beat, reach)
        assert abs(at - (start + k * beat)) <= RATE // 10000, k  # within 0.1 ms
        quiet = audio.db(audio.rms(other[at : at + len(hit)]) / audio.rms(hit))
        assert quiet < level - 40, k  # the other side is silent
        levels.append(level)
    # The first echo is the hit in mono, as loud as it; each one after is half as loud.
    assert levels == pytest.approx([0.0, -6.02, -12.04], abs=0.5)


@pytest.mark.req("REQ-MIX-02")
@pytest.mark.req("REQ-MIX-04")
def test_a_hall_dies_away_over_its_decay_time(
    real: SubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master = _master(
        real, tmp_path, monkeypatch, INTO.format(grid=ONE_HIT, send="hall", settings=HALL)
    )
    signal = audio.mono(master)
    hit = int(np.argmax(np.abs(signal[: RATE // 4])))
    # The tail from 50 ms after the hit (the rim itself lasts 30 ms), in the
    # band where damping takes little away, integrated backwards (Schroeder).
    tail = signal[hit + RATE // 20 :]
    spectrum = np.fft.rfft(tail)
    hz = np.fft.rfftfreq(len(tail), 1 / RATE)
    band = np.fft.irfft(spectrum * ((hz >= 300) & (hz <= 3000)), len(tail))
    energy = np.cumsum(band[::-1] ** 2)[::-1]
    curve = 10 * np.log10(energy / energy[0])
    seconds = np.arange(len(curve)) / RATE
    used = (curve <= -5) & (curve >= -25)
    slope = np.polyfit(seconds[used], curve[used], 1)[0]
    assert -60 / slope == pytest.approx(2.0, rel=0.15)  # the time to fall 60 dB


@pytest.mark.req("REQ-MIX-03")
@pytest.mark.req("REQ-MIX-04")
def test_automation_moves_a_level_along_a_line_and_jumps(
    real: SubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master = _master(real, tmp_path, monkeypatch, LEVELS)
    bar = 2 * RATE

    def level(first: float, last: float) -> float:
        return audio.db(audio.rms(master[round(first * bar) : round(last * bar)]))

    whole = level(0.05, 0.95)
    assert level(2.05, 2.95) - whole == pytest.approx(-12.0, abs=0.3)  # bar 3, turned down
    assert level(3.05, 3.95) - whole == pytest.approx(0.0, abs=0.3)  # bar 4, back up at once
    # Halfway down the line, in the middle of bar 2: half the dB, against the
    # same part of bar 1's note.
    assert level(1.45, 1.55) - level(0.45, 0.55) == pytest.approx(-6.0, abs=0.5)


@pytest.mark.req("REQ-MIX-01")
@pytest.mark.req("REQ-MIX-02")
@pytest.mark.req("REQ-MIX-04")
@pytest.mark.req("REQ-BEN-18")
@pytest.mark.req("REQ-AUD-04")
@pytest.mark.req("REQ-BEN-12")
def test_the_new_synths_pass_in_a_mix_with_real_supercollider(
    real: SubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every check passes: timing, levels, aliasing of each synth alone, the
    # sidechain's dip on its targets and on the whole mix, and determinism.
    master = _master(real, tmp_path, monkeypatch, NEW_SYNTHS)
    assert np.isfinite(master).all() and 0 < np.abs(master).max() < 1


# --- clips (REQ-TRK-02) --------------------------------------------------------------------


@pytest.mark.req("REQ-TRK-02")
@pytest.mark.req("REQ-TRK-06")
@pytest.mark.parametrize(("leftovers_db", "passes"), [(-40.0, True), (0.0, False)])
def test_clips_play_from_a_recording_at_another_rate_with_real_supercollider(
    real: SubprocessRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    leftovers_db: float,
    passes: bool,
) -> None:
    # A 44.1 kHz recording played at 48 kHz, through PlayBuf's cubic resampling,
    # with bright noise on the last bar of a clip: faint, as a stem's leftovers
    # between lines are, it passes; as loud as the song, it fails, as the fake
    # engine says it does.
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    loaded = track(add_song(conn, tmp_path, leftovers_db=leftovers_db))
    monkeypatch.setattr(render, "load", lambda _: loaded)
    outcome = render.run("BM-TT", cfg_for(tmp_path), real, conn, engine="supercollider")
    conn.close()
    failures = [m["name"] for m in outcome.report["measurements"] if not m["passed"]]  # type: ignore[attr-defined]
    assert failures == (
        [] if passes else ["clips: song worst match to the recording (dB left after cancelling)"]
    )
