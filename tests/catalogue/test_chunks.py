from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from mph.catalogue import chunks
from mph.catalogue.chunks import Acid, AppleLoop, Instrument, Loop, Sampler
from tests.catalogue import audiofiles as af

FRAMES = af.RATE * 4  # a 4-second file


def parse(data: bytes) -> chunks.Chunks:
    return chunks.parse(io.BytesIO(data))


@pytest.mark.req("REQ-CAT-12")
def test_wav_acid_smpl_and_inst() -> None:
    found = parse(
        af.wav(
            4.0,
            af.riff_chunk(b"LIST", af.info(INAM="Deep Loop 01")),
            af.riff_chunk(b"acid", af.acid(root=57, beats=8, tempo=120.0)),
            af.riff_chunk(b"smpl", af.smpl(45, cents=50.0, loops=((0, FRAMES - 1),))),
            af.riff_chunk(b"inst", af.wav_inst(45, detune=-3, notes=(40, 50))),
        )
    )
    assert found.acid == Acid(one_shot=False, root_note=57, beats=8, meter=(4, 4), tempo=120.0)
    assert found.smpl == Sampler(45, 50.0, (Loop(0, FRAMES, "forward"),))  # end: after the last
    assert found.inst == Instrument(45, -3, (40, 50), (1, 127), 0)
    assert (found.basc, found.skipped) == (None, ())


@pytest.mark.req("REQ-CAT-12")
def test_acid_one_shot_and_unset_root() -> None:
    found = parse(af.wav(0.5, af.riff_chunk(b"acid", af.acid(one_shot=True, beats=0))))
    assert found.acid is not None
    assert found.acid.one_shot
    assert found.acid.root_note is None  # the flag says the root note isn't set


@pytest.mark.req("REQ-CAT-12")
def test_aiff_apple_loop_and_instrument_loops_through_markers() -> None:
    found = parse(
        af.aiff(
            4.0,
            af.aiff_chunk(b"basc", af.basc(beats=8, root=48, scale=1)),
            af.aiff_chunk(b"MARK", af.mark((1, 0, "beg loop"), (2, FRAMES, "end"))),
            af.aiff_chunk(b"INST", af.aiff_inst(60, sustain=(1, 1, 2))),
        )
    )
    assert found.basc == AppleLoop(False, 8, 48, "minor", (4, 4))
    assert found.inst is not None
    assert found.inst.sustain == Loop(0, FRAMES, "forward")  # an AIFF loop ends at its marker
    assert found.inst.release is None


def test_aifc_and_a_loop_whose_markers_are_missing() -> None:
    found = parse(
        af.aiff(1.0, af.aiff_chunk(b"INST", af.aiff_inst(sustain=(1, 1, 2))), kind=b"AIFC")
    )
    assert found.inst is not None
    assert found.inst.sustain is None


@pytest.mark.req("REQ-CAT-12")
def test_other_files_have_none_whatever_their_name() -> None:
    m4a = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 100  # Dean has an M4A named .wav
    assert parse(m4a) == chunks.Chunks()
    assert parse(b"") == chunks.Chunks()
    assert parse(b"RIFF\x04\x00\x00\x00AVI ") == chunks.Chunks()


@pytest.mark.req("REQ-CAT-12")
def test_short_and_odd_chunks_are_skipped_and_noted() -> None:
    found = parse(
        af.wav(
            1.0,
            af.riff_chunk(b"acid", b"\x00" * 12),
            af.riff_chunk(b"smpl", af.smpl(200)),  # no such MIDI note
        )
    )
    assert (found.acid, found.smpl) == (None, None)
    assert found.skipped == (
        "acid: 12 bytes, wanted at least 24",
        "smpl: unity note 200 isn't a MIDI note",
    )


def test_a_chunk_too_big_to_be_real_is_skipped() -> None:
    found = parse(af.wav(1.0, af.riff_chunk(b"smpl", b"\x00" * (chunks.MAX_CHUNK + 2))))
    assert found.skipped == (f"smpl: {chunks.MAX_CHUNK + 2} bytes, more than {chunks.MAX_CHUNK}",)


def test_a_file_cut_short_inside_a_chunk() -> None:
    whole = af.wav(1.0, af.riff_chunk(b"acid", af.acid()))
    head = 12 + 8 + 16  # RIFF header and fmt chunk
    found = parse(whole[: head + 8 + 10])
    assert found.skipped == ("acid: cut short: 10 of 24 bytes",)


@pytest.mark.req("REQ-CAT-12")
def test_an_odd_chunk_written_without_its_pad_byte() -> None:
    odd = af.riff_chunk(b"LIST", af.info(INAM="Odd") + b"x", pad=False)  # odd length, no pad
    assert len(odd) % 2 == 1
    found = parse(af.wav(4.0, odd, af.riff_chunk(b"acid", af.acid(beats=8, tempo=120.0))))
    assert found.acid is not None
    assert found.acid.tempo == 120.0


def test_the_walk_stops_at_an_unknown_size_or_garbage() -> None:
    rf64_like = af.wav(
        1.0, b"data" + struct.pack("<I", chunks.UNKNOWN_SIZE), af.riff_chunk(b"acid", af.acid())
    )
    assert parse(rf64_like).acid is None
    garbage = af.wav(1.0, b"\x01\x02\x03\x04" + struct.pack("<I", 8) + b"\x00" * 8)
    assert parse(garbage) == chunks.Chunks()


def test_a_nan_tempo_reads_as_none_and_loops_past_the_cap_are_dropped() -> None:
    nan = struct.pack("<IHHfIHHf", 0, 0, 0, 0.0, 8, 4, 4, float("nan"))
    many = tuple((n, n + 10) for n in range(chunks.MAX_LOOPS + 4))
    found = parse(
        af.wav(1.0, af.riff_chunk(b"acid", nan), af.riff_chunk(b"smpl", af.smpl(loops=many)))
    )
    assert found.acid is not None
    assert found.acid.tempo == 0.0
    assert found.smpl is not None
    assert len(found.smpl.loops) == chunks.MAX_LOOPS


def test_markers_cut_short_are_noted() -> None:
    found = parse(af.aiff(1.0, af.aiff_chunk(b"MARK", struct.pack(">H", 3) + b"\x00\x01")))
    assert found.skipped == ("MARK: cut short after 0 of 3 markers",)


@pytest.mark.req("REQ-CAT-01")
def test_read_opens_the_file_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "loop.wav"
    path.write_bytes(af.wav(1.0, af.riff_chunk(b"acid", af.acid(beats=2))))
    modes: list[str] = []
    real_open = Path.open

    def spy(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        modes.append(mode)
        return real_open(self, mode, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", spy)
    assert chunks.read(path).acid is not None
    assert modes == ["rb"]
