from __future__ import annotations

import json
from pathlib import Path

import pytest

from mph.catalogue.probe import Picture, parse, probe
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


@pytest.mark.req("REQ-CAT-12")
def test_tags_come_from_the_file_and_its_audio_stream() -> None:
    out = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "vorbis",
                    "tags": {"encoder": "libvorbis", "title": "Stream title", "TBPM": 120},
                }
            ],
            "format": {"format_name": "ogg", "tags": {"title": "Love Train"}},
        }
    )
    assert parse(out).tags == {"encoder": "libvorbis", "title": "Love Train", "TBPM": "120"}


@pytest.mark.req("REQ-CAT-12")
def test_cover_art_is_noted_but_its_tags_and_stream_are_not_the_sound() -> None:
    out = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 1400,
                    "height": 1400,
                    "disposition": {"attached_pic": 1},
                    "tags": {"comment": "Cover (front)"},
                },
                {
                    "codec_type": "audio",
                    "codec_name": "flac",
                    "sample_rate": "44100",
                    "channels": 2,
                    "disposition": {"attached_pic": 0},
                },
            ],
            "format": {"format_name": "flac", "duration": "337.53", "tags": {"album": "X"}},
        }
    )
    found = parse(out)
    assert (found.codec, found.sample_rate, found.duration_s) == ("flac", 44100, 337.53)
    assert found.picture == Picture("mjpeg", 1400, 1400)
    assert found.tags == {"album": "X"}


def test_a_file_with_only_a_picture_has_no_audio() -> None:
    out = json.dumps({"streams": [{"codec_type": "video", "codec_name": "png"}], "format": {}})
    assert parse(out).error == "no audio stream"
    assert parse(json.dumps({"streams": [{"codec_type": "audio"}]})).picture is None
