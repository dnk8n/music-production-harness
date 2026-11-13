from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph.bench import audio, osc
from mph.bench.score import Event
from mph.deliver import DeliveryError, deliver, tpdf_quantise

RATE = 48000


def blip(seconds: float = 0.05, freq: float = 900.0) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    wave = np.sin(2 * np.pi * freq * t) * np.exp(-t * 60)
    return np.stack([wave, wave], axis=1).astype(np.float32)


def place(hits: list[tuple[float, float]], length_s: float = 2.0) -> np.ndarray:
    out = np.zeros((round(length_s * RATE), 2), dtype=np.float32)
    wave = blip()
    for time_s, level in hits:
        at = round(time_s * RATE)
        out[at : at + len(wave)] += level * wave
    return out


# --- osc ------------------------------------------------------------------------------


def test_osc_round_trip() -> None:
    bundles = [
        osc.Bundle(0.5, (osc.Message("/s_new", ("mph_kick", 1000, 1, 0, "amp", 0.5)),)),
        osc.Bundle(0.0, (osc.Message("/b_allocRead", (0, "/a b/c.wav")), osc.Message("/x"))),
        osc.Bundle(1.25, (osc.Message("/blob", (b"abcde",)),)),
    ]
    decoded = osc.decode_score(osc.encode_score(bundles))
    assert [b.time_s for b in decoded] == [0.0, 0.5, 1.25]
    assert decoded[0].messages[0] == osc.Message("/b_allocRead", (0, "/a b/c.wav"))
    assert decoded[1].messages[0].args[:4] == ("mph_kick", 1000, 1, 0)
    assert decoded[1].messages[0].args[5] == pytest.approx(0.5)
    assert decoded[2].messages[0].args == (b"abcde",)


def test_osc_padding_and_types() -> None:
    assert len(osc.encode_message(osc.Message("/abc"))) % 4 == 0
    assert osc.encode_message(osc.Message("/abcd")).startswith(b"/abcd\0\0\0")
    with pytest.raises(TypeError, match="bool"):
        osc.encode_message(osc.Message("/x", (True,)))


# --- matching -------------------------------------------------------------------------


@pytest.mark.req("REQ-BEN-01")
def test_hits_are_found_to_the_sample_with_their_level() -> None:
    late = 0.0005  # half a millisecond
    stem = place([(0.1, 1.0), (0.6 + late, 0.45), (1.1, 0.8)])
    events = [Event("v", 0, 0.1, 1.0), Event("v", 4, 0.6, 0.45), Event("v", 8, 1.1, 0.8)]
    found = audio.match_hits(stem, blip(), events, RATE)
    assert [round(m.error_ms, 3) for m in found] == [0.0, 0.5, 0.0]
    assert [round(m.level, 3) for m in found] == [1.0, 0.45, 0.8]


def swelling_kick(seconds: float = 1.0) -> np.ndarray:
    """A deep kick that takes about 90 ms to reach its loudest, over a soft click."""
    t = np.arange(round(seconds * RATE)) / RATE
    body = np.sin(2 * np.pi * 30.0 * t) * (1 - np.exp(-t / 0.04)) * np.exp(-t / 0.3)
    body /= np.abs(body).max()
    noise = np.random.default_rng(3).standard_normal(len(t))
    wave = body + 10 ** (-14 / 20) * noise * np.exp(-t / 0.001)
    return np.stack([wave, wave], axis=1).astype(np.float32)


def cut_hits(wave: np.ndarray, times: list[float], fade_s: float = 0.007) -> np.ndarray:
    """One sound at each time, each hit cut by the next with a short fade, as Renoise plays them."""
    starts = [round(t * RATE) for t in times]
    out = np.zeros((starts[-1] + len(wave), 2), dtype=np.float32)
    fade = round(fade_s * RATE)
    for start, until in zip(starts, [*starts[1:], None], strict=True):
        piece = wave.copy()
        if until is not None:
            end = until - start
            piece[end + fade :] = 0
            piece[end : end + fade] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        out[start : start + len(piece)] += piece
    return out


@pytest.mark.req("REQ-BEN-01")
def test_a_sound_that_swells_in_is_found_where_it_starts() -> None:
    # BM-DRUM-02's fresh take: a kick this slow failed at 5 ms with every hit on time.
    kick = swelling_kick()
    times = [n * 60 / 122 for n in range(6)]
    events = [Event("kick", 4 * n, t, 1.0) for n, t in enumerate(times)]
    found = audio.match_hits(cut_hits(kick, times), kick, events, RATE, voice_times=times)
    assert [round(m.error_ms, 3) for m in found] == [0.0] * 6
    assert all(m.level == pytest.approx(1.0, abs=0.01) for m in found)


