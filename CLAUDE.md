# Agent instructions

1. **Spec first.** `spec/` is the source of truth (ADR-0001). Behaviour
   changes start as a spec change with a Why and a Verify method. Run
   `uv run mph spec check` before every commit.
2. **Cite IDs.** Module docstrings name their spec file. Tests link
   requirements with `@pytest.mark.req("REQ-...")`. PR descriptions list the
   IDs they implement.
3. **Sources are read-only.** Never write under a configured source path.
   Everything derived goes to `data_dir`.
4. **Seams.** Spawn processes only via `mph.proc.Runner`. Unit tests use the
   fakes in `tests/fakes.py` and never touch real apps, the Keychain or the network.
5. **Checks before pushing:** `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest --cov=mph && uv run mph spec check`.
6. **Never** commit secrets, audio files, local config, or the catalogue DB.
7. Track work in GitHub issues with labels from `.github/labels.yml`
   (one `type:`, one `area:`, a `step:` where relevant).
8. Write for Dean: plain words in user-facing text, and no gendered pronouns
   for people whose pronouns aren't stated.
