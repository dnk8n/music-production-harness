# Catalogue

**Status:** draft (being built in step 2: REQ-CAT-01, 02, 03, 04, 08 done)

A local search engine over every sound the harness can use, with full
provenance. The first source is `/Users/dean/dj/samples` (about 4,000 files,
7.1 GB, scanned 2026-11-08), configured in Dean's user config, not here.

## Commands

- `mph catalogue scan [--source ID] [--workers N]`: read-only, incremental indexing.
- `mph catalogue stats`: counts by pack, format and role.
- `mph catalogue analyse [--role R] [--pack P] [--limit N] [--workers N] [--force]`: measure on demand, cached per analyser version (ADR-0008).
- Later in step 2: `mph catalogue search`, `refresh`, `stash`, `fetch`.

## Data model

Built so far (migrations 1 and 2, `src/mph/catalogue/db.py`):

```
source(id, kind, location, licence, last_scan_at)
asset(hash PK, bytes, container, codec, sample_rate, bit_depth, channels, duration_s,
      origin: source|download|derivative, probe_error)
source_file(source_id, rel_path, size, mtime_ns, asset_hash, scan_id)   -- where content lives
document(source_id, rel_path, kind, scan_id)                            -- licence evidence
tag(asset_hash, name, value, origin: folder|filename|analysis|human, confidence)
scan(id, source_id, started_at, finished_at, files_seen, hashed, new_assets, moved, removed,
     skipped)
analysis(asset_hash, analyser, version, analysed_at, result_json, error)
```

Analysis features (analyser `core` v1): duration, peak/RMS dBFS, integrated
LUFS (sounds of 0.4 s or more), onset count, BPM with confidence (loops, snapped
to a whole number of beats in the loop length), key with confidence
(Krumhansl-Schmuckler on chroma of the harmonic part), spectral centroid, share
of energy below 150 Hz, percussive share (HPSS), and form (one-shot, loop or
phrase). Tags `bpm`, `key`, `form` and `character` are added with
`origin=analysis`, next to the name priors.

Still to come (later migrations): `recipe`, `derivation`, `stash`, FTS5 text index.

One asset can live at several paths (duplicates across packs). Name-derived
tags are rebuilt from all current paths whenever an asset's paths change.

### REQ-CAT-01: Sources are read-only
The catalogue opens source files read-only and never creates, modifies or
deletes anything under a local source path.
- **Why:** Dean's sample folder must never be touched.
- **Verify:** test

### REQ-CAT-02: Incremental, content-addressed scanning
Assets are identified by content hash (BLAKE2b-256). Re-scans skip files
whose size and mtime haven't changed, and detect moves and renames by hash.
Hashing and probing run on a bounded worker pool (`--workers`, default 4).
- **Why:** a 7 GB library should rescan in seconds, and renaming folders
  shouldn't lose analysis.
- **Verify:** test

### REQ-CAT-03: Names are priors, analysis is evidence
Key, BPM and role hints parsed from folder and file names (e.g. "Acid Techno
(Ab, 131bpm)", "MXE_128_Vocals_19") are stored as tags with
`origin=filename`. Audio analysis confirms or contradicts them, and both are kept.
- **Why:** pack naming is a cheap, mostly right head start, but mislabels exist.
- **Verify:** test

### REQ-CAT-04: Just-in-time analysis, cached by analyser version
Expensive analysis (BPM, key, loudness, onset density, spectral centroid,
instrument class) runs on demand and is cached per analyser version.
- **Why:** analysing 4,000 files up front is slow and mostly wasted.
- **Verify:** test

### REQ-CAT-05: Search
`mph catalogue search` filters by role, BPM range, key (with compatible
keys), loudness, duration, licence and free text, ranked by fit.
- **Why:** track construction starts with "find me a dry 909 open hat near
  128 BPM", and picking samples has to be explainable.
- **Verify:** test

### REQ-CAT-06: Provenance and non-destructive refresh
Every derivative records its recipe and inputs. `mph catalogue refresh` can
rebuild any cache entry from source, and stashes survive cache purges.
- **Why:** the DB models sources, then downloads, then cache and stashes, and
  can always rebuild from source.
- **Verify:** test

### REQ-CAT-07: Remote sources and licence tracking
Remote connectors (Freesound, Internet Archive, LibriVox) download into
`data_dir/downloads/`, recording URL, licence and attribution. Local packs
default to `licence = "commercial-pack"`.
- **Why:** the library has no spoken word and little African percussion, so
  downloads are needed. Licence data drives the `licence:clear` marker.
- **Verify:** test

### REQ-CAT-08: Junk is ignored
`.DS_Store`, Ableton `.asd`, and non-audio files are skipped. Pack documents
(PDF, TXT) are linked to their source as licence evidence.
- **Why:** the scan found about 85 `.DS_Store` and 50 `.asd` files mixed in
  with the audio.
- **Verify:** test