@pytest.mark.req("REQ-BEN-01")
def test_a_match_never_reaches_into_the_next_note() -> None:
    kick = swelling_kick()
    roll = [0.1 + n * 0.05 for n in range(8)]  # faster than the kick takes to swell
    events = [Event("kick", n, t, 1.0) for n, t in enumerate(roll)]
    found = audio.match_hits(cut_hits(kick, roll), kick, events, RATE, voice_times=roll)
    assert [round(m.error_ms, 3) for m in found] == [0.0] * 8


def test_swing_is_measured_from_the_hits() -> None:
    step = 0.1
    events = [Event("v", 0, 0.0, 1.0), Event("v", 1, 0.116, 1.0)]
    found = audio.match_hits(place([(0.0, 1.0), (0.116, 1.0)]), blip(), events, RATE)
    assert audio.swing_of(found, step) == pytest.approx(0.58, abs=1e-4)
    assert audio.swing_of(found[:1], step) is None


def test_residue_and_click_detection() -> None:
    stem = place([(0.1, 1.0)])
    assert audio.residue_after(stem, 0.5, 0.6, RATE) == 0.0
    assert audio.residue_after(stem, 0.1, 0.11, RATE) > 0.5
    assert audio.residue_after(stem, 5.0, 6.0, RATE) == 0.0
    t = np.arange(RATE) / RATE
    smooth = np.sin(2 * np.pi * 200 * t)[:, None].repeat(2, axis=1)
    clicky = smooth.copy()
    flip = RATE // 2 + RATE // 800  # a quarter cycle in: the wave is at its peak
    clicky[flip:] *= -1  # a hard polarity flip
    assert audio.edge_jumps(smooth, [0.5], RATE) < 1.5
    assert audio.edge_jumps(clicky, [flip / RATE], RATE) > 10


def test_break_tempo_fitting_and_slicing() -> None:
    assert audio.fit_tempo(65, 130) == 130
    assert audio.fit_tempo(260, 130) == 130
    assert audio.fit_tempo(120, 130) == 120
    bpm, eighth = 120.0, round(0.25 * RATE)
    signal = np.zeros(8 * eighth)
    wave = blip()[:, 0]
    for n in range(8):
        at = n * eighth + (60 if n % 2 else 0)  # some onsets slightly late
        signal[at : at + len(wave)] += wave
    slices = audio.slice_points(signal, RATE, bpm)
    assert len(slices) == 8 and slices[0] == (0, slices[1][0])
    assert all(abs(s - n * eighth) <= 70 for n, (s, _) in enumerate(slices))
    assert sum(n for _, n in slices) == 8 * eighth


def test_levels_report() -> None:
    stem = place([(0.0, 0.5), (1.0, 0.5)], length_s=3.0)
    levels = audio.levels(stem, RATE)
    assert levels["lead_in_ms"] == pytest.approx(0.02, abs=0.05)
    assert levels["sample_peak_dbfs"] < 0
    assert levels["true_peak_dbtp"] >= levels["sample_peak_dbfs"] - 0.1
    assert levels["seconds"] == 3.0
    assert audio.db(0.0) == float("-inf")
    assert audio.levels(np.zeros((RATE, 2), dtype=np.float32), RATE)["lead_in_ms"] == 0.0


@pytest.mark.req("REQ-AUD-02")
def test_float_stems_are_plain_and_reproducible(tmp_path: Path) -> None:
    stem = place([(0.1, 0.7)])
    audio.write_float(tmp_path / "a.wav", stem, 96000)
    audio.write_float(tmp_path / "b.wav", stem, 96000)
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()
    info = soundfile.info(str(tmp_path / "a.wav"))
    assert (info.samplerate, info.channels, info.subtype) == (96000, 2, "FLOAT")
    back, rate = audio.read(tmp_path / "a.wav")
    assert rate == 96000 and np.array_equal(back, stem)
    stems = audio.split(np.hstack([stem, stem * 2]), ["a", "b"])
    assert np.allclose(audio.mixdown(stems), stem * 3)


# --- delivery -------------------------------------------------------------------------


@pytest.mark.req("REQ-AUD-03")
def test_delivery_is_one_src_and_one_dither(tmp_path: Path) -> None:
    master = place([(0.1, 0.5)], length_s=1.0)
    first = deliver(master, RATE, tmp_path / "a.aiff", to_rate=24000, bits=24)
    second = deliver(master, RATE, tmp_path / "b.aiff", to_rate=24000, bits=24)
    assert first.read_bytes() == second.read_bytes()
    info = soundfile.info(str(first))
    assert (info.samplerate, info.subtype, info.format) == (24000, "PCM_24", "AIFF")
    assert info.frames == 24000
    same_rate = deliver(master, RATE, tmp_path / "c.wav", to_rate=RATE, bits=16, fmt="wav")
    assert soundfile.info(str(same_rate)).frames == RATE
    with pytest.raises(DeliveryError):
        deliver(master, RATE, tmp_path / "d.aiff", to_rate=RATE, bits=20)


