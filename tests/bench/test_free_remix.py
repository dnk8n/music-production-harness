"""The free version of remix 1 (spec/tracks/love-train.md, issue #68).

Its scores load, keep remix 1's form, parts and mix, sing new lines on even
bars in the key, play nothing but synths (so every render is licence:clear),
and cut its blocks from the whole track. Real renders are measured on a
Mac or in a cloud copy (`mph bench render BM-LTFS-05`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mph.bench import kit, score
from tests.bench.test_tracks import bar_of, own, rounded, shifted

WHOLE = "BM-LTFS-05"
BLOCKS = {  # the same bars as remix 1's blocks (BM-LTDJ-01 to 04)
    "BM-LTFS-01": (25, 52),
    "BM-LTFS-02": (53, 80),
    "BM-LTFS-03": (97, 128),
    "BM-LTFS-04": (173, 204),
}
SINGERS = ("lead", "robot", "choir")
C_MAJOR = {0, 2, 4, 5, 7, 9, 11}  # pitch classes of C major and A minor


@pytest.mark.req("REQ-TRK-01")
@pytest.mark.req("REQ-MIX-03")
def test_it_keeps_remix_ones_form_parts_and_mix() -> None:
    free, remix = score.load(WHOLE), score.load("BM-LTDJ-05")
    assert (free.bars, free.bpm, free.swing, free.rate) == (
        remix.bars,
        remix.bpm,
        remix.swing,
        48000,
    )
    assert free.length_s == remix.length_s
    # The same sections, but the dub is split where the machine's bridge comes in.
    assert [(s.name, s.first) for s in free.sections if s.first != 84] == [
        (s.name, s.first) for s in remix.sections
    ]
    for name in ("bass", "organ", "stabs", "strings_a", "strings_b"):
        assert free.voice(name) == remix.voice(name), name
    assert (free.sends, free.master, free.sidechain) == (remix.sends, remix.master, remix.sidechain)
    # Every drum plays remix 1's pattern, on a 909-style synth.
    for name in ("kick", "clap", "chh", "ohh", "ride", "rim", "shaker"):
        drum, was = free.voice(name), remix.voice(name)
        assert (drum.grid, drum.chokes, drum.sound.role) == (was.grid, was.chokes, was.sound.role)
        assert drum.sound.synth.startswith("mph_") and not drum.sound.hash


@pytest.mark.req("REQ-MST-04")
@pytest.mark.req("REQ-BEN-05")
@pytest.mark.parametrize("benchmark", [WHOLE, *BLOCKS])
def test_every_sound_is_a_synth_so_every_render_is_clear(benchmark: str, tmp_path: Path) -> None:
    loaded = score.load(benchmark)
    choices = kit.choose(loaded, None, tmp_path)
    assert all(not c.is_sample and c.licence == kit.OWN for c in choices)
    assert not any(v.clips for v in loaded.voices)  # no recordings
    assert kit.licence_marker(choices) == "licence:clear"


@pytest.mark.req("REQ-TRK-08")
@pytest.mark.req("REQ-BEN-19")
def test_its_lines_are_cued_on_even_bars_and_stay_in_the_key() -> None:
    free = score.load(WHOLE)
    for name in (*SINGERS, "horn"):
        voice = free.voice(name)
        assert voice.phrases and voice.sound.synth
        assert all(bar % 2 == 0 for bar, _ in voice.cues), name  # on its own chords
    found = score.events(free)
    for name in SINGERS:
        chord = free.voice(name).chord or (0,)
        sung = {(e.pitch + k) % 12 for e in own(found, name) for k in chord}
        assert sung <= C_MAJOR, (name, sung - C_MAJOR)
        assert all(e.syllable for e in own(found, name))
    # The horn alone is set against the harmony: E major, as the song's horn is.
    horn = own(found, "horn")
    assert [(e.pitch, round(e.length_s, 3)) for e in horn] == [(52, 7.5)] * 3
    assert free.voice("horn").chord == (0, 4, 7)
    assert [bar for bar, _ in free.voice("horn").cues] == [34, 110, 194]


@pytest.mark.req("REQ-TRK-03")
@pytest.mark.req("REQ-TRK-08")
@pytest.mark.parametrize("block", sorted(BLOCKS))
def test_its_blocks_are_cut_from_the_whole_track(block: str) -> None:
    whole, part = score.load(WHOLE), score.load(block)
    first, last = BLOCKS[block]
    assert part.excerpt == score.Excerpt(WHOLE, first, last)
    assert (part.bars, part.lead_bars, part.project) == (
        last - first + 1,
        first - 1,
        "Love Train remix 1, free: synthesised",
    )
    # Every voice, the sung ones too, plays what the track plays in those bars:
    # no note of theirs is held over a block's first bar.
    found, cut = score.events(whole), score.events(part)
    for voice in whole.voices:
        inside = [e for e in own(found, voice.name) if first <= bar_of(e, voice) <= last]
        assert rounded(own(cut, voice.name)) == shifted(inside, first - 1, 16, whole.bar_s), (
            voice.name
        )
