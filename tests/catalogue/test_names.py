from __future__ import annotations

import pytest

from mph.catalogue.names import Prior, parse_key, priors_for


def values(rel: str, name: str) -> set[str]:
    return {p.value for p in priors_for(rel) if p.name == name}


@pytest.mark.req("REQ-CAT-03")
def test_novation_folder_key_and_bpm() -> None:
    priors = priors_for("Novation 3rd Drop/Acid Techno (Ab, 131bpm)/Drums/Kick 01.wav")
    assert Prior("bpm", "131", "folder", 0.4) in priors  # two folders up from the file
    assert Prior("key", "G#:major", "folder", 0.4) in priors
    assert Prior("role", "kick", "filename", 0.6) in priors
    assert Prior("pack", "Novation 3rd Drop", "folder", 1.0) in priors


@pytest.mark.req("REQ-CAT-03")
def test_loopmasters_bare_tempo_is_a_weak_prior() -> None:
    priors = priors_for("LM/MXE_VOCAL_LOOPS/MXE_128_Vocals_19.wav")
    assert Prior("bpm", "128", "filename", 0.3) in priors
    assert values("LM/MXE_VOCAL_LOOPS/MXE_128_Vocals_19.wav", "role") == {"vocal"}
    assert values("LM/MXE_VOCAL_LOOPS/MXE_128_Vocals_19.wav", "form") == {"loop"}


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.parametrize(
    ("tokens", "key"),
    [
        (["Ab"], "G#:major"),
        (["Dbm"], "C#:minor"),
        (["Amaj"], "A:major"),
        (["F#m"], "F#:minor"),
        (["F", "Maj7"], "F:major"),
        (["C", "Minor"], "C:minor"),
        (["Ebm"], "D#:minor"),
        (["Pad", "A"], None),
        (["6", "am"], None),
        (["Bass"], None),
        (["Cb"], "B:major"),
    ],
)
def test_parse_key(tokens: list[str], key: str | None) -> None:
    assert parse_key(tokens) == key


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.parametrize(
    ("rel", "role"),
    [
        ("p/Hats/Closed 1.wav", "hat"),
        ("p/Snares/SD 3.wav", "snare"),
        ("p/Congas/x.wav", "conga"),
        ("p/x/Sub Bass 2.wav", "bass"),
        ("p/x/Jungle Pad 4.wav", "pad"),
        ("p/x/Riser.wav", "fx"),
    ],
)
def test_roles(rel: str, role: str) -> None:
    assert role in values(rel, "role")


def test_index_numbers_are_not_tempos() -> None:
    assert values("p/Melodic/Melody 0015 [2020-08-03 010901].wav", "bpm") == set()
    assert values("p/x/Kick 999.wav", "bpm") == set()


def test_root_level_file_has_no_pack() -> None:
    assert values("onebassline.wav", "pack") == set()


def test_priors_are_sorted_and_unique() -> None:
    priors = priors_for("p/Kicks/Kick Kick.wav")
    assert priors == sorted(set(priors), key=lambda p: (p.name, -p.confidence, p.value, p.origin))


@pytest.mark.req("REQ-CAT-03")
def test_folder_confidence_decays_with_distance() -> None:
    priors = priors_for("Deep Bass Pack/Loops/Kicks/Kick 1.wav")
    by_value = {(p.value, p.origin): p.confidence for p in priors if p.name == "role"}
    assert by_value[("kick", "filename")] == 0.6
    assert by_value[("kick", "folder")] == 0.5  # directly containing folder
    assert by_value[("bass", "folder")] == 0.3  # pack name, three levels up


@pytest.mark.req("REQ-CAT-03")
def test_folder_listing_several_roles_is_split() -> None:
    priors = priors_for("LM/DRT_SOUNDS_AND_FX/DRT_Perc_01.wav")
    fx = next(p for p in priors if p.value == "fx")
    perc = next(p for p in priors if p.value == "perc" and p.origin == "filename")
    assert fx.confidence == 0.5  # "sounds" isn't a role, so fx keeps full weight
    assert perc.confidence == 0.6
    mixed = priors_for("LM/MXE_DRUM_HITS_&_FX/x.wav")
    assert {p.confidence for p in mixed if p.name == "role"} == {0.25}
