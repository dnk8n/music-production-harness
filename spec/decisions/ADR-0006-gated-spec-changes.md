# ADR-0006: Spec changes pass deterministic, advisory-AI and human gates

**Status:** accepted
**Date:** 2026-11-08

## Context
The architect's setup gates "brain" entries with a local scorer model, then
a reviewer model. Dean liked the idea, but on a 16 GB M1 with free tools.

## Decision
1. A deterministic gate (`mph spec check`) replaces the local scorer model
   for structural checks ("is there a Why?"). It's cheaper, faster and
   never flaky.
2. An advisory LLM review checks semantics (duplication, contradiction,
   vagueness). It uses a free-tier API key from the Keychain when present,
   otherwise local Ollama (`gemma3:4b` fits in 16 GB). It comments and never blocks.
3. Dean approves.

## Consequences
Most problems are caught for free in CI. The LLM gate stays swappable by
config, so a paid model can slot in later.
