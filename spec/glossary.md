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
| **Stem** | One musical part (drums, bass, vocals) as audio of its own, 32-bit float: rendered by mph at the production rate, or separated from a full track at the track's own rate (REQ-CAT-11). |
| **Full track** | A finished piece of music, kept to remix or play rather than as a sample (REQ-CAT-10). |
| **Label** | Something the catalogue knows about a sound (role, tempo, key, form, pack, title...), with where it came from (its origin: folder, filename, embedded, analysis or human) and how sure it is. Stored as a `tag`. |
| **Embedded metadata** | What an audio file says about itself: its tags (title, artist, album...) and its loop and sampler chunks (ACID tempo, root note, loop points). Labels from it have `origin=embedded` (REQ-CAT-12). |
| **Production rate** | Sample rate for synthesis and rendered stems (default 96 kHz). |
| **Delivery** | The final 48 kHz / 24-bit AIFF. The only point where sample rate conversion (SRC) and dither happen. |
| **Benchmark** | A short render with a written description of what Dean should hear, automated measurements, and Dean's rating. ID `BM-...`. |
| **Rating** | Dean's 0 to 5 stars on a take (3 or more is a pass), whether it is resolved, and his feedback, given on the feedback page (REQ-FBK-02). |
| **Resolved** | A take needs nothing more: it renders again only when forced. **Unresolved**: it needs work, and agents find it through `feedback` (REQ-FBK-06). Set by Dean, or by the stars when he doesn't. |
| **Idea** | A project, benchmark or variant Dean asked for on the page, until agents build it as spec and score (REQ-FBK-08). |
| **Verdict** | Retired 2026-11-13: pass, close or fail on a render, before ratings. Carried over into ratings (REQ-FBK-07). |
| **Take** | One way of making a benchmark (engine, sounds, levels): a variant. All takes are equal. |
| **Triplet grid** | A 12-step grid: eighth-note triplets, three to a beat, playing alongside 16-step grids (REQ-BEN-13). |
| **Feel** | Where a beat's three triplet pulses fall, as shares of the beat: even, or uneven like the long-short-short of jembe drumming (REQ-BEN-13). |
| **Timeline** | The repeating bell pattern that organises an African drum ensemble, such as the standard bell pattern in BM-AFRO-01. |
| **Gate** | A check a change must pass: deterministic, advisory AI, human. |
| **Licence marker** | `licence:clear` when every ingredient of a render has a publishable licence, otherwise `licence:private`. |
| **Doctor** | `mph doctor`: read-only diagnosis of installation and config, with a fix for each problem. |
| **Query channel** | `mph mcp`: a read-only MCP server through which Claude asks about the catalogue, benchmarks, renders and health. |
| **Stage** | A pipeline phase: core, catalogue, synthesis, vocals, renoise, mastering, ai. |
