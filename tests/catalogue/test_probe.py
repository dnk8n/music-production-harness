from __future__ import annotations

import json
from pathlib import Path

from mph.catalogue.probe import parse, probe
from mph.proc import Result
from tests.fakes import FakeRunner


def ffprobe_json(**stream: object) -> str:
    return json.dumps({"streams": [stream], "format": {"format_name": "wav", "duration": "2.5"}})


def test_parse_pcm_wav() -> None:
    p = parse(
        ffprobe_json(codec_name="pcm_s24le", sample_rate="44100", channels=2, bits_per_sample=24)
    )
    assert (p.container, p.codec, p.sample_rate, p.bit_depth, p.channels, p.duration_s) == (
        "wav",
        "pcm_s24le",
        44100,
        24,
        2,
        2.5,
    )


def test_parse_aac_has_no_bit_depth() -> None:
    p = parse(
        ffprobe_json(
            codec_name="aac", sample_rate="48000", channels=2, bits_per_sample=0, duration="3.0"
        )
    )
    assert p.bit_depth is None
    assert p.duration_s == 3.0


def test_parse_multi_name_container() -> None:
    out = json.dumps(
        {"streams": [{"codec_name": "aac"}], "format": {"format_name": "mov,mp4,m4a,3gp"}}
    )
    assert parse(out).container == "m4a"


def test_no_audio_stream() -> None:
    assert parse('{"streams": []}').error == "no audio stream"


def test_probe_failure_and_garbage() -> None:
    bad = FakeRunner(responses={"ffprobe": Result(1, stderr="Invalid data\n")})
    assert probe(bad, Path("x.wav")).error == "Invalid data"
    garbage = FakeRunner(responses={"ffprobe": Result(0, "not json")})
    assert (probe(garbage, Path("x.wav")).error or "").startswith("unreadable")
