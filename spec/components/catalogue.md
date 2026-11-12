# Catalogue

**Status:** built (step 2: REQ-CAT-01 to 09)

A local search engine over every sound the harness can use, with full
provenance. The first source is `/Users/dean/dj/samples` (about 4,000 files,
7.1 GB, scanned 2026-11-08), configured in Dean's user config, not here.

## Commands

- `mph catalogue scan [--source ID] [--workers N]`: read-only, incremental indexing.
- `mph catalogue stats`: counts by pack, format and role.
- `mph catalogue analyse [--role R] [--pack P] [--limit N] [--workers N] [--force]`: measure on demand, cached per analyser version (ADR-0008).
- Later in step 2: `mph catalogue search`, `refresh`, `stash`, `fetch`.
- `mph catalogue sound REF [--json]`: everything mph knows about one sound, every label
  with where it came from and how sure it is (REQ-CAT-09).

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
recipe(id, kind, version, params_json)                -- id = hash of the canonical recipe
derivation(output_hash, recipe_id, inputs_json, created_at)
stash(name, asset_hash, created_at)
download(source_id, remote_id, asset_hash, rel_path, url, page_url, title, creator,
         licence, licence_url, fetched_at)
```

Derivatives live in `data_dir/cache/<hh>/<hash>.wav` (purgeable) and, when
stashed, in `data_dir/stash/<name>/<hash>.wav` (kept). Recipes run ffmpeg with
`-bitexact` and write 32-bit float WAV at the input's own rate. The first
recipe is `trim`; stem separation and others join as the benchmarks need them.
Commands: `derive`, `stash`, `purge-cache`, `refresh`, `lineage`.

Analysis features (analyser `core` v2): duration, peak/RMS dBFS, integrated
LUFS (sounds of 0.4 s or more), onset count, BPM with confidence (loops, snapped
to a whole number of beats in the loop length), key with confidence
(Temperley key profiles on chroma of the harmonic part), spectral centroid, share
of energy below 150 Hz, percussive share (HPSS), and form (one-shot, loop or
phrase). Tags `bpm`, `key`, `form` and `character` are added with
`origin=analysis`, next to the name priors.

A single loop often doesn't spell out its pack's key: a riff in D minor may
only play F, A and C. So an analysed key is a hint, and where a pack labels its
key, the label wins. `mph catalogue analyse` reports agreement three ways (BPM
within 2%, key or its relative, and key or a neighbouring key one step round
the circle of fifths), always against the most confident name hint. Search
treats neighbouring keys as compatible.

Still to come: an FTS5 text index, if word search gets slow.

One asset can live at several paths (duplicates across packs). Name-derived
tags are rebuilt from all its current paths in local sources whenever an
asset's paths change, and for every asset when the naming rules change
(`names.VERSION`).

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

Genre names and artist credits never give a role, however deep they sit:
"bass" in BASS_HOUSE_AND_GARAGE, "Future Bass" or "Drum & Bass", and
everything before "presents" (TOM MIDDLETON PRESENTS DUB BASS HOUSE).

The pack's folder and the folder just inside it name things more than they
label them: often a kit or a broad category ("Mutant Breaks", "80s Synthpop
(Dm, 87bpm)", "Pads and Chords"). They still give tempo, key and form, but a
role only when it agrees with the roles the file name and deeper folders
give, or when those give none ("Mutant Breaks"). A pack folder named only for
its sounds ("Stab Bass Sample Pack") labels them like any other folder; a list
of kinds ("Kicks & Snares") still lets the file name say which.

A downloaded sound keeps the labels it got from its title and keywords when
it was fetched (REQ-CAT-07). Its path in the download store is a remote id
and a file name, so it adds none.
- **Why:** pack naming is a cheap, mostly right head start, but mislabels exist.
  Product and kit names often hold a genre or a person: the drum loops in
  BASS_HOUSE_AND_GARAGE came out as bass, and the TOM MIDDLETON basses as toms
  (2026-11-11). A pack's name is sometimes all that says what its files are,
  so it still counts when nothing else does. Reading a download's labels again
  from its path made its remote id its pack and lost the roles its title and
  keywords gave, which can't be made again because the keywords aren't kept.
  On Dean's library the products sit a level deeper, inside a year folder
  (LOOPMASTERS 4GB Pack/LOOPMASTERS_2014/BASS_HOUSE_AND_GARAGE_2), so a rule
  about the top two levels missed them, and took bass off the Stab Bass pack.
  The genre and artist words were what was wrong, wherever they sit: 251 of
  his 4,060 files lost a bass, drums or tom label they got that way
  (2026-11-12).
- **Verify:** test

### REQ-CAT-04: Just-in-time analysis, cached by analyser version
Expensive analysis (BPM, key, loudness, onset density, spectral centroid,
instrument class) runs on demand and is cached per analyser version.
- **Why:** analysing 4,000 files up front is slow and mostly wasted.
- **Verify:** test

### REQ-CAT-05: Search
`mph catalogue search` filters by role, BPM range, key (with compatible
keys), loudness, duration, licence and words, ranked by fit. Filters are
hard; fit is a sum of confidence-weighted matches, and each result lists the
reasons behind its score. Tempo matches allow half and double time; one-shots
and sounds with no key pass tempo and key filters without gaining score.
Each result names its licence, the one a render's report would record
(REQ-BEN-05): a download's own, else its source's. It also shows the start of
the sound's hash and its strongest labels, so `mph catalogue sound` can show
the rest.
Words match whole words of a sound's paths and labels, never part of one:
"dub" doesn't find DUBSTEP and "m1" doesn't find Tom1. Plurals match ("kick"
finds Kicks), joined-up words count as theirs (TR909 has 909, ClosedHat has
closed and hat), and a phrase ("open hat") matches in that order.
`--json` gives the same results for other tools.
- **Why:** track construction starts with "find me a dry 909 open hat near
  128 BPM", and picking samples has to be explainable. Whether a track can
  be published depends on the licences of its sounds, so choosing one should
  show it (2026-11-11). Matching parts of words found the wrong sounds:
  "hand" found Ghandi, "m1" Tom1 and "dub" DUBSTEP (2026-11-11). A search
  without `--role` showed no labels, so it couldn't answer "is this a pad or
  a chord?" (2026-11-12).
- **Verify:** test

### REQ-CAT-09: One sound in full
`mph catalogue sound REF` shows everything mph knows about one sound, found
by its hash, a unique hash prefix of 6 or more characters, or its path:
every label with where it came from and how sure it is, its files and their
licences, analysis, download source, what it was made from, stashes, whether
it is on disk and which renders used it. It is the query channel's `sound`
answer (REQ-QRY-03); `--json` prints that answer as it is.
- **Why:** Dean couldn't check a label change kit-diff reported. Search shows
  no labels unless asked for a role and doesn't take a hash, and the only way
  in was `mph mcp call sound` (2026-11-12).
- **Verify:** test

### REQ-CAT-06: Provenance and non-destructive refresh
Every derivative records its recipe and inputs. `mph catalogue refresh` can
rebuild any cache entry from source, and stashes survive cache purges. A
rebuild must reproduce the recorded bytes exactly; if a tool upgrade changes
them, refresh reports it instead of silently swapping the sound.
- **Why:** the DB models sources, then downloads, then cache and stashes, and
  can always rebuild from source.
- **Verify:** test

### REQ-CAT-07: Remote sources and licence tracking
Remote connectors (Freesound, Internet Archive, LibriVox) download into
`data_dir/downloads/`, recording URL, licence and attribution. Local packs
default to `licence = "commercial-pack"`.

`mph catalogue fetch <connector> <words>` searches and downloads; each file
becomes a catalogued sound (searchable, analysable, playable in the browser)
with a `download` row: source URL, page URL, title, creator, licence (short
form such as `cc0`, `cc-by`, `cc-by-nc`, `public-domain`) and fetch time.
`--clear-only` keeps to licences that allow publishing (`cc0`,
`public-domain`, `cc-by`): the set the `licence:clear` label depends on.
`mph catalogue credits` prints attribution lines. `mph catalogue refresh`
fetches a deleted download again and rejects it if the bytes changed.

Freesound originals need a one-time OAuth2 sign-in with PKCE (`mph catalogue auth
freesound`): Dean opens a link, approves, and pastes the code shown. Only the
refresh token is stored, in the Keychain beside the API keys (REQ-SEC-01);
access tokens live in memory. Without sign-in, `--previews` takes the lossy
MP3 previews. Internet Archive items yield one file each, the best quality on
offer (FLAC, then WAV or AIFF, then the highest-bitrate MP3). All network
access goes through `mph.net.Http`, so tests never reach the internet.
- **Why:** the library has no spoken word and little African percussion, so
  downloads are needed. Licence data drives the `licence:clear` marker.
- **Verify:** test

### REQ-CAT-08: Junk is ignored
`.DS_Store`, Ableton `.asd`, and non-audio files are skipped. Pack documents
(PDF, TXT) are linked to their source as licence evidence.
- **Why:** the scan found about 85 `.DS_Store` and 50 `.asd` files mixed in
  with the audio.
- **Verify:** test
