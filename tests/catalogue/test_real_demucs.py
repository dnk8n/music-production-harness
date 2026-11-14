"""Stems from the real Demucs (marker ``tools``; skipped without it).

``scripts/mac-check`` runs this on Dean's Mac, where Demucs is installed. The
first run downloads the model's weights (about 80 MB). CI skips it: unit
tests never touch real apps (REQ-ENG-03, REQ-ENG-06).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile

from mph.catalogue import db, provenance
from mph.catalogue.provenance import Workshop
from mph.proc import SubprocessRunner

pytestmark = pytest.mark.tools

RATE = 44100


def song(seconds: float = 12.0) -> np.ndarray:
    """A kick on every beat at 120 BPM under a held, wavering tone."""
    t = np.arange(int(seconds * RATE)) / RATE
    tone = 0.2 * np.sin(2 * np.pi * 330 * t + 2 * np.sin(2 * np.pi * 5 * t))
    out = tone.copy()
    hit = np.arange(int(0.12 * RATE)) / RATE
    kick = 0.8 * np.sin(2 * np.pi * 55 * hit) * np.exp(-hit * 18)
    for start in range(0, out.size - kick.size, RATE // 2):
        out[start : start + kick.size] += kick
    return np.stack([out, out], axis=1).astype(np.float32)


@pytest.mark.req("REQ-CAT-11")
@pytest.mark.req("REQ-AUD-05")
def test_real_stems_are_float_at_the_tracks_rate_and_rebuild_bit_exact(tmp_path: Path) -> None:
    runner = SubprocessRunner(timeout_s=900)
    if runner.which("demucs") is None:
        pytest.skip("Demucs isn't installed: mph install demucs")
    root = tmp_path / "samples" / "full_tracks"
    root.mkdir(parents=True)
    soundfile.write(root / "song.wav", song(), RATE, subtype="PCM_16")
    conn = db.connect(tmp_path / "data" / db.FILENAME)
    with conn:
        conn.execute(
            "INSERT INTO source VALUES ('dj', 'local', ?, 'x', NULL)", (str(tmp_path / "samples"),)
        )
        conn.execute(
            "INSERT INTO asset (hash, bytes, sample_rate, origin) "
            "VALUES ('song000000', 1, 44100, 'source')"
        )
        conn.execute(
            "INSERT INTO source_file VALUES ('dj', 'full_tracks/song.wav', 1, 1, 'song000000', 1)"
        )
    workshop = Workshop(conn, runner, tmp_path / "data")
    made = {
        part: workshop.derive(
            provenance.recipe("stems", {"model": "htdemucs", "part": part}), ["song000000"]
        )
        for part in provenance.parts("stems", {"model": "htdemucs"})
    }
    for stem in made.values():
        info = soundfile.info(str(stem.path))
        assert (info.samplerate, info.subtype, info.channels) == (RATE, "FLOAT", 2)
        assert info.frames == song().shape[0]
    provenance.purge_cache(tmp_path / "data")
    report = provenance.refresh(workshop, conn, tmp_path / "data")
    assert report.failed == []  # a second separation made the same bytes
    assert all(provenance.locate(conn, tmp_path / "data", m.digest) for m in made.values())
    conn.close()