def test_tpdf_dither_is_about_one_lsb() -> None:
    silence = np.zeros((48000, 1))
    quantised = tpdf_quantise(silence, 24)
    assert set(np.unique(quantised)) <= {-1, 0, 1}
    assert 0.3 < float(np.std(quantised)) < 0.6
    loud = tpdf_quantise(np.full((10, 1), 1.0), 16)
    assert loud.max() == 2**15 - 1


@pytest.mark.req("REQ-AUD-05")
def test_determinism_is_bit_exact_for_supercollider_and_a_null_test_for_renoise() -> None:
    from mph.bench import checks  # noqa: PLC0415

    first = np.zeros((100, 2), np.float32)
    tiny = first.copy()
    tiny[10, 0] = 1e-7  # -140 dBFS
    assert checks.determinism(first, first.copy()).passed
    assert not checks.determinism(first, tiny).passed
    nulled = checks.determinism(first, tiny, null_limit_db=-120.0)
    assert nulled.passed and nulled.value == -140.0 and nulled.note == "1 samples differ"
    loud = tiny.copy()
    loud[20, 1] = 1e-3
    assert not checks.determinism(first, loud, null_limit_db=-120.0).passed
    assert checks.determinism(first, first, null_limit_db=-120.0).note == "bit-identical"
    assert not checks.determinism(first, first[:50]).passed
    assert not checks.determinism(first, first[:50], null_limit_db=-120.0).passed


# --- tone: pitch and aliasing (BM-BASS-01, REQ-AUD-04) -----------------------------------


def harmonic_tone(freq: float, seconds: float = 0.2, harmonics: int = 10) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    wave = sum(np.sin(2 * np.pi * n * freq * t) / n for n in range(1, harmonics + 1))
    return np.stack([wave, wave], axis=1).astype(np.float32)


def test_pitch_is_found_within_the_range_asked() -> None:
    for freq in (65.41, 130.81, 523.25):
        heard = audio.pitch_hz(harmonic_tone(freq), RATE, freq / 1.4, freq * 1.4)
        assert abs(1200 * np.log2(heard / freq)) < 1  # cents
    # Searching around the wrong note finds nothing near it.
    assert audio.pitch_hz(harmonic_tone(65.41), RATE, 80, 120) not in range(64, 67)
    assert audio.pitch_hz(np.zeros((9600, 2), np.float32), RATE, 50, 100) == 0.0
    assert audio.pitch_hz(harmonic_tone(65.41, seconds=0.01), RATE, 50, 100) == 0.0


@pytest.mark.req("REQ-AUD-04")
def test_inharmonic_energy_shows_aliasing() -> None:
    clean = harmonic_tone(523.25, seconds=1.0)
    assert audio.inharmonic_db(clean, RATE, 523.25, (0.1, 0.9)) < -90
    t = np.arange(len(clean)) / RATE
    stray = 0.01 * np.sin(2 * np.pi * 7777.0 * t)  # a tone between harmonics
    dirty = clean + np.stack([stray, stray], axis=1).astype(np.float32)
    assert audio.inharmonic_db(dirty, RATE, 523.25, (0.1, 0.9)) == pytest.approx(-42, abs=3)


def test_brightness_and_level() -> None:
    dull, bright = harmonic_tone(200, harmonics=2), harmonic_tone(200, harmonics=12)
    assert audio.centroid(bright, RATE) > audio.centroid(dull, RATE)
    assert audio.rms(dull * 2) == pytest.approx(2 * audio.rms(dull))
    assert audio.rms(np.zeros((0, 2), np.float32)) == 0.0


@pytest.mark.req("REQ-BEN-05")
def test_flatness_tells_a_hiss_from_a_whistle() -> None:
    rng = np.random.default_rng(1)
    hiss = rng.uniform(-1, 1, (RATE // 10, 2)).astype(np.float32)
    whistle = harmonic_tone(9000.0, seconds=0.1, harmonics=1)
    assert audio.flatness(hiss, RATE) > 0.5
    assert audio.flatness(whistle, RATE) < 0.01
    assert audio.flatness(np.zeros((RATE, 2), np.float32), RATE) == 1.0  # silence: not tonal
    assert audio.flatness(hiss[:100], RATE) == 1.0  # too short to judge
