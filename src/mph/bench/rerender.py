"""A pass is a pass (spec/components/benchmarks.md: REQ-BEN-14, REQ-BEN-15;
spec/components/feedback.md: REQ-FBK-06).

A take renders again only when a render could come out different and its
rating doesn't hold it. A resolved take waits for ``--force-rerender``. Any
other renders once something that shapes it changed since its newest render,
and otherwise is skipped: renders are deterministic, so the same inputs make
the same file. When a take rated a pass does render again, its voices keep
the sounds Dean heard (``kit.Kept``), so a better catalogue search can't swap
them out from under his rating.

What shapes a take is one digest, ``inputs``, written into every report: its
score, the engine, the audio settings and the code that renders and measures
it. Kit choice and search are left out on purpose, because a passed take
keeps its sounds whatever they would pick now. For a take that chooses its
sounds afresh, the caller compares what it would choose now
(``changed_sounds``), so a new sample or a better search still reaches it.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from mph.bench import kit, ratings
from mph.bench.ratings import Rating
from mph.bench.score import Score

# Inside the mph package: the code that renders a take and measures it.
CODE = (
    "bench/audio.py",
    "bench/checks.py",
    "bench/levels.py",
    "bench/osc.py",
    "bench/render.py",
    "bench/sc.py",
    "bench/score.py",
    "bench/synthdefs.scd",
    "deliver.py",
    "renoise/luadata.py",
    "renoise/plan.py",
    "renoise/tool/com.mph.bridge.xrnx/build.lua",
    "renoise/tool/com.mph.bridge.xrnx/json.lua",
    "renoise/tool/com.mph.bridge.xrnx/main.lua",
)
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True, slots=True)
class Decision:
    render: bool
    why: str


def _docstring(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def shape(name: str, text: str) -> str:
    """What a source file does. Python loses its comments, docstrings and layout,
    so tidying the code doesn't count as a change."""
    if not name.endswith(".py"):
        return text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, DOCUMENTED) and node.body and _docstring(node.body[0]):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree)


def _digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True)
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


@cache
def code_digest() -> str:
    root = resources.files("mph")
    return _digest({name: shape(name, root.joinpath(name).read_text("utf-8")) for name in CODE})


def inputs(score: Score, engine: str, audio: dict[str, Any]) -> str:
    """Everything that shapes a take except the sounds chosen for it (REQ-BEN-14)."""
    return _digest(
        {"score": score.digest(), "engine": engine, "audio": audio, "code": code_digest()}
    )


def _what_changed(report: Mapping[str, Any], current: str) -> str:
    """Why the take's newest render no longer shows what it would make now, or ""."""
    recorded = report.get("inputs")
    if recorded is None:
        return "rendered before mph recorded what shapes a render"
    if recorded != current:
        return "its score, engine, audio settings or render code changed"
    return ""


def _stars(rating: Rating) -> str:
    return f"{rating.stars} star" + ("" if rating.stars == 1 else "s")


def chooses_afresh(rating: Rating | None) -> bool:
    """A take not rated a pass chooses its sounds again on each render (REQ-BEN-15)."""
    return rating is None or not rating.passed


def changed_sounds(report: Mapping[str, Any], choices: Sequence[kit.Choice]) -> str:
    """Which voices would now get another sound than the newest render used, or ""
    if none would. A sample is the same sound by its digest, wherever it lives;
    a synth voice by its synth's name."""
    used = {
        str(entry.get("voice")): str(entry.get("hash") or entry.get("sound") or "")
        for entry in report.get("kit", [])
    }
    moved = [c.voice for c in choices if used.get(c.voice) != (c.digest or c.label)]
    return f"it would choose other sounds now ({', '.join(moved)})" if moved else ""


def _waiting(rating: Rating | None) -> Decision:
    if rating is None:
        return Decision(False, "not rated yet, and nothing changed since its newest render")
    return Decision(
        False, f"unresolved ({_stars(rating)}), waiting for a change: nothing changed yet"
    )


def decide(
    report: Mapping[str, Any] | None,
    rating: Rating | None,
    current: str,
    *,
    sounds: Callable[[], str] | None = None,
    force: bool = False,
) -> Decision:
    """Whether a take renders, from its newest render, the rating that stands for
    it and its ``inputs`` now (REQ-BEN-14). ``sounds`` says which sounds it would
    choose differently now (``changed_sounds``); it is asked only when the
    answer matters, because choosing searches the catalogue."""
    if force:
        return Decision(True, "asked to (--force-rerender)")
    if report is None:
        return Decision(True, "never rendered")
    if rating is not None and rating.state == ratings.RESOLVED:
        return Decision(False, f"resolved ({_stars(rating)}), so only --force-rerender renders it")
    if why := _what_changed(report, current):
        return Decision(True, why)
    if chooses_afresh(rating) and sounds is not None and (why := sounds()):
        return Decision(True, why)
    return _waiting(rating)


def kept_sounds(report: Mapping[str, Any] | None, rating: Rating | None) -> dict[str, kit.Kept]:
    """The catalogue sounds a take keeps (REQ-BEN-15): only one rated a pass keeps any."""
    if report is None or chooses_afresh(rating):
        return {}
    return {
        str(entry["voice"]): kit.Kept(
            digest=str(entry["hash"]),
            label=str(entry.get("sound", "")),
            reason=str(entry.get("why", "")),
            render=str(report.get("score_hash", "")),
            request=entry.get("request"),
        )
        for entry in report.get("kit", [])
        if entry.get("hash")  # synth voices are made, not chosen
    }
