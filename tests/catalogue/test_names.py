from __future__ import annotations

import pytest

from mph.catalogue.names import Prior, parse_key, priors_for, priors_from_text


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
    priors = priors_for("Pack/Kit 1/Percussion/Loops/Shakers/Shaker 1.wav")
    by_value = {(p.value, p.origin): p.confidence for p in priors if p.name == "role"}
    assert by_value[("shaker", "filename")] == 0.6
    assert by_value[("shaker", "folder")] == 0.5  # directly containing folder
    assert by_value[("perc", "folder")] == 0.3  # three levels up
    deep = priors_for("Pack/Kit/Drums/Loops/Tops/Wet/Dry/Top 1.wav")
    assert Prior("role", "drums", "folder", 0.2) in deep  # far off, but still a little


@pytest.mark.req("REQ-CAT-03")
def test_folder_listing_several_roles_is_split() -> None:
    priors = priors_for("LM/Deep & Raw Techno/DRT_SOUNDS_AND_FX/DRT_Perc_01.wav")
    fx = next(p for p in priors if p.value == "fx")
    perc = next(p for p in priors if p.value == "perc" and p.origin == "filename")
    assert fx.confidence == 0.5  # "sounds" isn't a role, so fx keeps full weight
    assert perc.confidence == 0.6
    mixed = priors_for("LM/Maximum EDM/MXE_DRUM_HITS_&_FX/x.wav")
    assert {p.confidence for p in mixed if p.name == "role"} == {0.25}


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.parametrize(
    ("rel", "roles"),
    [
        # a genre in a product's name: the drum loops are drums, not bass
        ("LM 4GB/BASS_HOUSE_AND_GARAGE_2/BHG2_Drum_Loops/BHG2_124_Full_Loop_01.wav", {"drums"}),
        ("LM 4GB/BASS_HOUSE_AND_GARAGE_2/BHG2_124_Drum_Loop_01.wav", {"drums"}),
        # a producer's name: Tom Middleton's basses aren't toms
        ("LM 4GB/TOM MIDDLETON/Bass/TM_Bass_01.wav", {"bass"}),
        ("Novation 3rd Drop/Drum & Bass (F, 174bpm)/Synths/Lead 01.wav", {"melodic"}),
        ("Deep Bass Pack/Loops/Kicks/Kick 1.wav", {"kick"}),
        # nothing nearer names a role, so the pack's or product's name stands in
        ("Mutant Breaks/MB_Amen_120.wav", {"break"}),
        ("Photek Full/Breaks/120/Amen.wav", {"break"}),
        ("LM 4GB/Latin Bass/LB_124_Loop_01.wav", {"bass"}),
    ],
)
def test_pack_and_product_names_only_back_up_roles(rel: str, roles: set[str]) -> None:
    assert values(rel, "role") == roles


