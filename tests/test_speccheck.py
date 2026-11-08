from __future__ import annotations

from pathlib import Path

import pytest

from mph import speccheck

# Fixture IDs are assembled at runtime so the real repo's spec check (which
# scans tests/ for references) doesn't mistake them for dangling references.
R1, R2, BM1, ADR9 = "REQ-" + "ABC-01", "REQ-" + "ABC-02", "BM-" + "ABC-01", "ADR-" + "0009"
MARK = 'pytest.mark.req("' + R1 + '")'

GOOD_ITEM = f"""### {R1}: Thing
Statement.
- **Why:** because.
- **Verify:** test
"""


def make_repo(tmp_path: Path, spec: str, test: str = "", adr: str | None = None) -> Path:
    (tmp_path / "spec" / "decisions").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "spec" / "a.md").write_text(spec)
    (tmp_path / "tests" / "test_x.py").write_text(test)
    if adr is not None:
        (tmp_path / "spec" / "decisions" / "ADR-0001-x.md").write_text(adr)
    return tmp_path


@pytest.mark.req("REQ-PRC-01")
def test_clean_spec_passes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, GOOD_ITEM, MARK, "**Status:** accepted\n")
    report = speccheck.check(repo)
    assert report.ok, report.errors
    assert report.untested == []
    assert report.adrs == {"ADR-0001"}


@pytest.mark.req("REQ-PRC-01")
def test_missing_why_and_verify(tmp_path: Path) -> None:
    report = speccheck.check(make_repo(tmp_path, f"### {R1}: Thing\nNo reasons.\n"))
    assert any("no **Why:**" in e for e in report.errors)
    assert any("needs **Verify:**" in e for e in report.errors)


@pytest.mark.req("REQ-PRC-01")
def test_duplicate_ids(tmp_path: Path) -> None:
    report = speccheck.check(make_repo(tmp_path, GOOD_ITEM + GOOD_ITEM))
    assert any("defined 2 times" in e for e in report.errors)


@pytest.mark.req("REQ-PRC-01")
def test_dangling_reference(tmp_path: Path) -> None:
    report = speccheck.check(make_repo(tmp_path, GOOD_ITEM + f"See {R2} and {ADR9}.\n"))
    assert sum("unknown ref" in e for e in report.errors) == 2


@pytest.mark.req("REQ-PRC-01")
def test_placeholder_refs_allowed(tmp_path: Path) -> None:
    report = speccheck.check(make_repo(tmp_path, GOOD_ITEM + "Format: REQ-XXX-00\n", MARK))
    assert report.ok, report.errors


@pytest.mark.req("REQ-PRC-01")
@pytest.mark.parametrize(
    ("name", "body", "message"),
    [
        ("ADR-0001-x.md", "no status", "needs **Status:**"),
        ("ADR-0001-x.md", "**Status:** maybe", "needs **Status:**"),
        ("ADR-1-Bad.md", "**Status:** accepted", "named ADR-NNNN"),
    ],
)
def test_adr_rules(tmp_path: Path, name: str, body: str, message: str) -> None:
    repo = make_repo(tmp_path, GOOD_ITEM)
    (repo / "spec" / "decisions" / name).write_text(body)
    assert any(message in e for e in speccheck.check(repo).errors)


@pytest.mark.req("REQ-PRC-02")
def test_untested_requirement_reported(tmp_path: Path) -> None:
    report = speccheck.check(make_repo(tmp_path, GOOD_ITEM))
    assert report.untested == [R1]
    assert report.ok  # untested is a warning unless --strict


@pytest.mark.req("REQ-PRC-02")
def test_listen_only_items_need_no_test(tmp_path: Path) -> None:
    spec = f"### {BM1}: Groove\n- **Why:** x\n- **Verify:** listen, measure\n"
    assert speccheck.check(make_repo(tmp_path, spec)).untested == []


def test_verify_methods_parsed() -> None:
    items = speccheck.parse_items(Path("a.md"), GOOD_ITEM.replace("test", "Measure, listen"))
    assert items[0].verify == frozenset({"measure", "listen"})


@pytest.mark.req("REQ-PRC-02")
def test_untested_item_in_built_spec_is_an_error(tmp_path: Path) -> None:
    report = speccheck.check(make_repo(tmp_path, "**Status:** built\n\n" + GOOD_ITEM))
    assert not report.ok
    assert any("built spec but no test" in e for e in report.errors)
