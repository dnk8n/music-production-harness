"""A pass is a pass (spec/components/benchmarks.md: REQ-BEN-14, REQ-BEN-15).

A take renders again only when something about it is new: it was never
rendered, what shapes it changed, it failed, or Dean's notes on it are still
to act on. When a take he passed does render again, its voices keep the
sounds he heard (``kit.Kept``), so a better catalogue search can't swap them
out from under his verdict.

What shapes a take is one digest, ``inputs``, written into every report: its
score, the engine, the audio settings and the code that renders and measures
it. Kit choice and search are left out on purpose, because a passed take
keeps its sounds whatever they would pick now.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from mph.bench import kit
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


def _what_changed(report: dict[str, Any] | None, current: str) -> str:
    """Why a take's newest render no longer stands for it, or "" if it does."""
    if report is None:
        return "never rendered"
    recorded = report.get("inputs")
    if recorded is None:
        return "rendered before mph recorded what shapes a render"
    if recorded != current:
        return "its score, engine, audio settings or render code changed"
    return "" if report.get("passed") else "a measurement failed"


def _what_dean_said(verdict: dict[str, str] | None) -> Decision:
    if verdict is None:
        return Decision(False, "waiting for your verdict")
    if verdict["verdict"] != "pass":
        return Decision(True, f"your verdict is {verdict['verdict']}")
    if notes := str(verdict.get("notes") or "").strip():
        return Decision(True, f"your notes are still to act on: {notes}")
    return Decision(False, "passed, and nothing changed")


def decide(
    report: dict[str, Any] | None,
    verdict: dict[str, str] | None,
    current: str,
    *,
    force: bool = False,
) -> Decision:
    """Whether a take renders, from its newest render and the verdict that stands."""
    if force:
        return Decision(True, "asked to (--force-rerender)")
    if why := _what_changed(report, current):
        return Decision(True, why)
    return _what_dean_said(verdict)


def kept_sounds(
    report: dict[str, Any] | None, verdict: dict[str, str] | None
) -> dict[str, kit.Kept]:
    """The catalogue sounds a take keeps (REQ-BEN-15): only a passed take keeps any."""
    if report is None or verdict is None or verdict["verdict"] != "pass":
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