@pytest.mark.req("REQ-CAT-03")
def test_pack_and_product_names_still_give_tempo_key_and_agreeing_roles() -> None:
    kit = priors_for("Novation 3rd Drop/Deep Bass (F#m, 174bpm)/Bass/Reese 01.wav")
    assert Prior("bpm", "174", "folder", 0.4) in kit
    assert Prior("key", "F#:minor", "folder", 0.4) in kit
    assert Prior("role", "bass", "folder", 0.4) in kit  # agrees with the folder below it
    genre = priors_for("Novation 3rd Drop/Drum & Bass (F#m, 174bpm)/Bass/Reese 01.wav")
    assert {p for p in genre if p.name != "role"} == {p for p in kit if p.name != "role"}
    assert Prior("role", "bass", "folder", 0.4) not in genre  # a genre, not a sound


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.parametrize(
    ("rel", "roles"),
    [
        # Dean's packs: products sit inside a year folder, deeper than the name levels
        (
            "LM 4GB/LM_2014/BASS_HOUSE_AND_GARAGE_2/BHG2_SOUNDS_AND_FX/BHG2_Hat_17.wav",
            {"hat", "fx"},
        ),
        (
            "LM 4GB/LM_2014/BASS_HOUSE_AND_GARAGE_2/BHG2_WAV_LOOPS/BHG2_120_Cm_Chord_Synth_09.wav",
            {"chord", "melodic"},
        ),
        (
            "LM 4GB/LM_2015/TOM MIDDLETON PRESENTS DUB BASS HOUSE/LOOPS/TM2_125_C_Background.wav",
            set(),
        ),
        (
            "LM/LM_2015/TOM MIDDLETON PRESENTS DUB BASS HOUSE/SOUNDS_&_FX/TM2_Attack_Bass.wav",
            {"bass", "fx"},
        ),
        (
            "LM/LM_2015/JUNE MILLER PRESENTS DARK COMPLEX DRUM & BASS VOL 2/LOOPS/JM2_Atmos.wav",
            {"pad"},
        ),
        ("LM 4GB/LM_2014/THE_MEDIKS_DRUM_&_BASS_SURGERY/MED_Loops/MED_174_Reese_1.wav", set()),
        ("LM 4GB/LM_2016/CAPSUN_CHILL_TRAP_&_FUTURE_BASS/CT2_Mono_Synth_02.wav", {"melodic"}),
        # the credit goes, the sounds stay
        ("LM 4GB/LM_2015/LOOPMASTERS_PRESENTS_FUNK_BASS_LOOPS/FBL_100_Loop_01.wav", {"bass"}),
        # drums and a bass, not the genre
        ("Drums and Bass/Loop 01.wav", {"drums", "bass"}),
    ],
)
def test_genres_and_artist_credits_give_no_role_at_any_depth(rel: str, roles: set[str]) -> None:
    assert values(rel, "role") == roles


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.req("REQ-CAT-07")
def test_a_downloads_title_and_keywords_skip_genres_too() -> None:
    priors = priors_from_text("Drum and Bass breakbeat loop, 174 BPM")
    assert {(p.name, p.value) for p in priors} == {
        ("role", "break"),
        ("form", "loop"),
        ("bpm", "174"),
    }


@pytest.mark.req("REQ-CAT-03")
@pytest.mark.parametrize(
    ("rel", "roles"),
    [
        ("Stab Bass Sample Pack/Digital Stab 1.wav", {"stab", "bass"}),  # named for its sounds
        ("Stab Bass Sample Pack/Digital Stab 1 Bb (Processed Filter).wav", {"stab", "bass"}),
        ("voice/one take.wav", {"vocal"}),
        ("Kicks & Snares/Snare 01.wav", {"snare"}),  # a list: the file says which
        ("Mutant Breaks/GG_A Damn Kool Groove 2 168.wav", {"drums"}),  # a name: it backs up
        ("Deep Bass Pack/Loops/Kicks/Kick 1.wav", {"kick"}),
    ],
)
def test_a_pack_folder_named_only_for_its_sounds_labels_them(rel: str, roles: set[str]) -> None:
    assert values(rel, "role") == roles


@pytest.mark.req("REQ-CAT-10")
@pytest.mark.parametrize(
    "rel",
    [
        "full_tracks/pillowtalk_x_soul_clap_ft_greg_paulus_and_crew_love-love_train.flac",
        "Full Tracks/Kerri Chandler - Bar A Thym.wav",
        "dj/FULL-TRACK/Deep Loop Bass Groove.aiff",  # loop, bass and groove are a title here
    ],
)
def test_full_tracks_are_tracks_whose_names_give_no_role(rel: str) -> None:
    labels = {(p.name, p.value) for p in priors_for(rel) if p.name in ("role", "form")}
    assert labels == {("form", "track")}


@pytest.mark.req("REQ-CAT-10")
def test_a_full_tracks_name_still_gives_its_tempo_and_key() -> None:
    priors = priors_for("Full Tracks/Moodymann - Shades Of Jae (Am, 118bpm).wav")
    assert {(p.name, p.value, p.confidence) for p in priors if p.name != "pack"} == {
        ("form", "track", 0.9),
        ("key", "A:minor", 0.6),
        ("bpm", "118", 0.6),
    }
    assert ("role", "clap") in {(p.name, p.value) for p in priors_for("Packs/Soul Clap.wav")}
