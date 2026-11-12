# Glossary

**Status:** accepted

| Term | Meaning |
|---|---|
| **Source** | A read-only origin of audio: a local folder (`kind = "local"`) or an online archive (`kind = "remote"`). Never written to. |
| **Asset** | One audio file known to the catalogue, identified by content hash, with provenance back to a source. |
| **Derivative** | An asset produced from other assets by a recorded, repeatable transformation (trim, separate, render). Lives in the cache. |
| **Cache** | Derivatives that can be regenerated at any time. Safe to delete. |
| **Stash** | Derivatives Dean chose to keep (e.g. a favourite vocal chop). Not auto-deleted, but still regenerable from its recipe. |
| **Recipe** | The exact tool, version and parameters that turned inputs into a derivative. Hash of the recipe plus input hashes = derivative identity. |
| **Stem** | A rendered audio track for one musical part (drums, bass, vocal), 32-bit float at the production rate. |
| **Production rate** | Sample rate for synthesis and stems (default 96 kHz). |
| **Delivery** | The final 48 kHz / 24-bit AIFF. The only point where sample rate conversion (SRC) and dither happen. |
| **Benchmark** | A short render with a written description of what Dean should hear, automated measurements, and Dean's verdict. ID `BM-...`. |
| **Verdict** | Dean's pass/fail plus notes on a benchmark render, captured by the feedback page. |
| **Take** | One way of making a benchmark (engine, sounds, levels): a variant. All takes are equal. |
| **Triplet grid** | A 12-step grid: eighth-note triplets, three to a beat, playing alongside 16-step grids (REQ-BEN-13). |
| **Feel** | Where a beat's three triplet pulses fall, as shares of the beat: even, or uneven like the long-short-short of jembe drumming (REQ-BEN-13). |
| **Timeline** | The repeating bell pattern that organises an African drum ensemble, such as the standard bell pattern in BM-AFRO-01. |
| **Gate** | A check a change must pass: deterministic, advisory AI, human. |
| **Licence marker** | `licence:clear` when every ingredient of a render has a publishable licence, otherwise `licence:private`. |
| **Doctor** | `mph doctor`: read-only diagnosis of installation and config, with a fix for each problem. |
| **Query channel** | `mph mcp`: a read-only MCP server through which Claude asks about the catalogue, benchmarks, renders and health. |
| **Stage** | A pipeline phase: core, catalogue, synthesis, vocals, renoise, mastering, ai. |
