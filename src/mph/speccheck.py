"""Deterministic gate for the spec (ADR-0006). Spec: spec/README.md.

This is the first, cheapest gate any spec change passes, before an advisory
LLM review or a human. It checks structure, not taste:

* every requirement/benchmark ID is defined exactly once,
* every one states a **Why** and a **Verify** method,
* every ID referenced anywhere (spec, code, tests) is defined,
* every ADR declares a valid status,
* every ``Verify: test`` requirement in a ``built`` spec file is linked from a
  test (elsewhere it is reported as a warning, or an error with ``--strict``).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

ITEM_ID = r"(?:REQ|BM)-[A-Z]{2,5}-\d{2}"
HEADING = re.compile(rf"^#{{2,4}} ({ITEM_ID})\b[:\s]*(.*)$", re.MULTILINE)
REFERENCE = re.compile(rf"\b({ITEM_ID}|ADR-\d{{4}})\b")
TEST_LINK = re.compile(rf"""mark\.req\(\s*["']({ITEM_ID})["']""")
ADR_FILE = re.compile(r"^(ADR-\d{4})-[a-z0-9-]+\.md$")
STATUS = re.compile(r"^\*\*Status:\*\*\s*(\w+)", re.MULTILINE)
VALID_STATUS = frozenset({"proposed", "accepted", "superseded", "rejected"})
VERIFY_METHODS = frozenset({"test", "listen", "measure", "inspect"})


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    title: str
    file: Path
    why: bool
    verify: frozenset[str]
    built: bool = False  # the item's file declares **Status:** built


@dataclass(slots=True)
class SpecReport:
    items: dict[str, Item] = field(default_factory=dict)
    adrs: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    untested: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_items(path: Path, text: str) -> list[Item]:
    file_status = STATUS.search(text)
    built = bool(file_status and file_status.group(1).lower() == "built")
    matches = list(HEADING.finditer(text))
    items: list[Item] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        verify_line = re.search(r"\*\*Verify:\*\*\s*([^\n]*)", body)
        methods = (
            frozenset(
                word
                for word in re.findall(r"[a-z]+", verify_line.group(1).lower())
                if word in VERIFY_METHODS
            )
            if verify_line
            else frozenset()
        )
        items.append(
            Item(match.group(1), match.group(2).strip(), path, "**Why:**" in body, methods, built)
        )
    return items


def _files(root: Path, patterns: Iterable[str]) -> list[Path]:
    return sorted(p for pattern in patterns for p in root.glob(pattern) if p.is_file())


def check(repo: Path) -> SpecReport:
    report = SpecReport()
    spec_files = _files(repo / "spec", ["**/*.md"])
    all_items = [
        item
        for f in spec_files
        for item in parse_items(f, f.read_text("utf-8"))
        if not _is_placeholder(item.id)
    ]
    for item_id, count in Counter(i.id for i in all_items).items():
        if count > 1:
            report.errors.append(f"{item_id} defined {count} times")
    for item in all_items:
        report.items.setdefault(item.id, item)
        where = item.file.relative_to(repo)
        if not item.why:
            report.errors.append(f"{item.id} ({where}) has no **Why:**")
        if not item.verify:
            report.errors.append(
                f"{item.id} ({where}) needs **Verify:** with one of {sorted(VERIFY_METHODS)}"
            )
    _check_adrs(repo, report)
    _check_references(repo, spec_files, report)
    _check_traceability(repo, report)
    return report


def _check_adrs(repo: Path, report: SpecReport) -> None:
    for path in _files(repo / "spec" / "decisions", ["ADR-*.md"]):
        match = ADR_FILE.match(path.name)
        if not match:
            report.errors.append(f"{path.name}: ADR files are named ADR-NNNN-kebab-title.md")
            continue
        report.adrs.add(match.group(1))
        status = STATUS.search(path.read_text("utf-8"))
        if not status or status.group(1).lower() not in VALID_STATUS:
            report.errors.append(f"{path.name}: needs **Status:** one of {sorted(VALID_STATUS)}")


def _check_references(repo: Path, spec_files: list[Path], report: SpecReport) -> None:
    code_files = _files(repo, ["src/**/*.py", "tests/**/*.py", "*.md", ".github/**/*.md"])
    known = report.items.keys() | report.adrs
    for path in [*spec_files, *code_files]:
        for line_no, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            for ref in REFERENCE.findall(line):
                if ref not in known and not _is_placeholder(ref):
                    report.errors.append(f"{path.relative_to(repo)}:{line_no} unknown ref {ref}")


def _is_placeholder(ref: str) -> bool:
    """Examples in docs use zeroed IDs (REQ-XXX-00 style) that need not exist."""
    return ref.endswith(("-00", "-0000"))


def _check_traceability(repo: Path, report: SpecReport) -> None:
    linked = {
        m for p in _files(repo, ["tests/**/*.py"]) for m in TEST_LINK.findall(p.read_text("utf-8"))
    }
    untested = [i for i in report.items.values() if "test" in i.verify and i.id not in linked]
    report.untested = sorted(i.id for i in untested if not i.built)
    report.errors.extend(
        f"{i.id} is in a built spec but no test links it" for i in untested if i.built
    )
