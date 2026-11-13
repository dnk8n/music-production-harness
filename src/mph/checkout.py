"""The git checkout mph runs from, for render reports to name (REQ-BEN-03).

Threads render from the branches they work on (REQ-QRY-09), so each report
names the commit it was rendered from, its branch, and whether the checkout
had uncommitted changes. That links a rating to the change that answered it
(REQ-FBK-06). Git runs through the ``Runner`` seam (REQ-ENG-03), and outside
a git checkout there is nothing to name.
"""

from __future__ import annotations

from pathlib import Path

from mph.proc import Runner

ROOT = Path(__file__).resolve().parents[2]  # src/mph/checkout.py -> the repo


def state(runner: Runner, root: Path = ROOT) -> dict[str, object] | None:
    """The commit, branch ("" when detached) and whether anything tracked is
    changed but not committed; None when ``root`` isn't a git checkout."""
    if not (root / ".git").exists():  # a folder, or a file in a worktree
        return None
    git = ["git", "-C", str(root)]
    head = runner.run([*git, "rev-parse", "HEAD"])
    commit = head.stdout.strip() if head.ok else ""
    if not commit:
        return None
    branch = runner.run([*git, "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    changed = runner.run([*git, "status", "--porcelain", "--untracked-files=no"]).stdout
    return {
        "commit": commit,
        "branch": "" if branch == "HEAD" else branch,
        "uncommitted": bool(changed.strip()),
    }
