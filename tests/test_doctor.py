from __future__ import annotations

from pathlib import Path

import pytest

from mph import components, config, doctor
from mph.components import Binary, Bundle, Component, Context, Probe, Stage
from mph.doctor import Status
from mph.platform_info import Host, parse_version
from mph.proc import Result
from tests.conftest import make_bundle
from tests.fakes import FakeRunner, FakeStore


def cfg_with_source(tmp_path: Path, source: Path) -> config.Config:
    return config.parse(
        {
            **_raw_defaults(),
            "paths": {"data_dir": str(tmp_path / "data")},
            "sources": [{"id": "dj", "kind": "local", "path": str(source)}],
        }
    )


def _raw_defaults() -> dict[str, object]:
    return config.deep_merge(config._defaults(), {})


def by_check(report: doctor.Report, check: str) -> doctor.Finding:
    return next(f for f in report.findings if f.check == check)


@pytest.mark.req("REQ-DOC-01")
def test_doctor_reports_sources_data_dir_components_secrets(tmp_path: Path, mac_host: Host) -> None:
    (tmp_path / "samples").mkdir()
    cfg = cfg_with_source(tmp_path, tmp_path / "samples")
    report = doctor.run(cfg, Context(FakeRunner(), mac_host), FakeStore())
    checks = {f.check for f in report.findings}
    assert {"data_dir", "source:dj", "component:renoise", "secret:freesound_api_key"} <= checks
    assert by_check(report, "source:dj").status is Status.OK
    assert by_check(report, "data_dir").status is Status.OK


@pytest.mark.req("REQ-DOC-01")
def test_doctor_does_not_create_data_dir(tmp_path: Path, mac_host: Host) -> None:
    (tmp_path / "samples").mkdir()
    cfg = cfg_with_source(tmp_path, tmp_path / "samples")
    doctor.run(cfg, Context(FakeRunner(), mac_host), FakeStore())
    assert not cfg.data_dir.exists()


@pytest.mark.req("REQ-DOC-02")
def test_every_problem_has_a_fix(tmp_path: Path, mac_host: Host) -> None:
    cfg = cfg_with_source(tmp_path, tmp_path / "missing")
    report = doctor.run(cfg, Context(FakeRunner(), mac_host), FakeStore())
    problems = [f for f in report.findings if f.status in (Status.WARN, Status.FAIL)]
    assert problems
    assert all(f.fix for f in problems)


@pytest.mark.req("REQ-DOC-03")
def test_missing_required_fails_missing_optional_warns(tmp_path: Path, mac_host: Host) -> None:
    report = doctor.run(config.load(), Context(FakeRunner(), mac_host), FakeStore())
    assert by_check(report, "component:renoise").status is Status.FAIL
    assert by_check(report, "component:uvr").status is Status.WARN
    assert by_check(report, "secret:gemini_api_key").status is Status.WARN
    assert not report.healthy


@pytest.mark.req("REQ-DOC-03")
def test_version_below_minimum_fails(mac_host: Host) -> None:
    make_bundle(mac_host.roots[0], "/Applications/Renoise.app", "3.4.4")
    finding = doctor.check_component(components.by_id("renoise"), Context(FakeRunner(), mac_host))
    assert finding.status is Status.FAIL
    assert "< 3.5" in finding.detail


@pytest.mark.req("REQ-DOC-03")
def test_current_renoise_passes(mac_host: Host) -> None:
    make_bundle(mac_host.roots[0], "/Applications/Renoise.app", "3.5.4")
    finding = doctor.check_component(components.by_id("renoise"), Context(FakeRunner(), mac_host))
    assert finding.status is Status.OK
    assert "3.5.4" in finding.detail


@pytest.mark.req("REQ-DOC-04")
def test_stage_scoping(mac_host: Host) -> None:
    report = doctor.run(
        config.load(), Context(FakeRunner(), mac_host), FakeStore(), [Stage.RENOISE]
    )
    checks = {f.check for f in report.findings}
    assert "component:renoise" in checks
    assert "component:ffmpeg" not in checks
    assert "data_dir" not in checks
    assert not any(c.startswith("secret:") for c in checks)


