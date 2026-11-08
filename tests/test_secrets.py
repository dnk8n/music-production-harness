from __future__ import annotations

from collections.abc import Sequence

import pytest

from mph import secrets
from mph.proc import Result
from mph.secrets import SECURITY, KeychainStore, credential_for
from tests.fakes import FakeRunner, FakeStore, ScriptedPrompter

FREESOUND = credential_for("freesound")
GOOD_ID, GOOD_SECRET = "AbCdEf123456", "s3cr3tKeyValue_with-dots.ok"


@pytest.mark.req("REQ-SEC-01")
def test_get_reads_keychain_item_under_service() -> None:
    runner = FakeRunner(responses={"security": Result(0, "value\n")})
    assert KeychainStore(runner, "mph").get("freesound.client_secret") == "value"
    assert runner.calls[0][0] == (
        SECURITY,
        "find-generic-password",
        "-s",
        "mph",
        "-a",
        "freesound.client_secret",
        "-w",
    )


@pytest.mark.req("REQ-SEC-01")
def test_get_missing_returns_none() -> None:
    store = KeychainStore(FakeRunner(responses={"security": Result(44)}), "mph")
    assert store.get("gemini.api_key") is None


@pytest.mark.req("REQ-SEC-01")
def test_one_keychain_item_per_field() -> None:
    accounts = [FREESOUND.account(f) for f in FREESOUND.fields]
    assert accounts == ["freesound.client_id", "freesound.client_secret"]


@pytest.mark.req("REQ-SEC-02")
def test_put_sends_value_over_stdin_never_argv() -> None:
    def respond(argv: Sequence[str]) -> Result:
        return Result(0, GOOD_SECRET + "\n") if "-w" in argv else Result(0)

    runner = FakeRunner(responses={"security": respond})
    assert KeychainStore(runner, "mph").put("gemini.api_key", GOOD_SECRET)
    argv, _ = runner.calls[0]
    assert argv == (SECURITY, "-i")
    assert all(GOOD_SECRET not in " ".join(a) for a, _ in runner.calls)
    stdin = runner.stdins[0] or ""
    assert "add-generic-password -s mph -a gemini.api_key -U -w" in stdin
    assert GOOD_SECRET in stdin


@pytest.mark.req("REQ-SEC-02")
def test_put_verifies_by_reading_back() -> None:
    runner = FakeRunner(responses={"security": Result(0, "something-else\n")})
    assert not KeychainStore(runner, "mph").put("gemini.api_key", GOOD_SECRET)


@pytest.mark.req("REQ-SEC-02")
@pytest.mark.parametrize("bad", ['has"quote', "has space", "short", "x\ny" * 5])
def test_put_rejects_values_that_could_break_quoting(bad: str) -> None:
    runner = FakeRunner()
    assert not KeychainStore(runner, "mph").put("gemini.api_key", bad)
    assert runner.calls == []


@pytest.mark.req("REQ-SEC-02")
def test_exists_never_requests_the_value() -> None:
    runner = FakeRunner()
    assert KeychainStore(runner, "mph").exists("gemini.api_key")
    assert "-w" not in runner.calls[0][0]


def test_delete_uses_delete_verb() -> None:
    runner = FakeRunner()
    KeychainStore(runner, "mph").delete("gemini.api_key")
    assert runner.calls[0][0][1] == "delete-generic-password"


@pytest.mark.req("REQ-SEC-03")
def test_unknown_credential_rejected() -> None:
    with pytest.raises(secrets.UnknownCredentialError, match="known: freesound, gemini"):
        credential_for("aws")


@pytest.mark.req("REQ-SEC-03")
def test_every_field_says_where_to_find_it() -> None:
    for credential in secrets.CREDENTIALS:
        assert credential.signup_url.startswith("https://")
        for field in credential.fields:
            assert field.label
            assert field.where


@pytest.mark.req("REQ-SEC-03")
def test_freesound_needs_client_id_and_secret() -> None:
    assert [f.key for f in FREESOUND.fields] == ["client_id", "client_secret"]
    assert not FREESOUND.fields[0].secret  # client ID is shown while typing
    assert FREESOUND.fields[1].secret


@pytest.mark.req("REQ-SEC-04")
def test_all_current_credentials_are_optional() -> None:
    assert not any(c.required for c in secrets.CREDENTIALS)


@pytest.mark.req("REQ-SEC-05")
def test_setup_asks_every_field_then_saves_all() -> None:
    store = FakeStore()
    prompter = ScriptedPrompter([GOOD_ID, GOOD_SECRET])
    assert secrets.setup(store, FREESOUND, prompter)
    assert prompter.asked == [("Client ID", False), ("Client secret / API key", True)]
    assert secrets.read(store, FREESOUND) == {"client_id": GOOD_ID, "client_secret": GOOD_SECRET}
    said = "\n".join(prompter.said)
    assert "freesound.org/apiv2/apply" in said
    assert "OAuth2" in said  # notes explain the extra step for full-quality downloads


@pytest.mark.req("REQ-SEC-05")
def test_setup_reasks_on_bad_shape_and_strips_whitespace() -> None:
    store = FakeStore()
    prompter = ScriptedPrompter(["bad value", f"  {GOOD_ID}  ", GOOD_SECRET])
    assert secrets.setup(store, FREESOUND, prompter)
    assert store.values["freesound.client_id"] == GOOD_ID


@pytest.mark.req("REQ-SEC-05")
def test_setup_writes_nothing_if_any_field_abandoned() -> None:
    store = FakeStore()
    prompter = ScriptedPrompter([GOOD_ID, "x", "y", "z"])
    assert not secrets.setup(store, FREESOUND, prompter)
    assert store.values == {}
    assert "Nothing was saved." in prompter.said


@pytest.mark.req("REQ-SEC-05")
def test_setup_reports_keychain_failure() -> None:
    prompter = ScriptedPrompter([GOOD_SECRET])
    assert not secrets.setup(FakeStore(fail_puts=True), credential_for("gemini"), prompter)
    assert any("Could not save" in line for line in prompter.said)


def test_missing_fields_and_partial_read() -> None:
    store = FakeStore({"freesound.client_id": GOOD_ID})
    assert [f.key for f in secrets.missing_fields(store, FREESOUND)] == ["client_secret"]
    assert secrets.read(store, FREESOUND) is None


def test_remove_deletes_every_field() -> None:
    store = FakeStore({"freesound.client_id": "a", "freesound.client_secret": "b"})
    assert secrets.remove(store, FREESOUND)
    assert store.values == {}
    assert not secrets.remove(store, FREESOUND)
