"""Rendering on demand from the listening page (REQ-RET-06).

Renders are deterministic, so mph doesn't keep old ones (REQ-RET-01). When
the page needs a render whose audio is gone, or one that was never made, it
asks for it here. One render runs at a time, in the background, and the page
polls ``status`` until it is done.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from mph.bench import score

RenderOne = Callable[[str, str], None]  # (benchmark id, variant); raises on failure
Spawn = Callable[[Callable[[], None]], None]

IDLE, RENDERING, DONE, FAILED = "idle", "rendering", "done", "failed"


class JitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class State:
    state: str = IDLE
    id: str = ""
    variant: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="mph-jit-render", daemon=True).start()


class Renderer:
    def __init__(self, render_one: RenderOne, spawn: Spawn = _thread) -> None:
        self._render_one = render_one
        self._spawn = spawn
        self._lock = threading.Lock()
        self._state = State()

    def status(self) -> State:
        with self._lock:
            return self._state

    def start(self, benchmark_id: str, variant: str) -> State:
        """Begin a render, or refuse while another one runs."""
        if benchmark_id not in score.available():
            raise JitError(f"no benchmark called {benchmark_id!r}")
        names = {v.name for v in score.all_variants(score.load(benchmark_id))}
        if variant not in names:
            raise JitError(f"{benchmark_id} has no variant called {variant!r}")
        with self._lock:
            if self._state.state == RENDERING:
                busy = self._state
                raise JitError(f"already rendering {busy.id} {busy.variant}: wait for it to finish")
            self._state = State(RENDERING, benchmark_id, variant, "rendering…")
            started = self._state
        self._spawn(lambda: self._run(benchmark_id, variant))
        return started

    def _run(self, benchmark_id: str, variant: str) -> None:
        try:
            self._render_one(benchmark_id, variant)
        except Exception as exc:  # any failure is shown on the page
            outcome = State(FAILED, benchmark_id, variant, str(exc) or type(exc).__name__)
        else:
            outcome = State(DONE, benchmark_id, variant, "rendered")
        with self._lock:
            self._state = outcome
