# ADR-0005: Secrets in the macOS Keychain via `security`

**Status:** accepted
**Date:** 2026-11-08

## Context
Dean asked for the Apple password system, and secrets must never touch the repo.

## Decision
Use `/usr/bin/security` generic-password items under one service, one item
per credential field. `mph secrets set` collects every field through a guided
prompt (hidden input) and hands values to `security -i` over stdin, never
argv. Values are read only at point of use.
There are no `.env` files and no environment variable fallback.

## Consequences
Secrets only work on macOS, so Linux CI tests use a fake store. If CI ever
needs a key, it uses GitHub Actions secrets, scoped to the one job.
