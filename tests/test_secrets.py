from __future__ import annotations

import pytest

from mph.proc import Result
from mph.secrets import KNOWN_SECRETS, SECURITY, KeychainStore, UnknownSecretError, spec_for
from tests.fakes import FakeRunner


@pytest.mark.req("REQ-SEC-01")
def test_get_reads_keychain_item_under_service() -> None:
    runner = FakeRunner(responses={"security": Result(0, "s3cret\n")})
    store = KeychainStore(runner, "mph")
    assert store.get("freesound_api_key") == "s3cret"
    argv, _ = runner.calls[0]
    assert argv == (SECURITY, "find-generic-password", "-s", "mph", "-a", "freesound_api_key", "-w")


@pytest.mark.req("REQ-SEC-01")
def test_get_missing_returns_none() -> None:
    store = KeychainStore(FakeRunner(responses={"security": Result(44)}), "mph")
    assert store.get("freesound_api_key") is None


@pytest.mark.req("REQ-SEC-02")
def test_exists_never_requests_the_value() -> None:
    runner = FakeRunner()
    assert KeychainStore(runner, "mph").exists("gemini_api_key")
    argv, _ = runner.calls[0]
    assert "-w" not in argv


@pytest.mark.req("REQ-SEC-02")
def test_set_delegates_prompt_to_security_on_tty() -> None:
    runner = FakeRunner()
    assert KeychainStore(runner, "mph").set_interactive("gemini_api_key")
    argv, interactive = runner.calls[0]
    assert interactive
    assert argv[-1] == "-w"  # bare -w: security prompts; value never in argv
    assert "-U" in argv


@pytest.mark.req("REQ-SEC-02")
def test_delete_uses_delete_verb() -> None:
    runner = FakeRunner()
    KeychainStore(runner, "mph").delete("gemini_api_key")
    assert runner.calls[0][0][1] == "delete-generic-password"


@pytest.mark.req("REQ-SEC-03")
def test_unknown_secret_rejected_before_any_process_runs() -> None:
    runner = FakeRunner()
    with pytest.raises(UnknownSecretError):
        KeychainStore(runner, "mph").get("aws_key")
    assert runner.calls == []


@pytest.mark.req("REQ-SEC-03")
def test_every_known_secret_documents_purpose_and_signup() -> None:
    for spec in KNOWN_SECRETS:
        assert spec.purpose
        assert spec.signup_url.startswith("https://")
        assert spec_for(spec.name) is spec


@pytest.mark.req("REQ-SEC-04")
def test_all_current_secrets_are_optional() -> None:
    assert not any(s.required for s in KNOWN_SECRETS)
