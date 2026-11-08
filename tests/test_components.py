from __future__ import annotations

import pytest

from mph import components
from mph.components import Stage


@pytest.mark.req("REQ-DOC-07")
def test_registry_covers_architecture() -> None:
    ids = {c.id for c in components.REGISTRY}
    assert {
        "homebrew",
        "uv",
        "ffmpeg",
        "sox",
        "supercollider",
        "sc3-plugins",
        "tidal",
        "renoise",
        "surge-xt",
        "demucs",
        "uvr",
        "ollama",
        "gh",
    } <= ids
    assert components.by_id("renoise").min_version == "3.5"
    assert components.by_id("supercollider").min_version == "3.13"


@pytest.mark.req("REQ-DOC-07")
def test_ids_unique_and_every_component_installable() -> None:
    ids = [c.id for c in components.REGISTRY]
    assert len(ids) == len(set(ids))
    assert all(c.steps for c in components.REGISTRY)


@pytest.mark.req("REQ-DOC-06")
def test_no_step_uses_sudo_or_shell() -> None:
    for component in components.REGISTRY:
        for step in component.steps:
            assert "sudo" not in step.argv
            assert not any(tok in ("sh", "bash", "-c") for tok in step.argv)


@pytest.mark.req("REQ-DOC-06")
def test_licensed_and_gui_apps_are_manual_only() -> None:
    for cid in ("renoise", "uvr", "homebrew"):
        assert not any(s.automatable for s in components.by_id(cid).steps)


def test_select_by_stage() -> None:
    renoise = components.select([Stage.RENOISE])
    assert {c.id for c in renoise} == {"renoise", "surge-xt"}
    assert components.select() == components.REGISTRY


def test_unknown_component() -> None:
    with pytest.raises(KeyError):
        components.by_id("cubase")
