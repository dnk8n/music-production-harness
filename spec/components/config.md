# Config

**Status:** built

Settings are TOML, layered, validated once and immutable afterwards.
Implementation: `src/mph/config.py`, defaults in `src/mph/default_config.toml`.

### REQ-CFG-01: Layered precedence
Config is resolved as packaged defaults, then the user file
(`~/.config/mph/config.toml`, or the path in `$MPH_CONFIG`), then an
explicit `--config` path. Tables merge recursively and arrays replace whole.
- **Why:** defaults keep the repo runnable out of the box, while private paths
  stay on Dean's machine and out of git.
- **Verify:** test

### REQ-CFG-02: Report every problem at once
Invalid config fails with a single error that lists every problem found,
including unknown keys.
- **Why:** fixing config one error at a time is pointless back and forth.
- **Verify:** test

### REQ-CFG-03: Sources are declared, not hard-coded
Sample sources are `[[sources]]` tables with `id`, `kind` (`local` or
`remote`), `path` or `url`, and `license`. IDs are unique.
- **Why:** the sample folder must be configurable, and more sources (archives,
  new packs) get added over time.
- **Verify:** test

### REQ-CFG-04: Writable data never overlaps a source
Config is rejected if `paths.data_dir` is inside, equal to, or contains any
local source path.
- **Why:** sources are read-only by principle. Making the overlap impossible
  stops caches or DB files from ever landing inside a sample folder.
- **Verify:** test

### REQ-CFG-05: Audio policy is validated
Sample rates must be standard rates, production rate must be at least the
delivery rate, delivery bit depth must be 16 or 24, and the format must be
`aiff` or `wav`.
- **Why:** an impossible audio policy should fail at load, not at minute 12 of
  a render.
- **Verify:** test

### REQ-CFG-06: No secrets in config
Config files never hold secrets. Secret values live only in the Keychain
(see [secrets.md](secrets.md)).
- **Why:** config files get copied, shared and committed. The Keychain is
  encrypted and access-controlled.
- **Verify:** inspect
