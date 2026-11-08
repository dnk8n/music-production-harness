# ADR-0001: The spec is the source of truth; code is regenerable

**Status:** accepted
**Date:** 2026-11-08

## Context
Dean wants to own the *system*, not a one-off artefact, and to be able to
rebuild everything from the latest spec in one pass. AI makes rewriting code
cheap, while understanding stays expensive.

## Decision
`spec/` holds numbered requirements with a Why and a Verify method. Code
cites spec IDs, tests link them with `@pytest.mark.req`, and CI checks the
links. When behaviour must change, the spec changes first.

## Consequences
- The spec needs care: gates (ADR-0006) keep it consistent.
- Code can be deleted and regenerated without losing intent.
- Reviewers judge alignment by reading spec items next to the code that cites them.
