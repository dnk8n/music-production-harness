"""External tools the harness depends on, as data. Spec: spec/components/doctor.md.

Each component knows how to detect itself and how to get installed. Install
plans are printed by default and only executed with explicit consent; steps
that need a human (licensed apps, GUI installers) are always manual. Nothing
here ever uses sudo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from mph.platform_info import Host, bundle_version, parse_version
from mph.proc import Runner


class Stage(StrEnum):
    """Pipeline stage a component serves; doctor can be scoped to one stage."""

    CORE = "core"
    CATALOGUE = "catalogue"
    SYNTHESIS = "synthesis"
    VOCALS = "vocals"
    RENOISE = "renoise"
    MASTERING = "mastering"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class Detection:
    found: bool
    version: str | None = None
    location: str | None = None


@dataclass(frozen=True, slots=True)
class Context:
    runner: Runner
    host: Host


class Detector(Protocol):
    def detect(self, ctx: Context) -> Detection: ...


@dataclass(frozen=True, slots=True)
class Binary:
    """An executable on PATH (or one of ``fallbacks``), optionally queried for a version."""

    name: str
    version_argv: tuple[str, ...] = ()
    fallbacks: tuple[str, ...] = ()

    def detect(self, ctx: Context) -> Detection:
        location = ctx.runner.which(self.name)
        if location is None:
            found = ctx.host.find(*self.fallbacks) if self.fallbacks else None
            location = str(found) if found else None
        if location is None:
            return Detection(False)
        version = None
        if self.version_argv:
            result = ctx.runner.run([location, *self.version_argv])
            text = (result.stdout or result.stderr).strip()
            version = ".".join(map(str, parse_version(text))) or None
        return Detection(True, version, location)


@dataclass(frozen=True, slots=True)
class Bundle:
    """A macOS .app / plug-in bundle at one of several conventional paths."""

    candidates: tuple[str, ...]

    def detect(self, ctx: Context) -> Detection:
        if not ctx.host.is_macos:
            return Detection(False)
        found = ctx.host.find(*self.candidates)
        if found is None:
            return Detection(False)
        return Detection(True, bundle_version(found), str(found))


@dataclass(frozen=True, slots=True)
class Probe:
    """Run a command; success means present. Used for library-style installs (Tidal)."""

    argv: tuple[str, ...]

    def detect(self, ctx: Context) -> Detection:
        if ctx.runner.which(self.argv[0]) is None:
            return Detection(False)
        result = ctx.runner.run(self.argv)
        return Detection(result.ok, location=self.argv[0] if result.ok else None)


@dataclass(frozen=True, slots=True)
class Step:
    """One install step. ``argv`` steps can be automated; others are instructions."""

    description: str
    argv: tuple[str, ...] = ()

    @property
    def automatable(self) -> bool:
        return bool(self.argv)


@dataclass(frozen=True, slots=True)
class Component:
    id: str
    name: str
    stage: Stage
    purpose: str
    detector: Detector
    required: bool = False
    min_version: str | None = None
    steps: tuple[Step, ...] = field(default=())
    docs: str = ""

    def version_ok(self, detection: Detection) -> bool:
        if self.min_version is None or detection.version is None:
            return True
        return parse_version(detection.version) >= parse_version(self.min_version)


def brew(formula: str, *, cask: bool = False) -> Step:
    argv = ("brew", "install", "--cask", formula) if cask else ("brew", "install", formula)
    return Step(f"Install {formula} with Homebrew", argv)


def manual(description: str) -> Step:
    return Step(description)


SC_APP = "/Applications/SuperCollider.app"

REGISTRY: tuple[Component, ...] = (
    Component(
        "homebrew",
        "Homebrew",
        Stage.CORE,
        "Installs most other components",
        Binary("brew", ("--version",), ("/opt/homebrew/bin/brew",)),
        required=True,
        steps=(manual("Install from https://brew.sh (needs your admin password once)"),),
        docs="https://brew.sh",
    ),
    Component(
        "uv",
        "uv",
        Stage.CORE,
        "Python toolchain: runs mph and installs Python audio tools",
        Binary("uv", ("--version",)),
        required=True,
        steps=(brew("uv"),),
        docs="https://docs.astral.sh/uv/",
    ),
    Component(
        "ffmpeg",
        "FFmpeg",
        Stage.CATALOGUE,
        "Decodes every source format; ffprobe reads metadata",
        Binary("ffprobe", ("-version",)),
        required=True,
        steps=(brew("ffmpeg"),),
    ),
    Component(
        "sox",
        "SoX (with libsoxr)",
        Stage.MASTERING,
        "The single sample-rate conversion and dither step at delivery (ADR-0003)",
        Binary("sox", ("--version",)),
        required=True,
        steps=(brew("sox"),),
    ),
    Component(
        "supercollider",
        "SuperCollider",
        Stage.SYNTHESIS,
        "Deterministic non-realtime synthesis of drums, 303 and FX stems",
        Bundle((SC_APP,)),
        required=True,
        min_version="3.13",
        steps=(brew("supercollider", cask=True),),
        docs="https://supercollider.github.io",
    ),
    Component(
        "sc3-plugins",
        "sc3-plugins",
        Stage.SYNTHESIS,
        "Extra UGens (analog-modelled filters)",
        Bundle(("~/Library/Application Support/SuperCollider/Extensions/SC3plugins",)),
        steps=(
            manual(
                "Download from https://supercollider.github.io/sc3-plugins/ and copy "
                "SC3plugins into ~/Library/Application Support/SuperCollider/Extensions"
            ),
        ),
    ),
    Component(
        "tidal",
        "Tidal Cycles",
        Stage.SYNTHESIS,
        "Optional live-coding pattern layer",
        Probe(("ghc", "-package", "tidal", "-e", "return ()")),
        steps=(
            brew("ghcup"),
            Step("Install GHC", ("ghcup", "install", "ghc", "recommended")),
            Step("Install cabal", ("ghcup", "install", "cabal", "recommended")),
            Step("Install the Tidal library", ("cabal", "v2-install", "tidal", "--lib")),
            manual('In SuperCollider run: Quarks.install("SuperDirt"); thisProcess.recompile'),
        ),
        docs="https://tidalcycles.org/docs/getting-started/macos_install",
    ),
    Component(
        "renoise",
        "Renoise",
        Stage.RENOISE,
        "Final arrangement, live performance and render",
        Bundle(("/Applications/Renoise.app",)),
        required=True,
        min_version="3.5",
        steps=(
            manual("Licensed app: download from your account at https://backstage.renoise.com"),
        ),
    ),
    Component(
        "surge-xt",
        "Surge XT",
        Stage.RENOISE,
        "Free open-source synth used in the Renoise template",
        Bundle(
            (
                "/Library/Audio/Plug-Ins/Components/Surge XT.component",
                "~/Library/Audio/Plug-Ins/Components/Surge XT.component",
            )
        ),
        steps=(brew("surge-xt", cask=True),),
    ),
    Component(
        "demucs",
        "Demucs",
        Stage.VOCALS,
        "Separates vocals from full mixes",
        Binary("demucs"),
        steps=(Step("Install as an isolated tool", ("uv", "tool", "install", "demucs")),),
    ),
    Component(
        "uvr",
        "Ultimate Vocal Remover",
        Stage.VOCALS,
        "Optional GUI alternative to Demucs",
        Bundle(("/Applications/Ultimate Vocal Remover.app",)),
        steps=(
            manual("Download from https://github.com/Anjok07/ultimatevocalremovergui/releases"),
        ),
    ),
    Component(
        "ollama",
        "Ollama",
        Stage.AI,
        "Local fallback LLM for advisory reviews",
        Binary("ollama", ("--version",)),
        steps=(brew("ollama"), Step("Pull the default model", ("ollama", "pull", "gemma3:4b"))),
    ),
    Component(
        "gh",
        "GitHub CLI",
        Stage.AI,
        "Turns listening verdicts into issues",
        Binary("gh", ("--version",)),
        steps=(brew("gh"),),
    ),
)


def by_id(component_id: str) -> Component:
    for component in REGISTRY:
        if component.id == component_id:
            return component
    raise KeyError(component_id)


def select(stages: Sequence[Stage] = ()) -> tuple[Component, ...]:
    return tuple(c for c in REGISTRY if not stages or c.stage in stages)