def test_secrets_skipped_without_keychain(mac_host: Host) -> None:
    report = doctor.run(config.load(), Context(FakeRunner(), mac_host), None)
    assert by_check(report, "secrets").status is Status.SKIP


def test_no_sources_warns(mac_host: Host) -> None:
    report = doctor.run(config.load(), Context(FakeRunner(), mac_host), FakeStore())
    assert by_check(report, "sources").status is Status.WARN


def test_unwritable_data_dir_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config.parse({**_raw_defaults(), "paths": {"data_dir": str(tmp_path / "d")}})
    monkeypatch.setattr("os.access", lambda *_: False)
    assert doctor.check_data_dir(cfg).status is Status.FAIL


def test_unreadable_source_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "s").mkdir()
    monkeypatch.setattr("os.access", lambda *_: False)
    source = config.Source("s", "local", str(tmp_path / "s"), "x")
    assert doctor.check_source(source).status is Status.FAIL


def test_remote_source_ok() -> None:
    finding = doctor.check_source(config.Source("ia", "remote", "https://archive.org", "pd"))
    assert finding.status is Status.OK


def test_present_secret_ok(mac_host: Host) -> None:
    findings = list(doctor.check_secrets(FakeStore({"freesound_api_key"})))
    assert findings[0].status is Status.OK


# --- detectors -------------------------------------------------------------


def test_binary_detector_parses_version(mac_host: Host) -> None:
    runner = FakeRunner(
        on_path={"sox": "/opt/homebrew/bin/sox"},
        responses={"sox": Result(0, "sox:      SoX v14.4.2\n")},
    )
    found = Binary("sox", ("--version",)).detect(Context(runner, mac_host))
    assert (found.found, found.version, found.location) == (True, "14.4.2", "/opt/homebrew/bin/sox")


def test_binary_detector_uses_fallback_path(mac_host: Host) -> None:
    (mac_host.roots[0] / "opt/homebrew/bin").mkdir(parents=True)
    (mac_host.roots[0] / "opt/homebrew/bin/brew").write_text("")
    found = Binary("brew", (), ("/opt/homebrew/bin/brew",)).detect(Context(FakeRunner(), mac_host))
    assert found.found


def test_binary_detector_missing(mac_host: Host) -> None:
    assert not Binary("nope").detect(Context(FakeRunner(), mac_host)).found


def test_bundle_detector_only_on_macos(tmp_path: Path) -> None:
    linux = Host("Linux", "x86_64", tmp_path, (tmp_path,))
    make_bundle(tmp_path, "/Applications/X.app", "1.0")
    assert not Bundle(("/Applications/X.app",)).detect(Context(FakeRunner(), linux)).found


def test_bundle_in_home(mac_host: Host) -> None:
    make_bundle(mac_host.home, "Library/Audio/Plug-Ins/Components/Surge XT.component", None)
    found = components.by_id("surge-xt").detector.detect(Context(FakeRunner(), mac_host))
    assert found.found
    assert found.version is None


def test_probe_detector(mac_host: Host) -> None:
    probe = Probe(("ghc", "-package", "tidal", "-e", "return ()"))
    assert not probe.detect(Context(FakeRunner(), mac_host)).found
    runner = FakeRunner(on_path={"ghc": "/x/ghc"}, responses={"ghc": Result(1)})
    assert not probe.detect(Context(runner, mac_host)).found
    runner = FakeRunner(on_path={"ghc": "/x/ghc"})
    assert probe.detect(Context(runner, mac_host)).found


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3.5.4", (3, 5, 4)),
        ("Renoise 3.5.4 (arm64)", (3, 5, 4)),
        ("ffprobe version 7.1", (7, 1)),
        ("no digits", ()),
    ],
)
def test_parse_version(text: str, expected: tuple[int, ...]) -> None:
    assert parse_version(text) == expected


def test_unknown_version_passes_minimum() -> None:
    component = Component("x", "X", Stage.CORE, "p", Binary("x"), min_version="2.0")
    assert component.version_ok(components.Detection(True, None))
