"""The checkout a render names (REQ-BEN-03)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from mph import checkout
from mph.proc import Result
from tests.fakes import FakeRunner


def git(head: str, branch: str, changed: str, code: int = 0) -> FakeRunner:
    def answer(argv: Sequence[str]) -> Result:
        if "--abbrev-ref" in argv:
            return Result(0, branch)
        if "--porcelain" in argv:
            return Result(0, changed)
        return Result(code, head)

    return FakeRunner(responses={"git": answer})


@pytest.mark.req("REQ-BEN-03")
def test_a_render_names_the_commit_branch_and_uncommitted_changes(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    runner = git("c0ffee\n", "claude/stars\n", " M src/mph/bench/render.py\n")
    assert checkout.state(runner, tmp_path) == {
        "commit": "c0ffee",
        "branch": "claude/stars",
        "uncommitted": True,
    }
    assert runner.calls[0][0] == ("git", "-C", str(tmp_path), "rev-parse", "HEAD")
    detached = git("c0ffee\n", "HEAD\n", "")
    assert checkout.state(detached, tmp_path) == {
        "commit": "c0ffee",
        "branch": "",
        "uncommitted": False,
    }


@pytest.mark.req("REQ-BEN-03")
def test_outside_a_git_checkout_there_is_nothing_to_name(tmp_path: Path) -> None:
    runner = git("c0ffee\n", "main\n", "")
    assert checkout.state(runner, tmp_path) is None  # no .git here
    assert runner.calls == []
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")  # a worktree's .git is a file
    assert checkout.state(git("", "", ""), tmp_path) is None  # git found no commit
    assert checkout.state(git("fatal: not a repo", "", "", code=128), tmp_path) is None
