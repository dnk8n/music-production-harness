# ADR-0004: SQLite catalogue with content-addressed assets

**Status:** proposed (finalised in step 2)
**Date:** 2026-11-08

## Context
About 4,000 local files plus downloads and derivatives need search,
provenance and non-destructive refresh, on one laptop.

## Decision
A single SQLite file in `data_dir` (standard library, WAL mode, FTS5 for
text search). Assets are keyed by BLAKE2b content hash, and derivatives by
recipe hash plus input hashes.

## Alternatives considered
- *DuckDB:* great for analytics, but there are fewer write patterns and it's
  an extra dependency.
- *A vector DB for "sounds like" search:* deferred. Embeddings can go in a
  table if needed.

## Consequences
Zero setup and an easy-to-inspect file. Moving the library doesn't lose
analysis, because hashes survive renames.
