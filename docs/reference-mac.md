# The reference Mac

Every example project was built, rendered and rated on Dean's Mac. This page
describes it, so you can tell how close your own setup is, and what a
difference in your takes might come from. Checked with `mph doctor` and
`sw_vers`; dates say when each fact was last seen.

## The machine

| | |
|---|---|
| Model | MacBook Pro 14-inch (2021) |
| Chip and memory | Apple M1 Pro, 16 GB |
| System | macOS 26.5.2 (build 25F84), arm64 (2026-11-21) |
| Disk | 460 GiB, nearly full: 6 to 8 GiB free through November 2026 (6.1 GiB on 2026-11-21) |
| Checkout | `/Users/dean/src/music-production-harness` |
| Data folder | `~/Library/Application Support/mph`: renders took 13.8 GB on 2026-11-21 |
| Config | `~/.config/mph/config.toml`, as in [examples/config/dean.toml](../examples/config/dean.toml) |

## The tools

As `mph doctor` reported them on 2026-11-21, every check green:

| Tool | Version | Notes |
|---|---|---|
| Renoise | 3.5.4, arm64, licensed | the licence likely covers upgrades to about 4.5 |
| SuperCollider | 3.13.0, with sc3-plugins | |
| Surge XT | 1.3.4 | an Audio Unit |
| FFmpeg | 9.0.2 | Homebrew |
| SoX | Homebrew's, with libsoxr | |
| Demucs | 4.1.0, `htdemucs_ft` on the CPU | a `uv tool`, installed with numpy |
| uv | 0.9.17 | |
| Homebrew | 7.0.13 | |
| GitHub CLI | 2.103.3 | |
| Ollama | 0.40.16 | |
| Tidal Cycles, Ultimate Vocal Remover | installed | optional; nothing uses them yet |
| LuaJIT | Homebrew's | Renoise's own Lua; the bridge tests prefer it |
| Claude Code | with `mph mcp install` done | the `mph` tools at user scope |

Freesound and Gemini keys are in the Keychain (`mph secrets list`).

## The sample library

Dean's sources are one read-only folder, `/Users/dean/dj/samples`, scanned
on 2026-11-08 and many times since:

- About 7.1 GB: 4,060 sounds (3,753 WAV, 270 AIFF, 35 M4A, 1 MP3) in 577
  folders, mostly 44.1 kHz / 16-bit stereo.
- Commercial packs: the Loopmasters 4 GB pack (about 2,180 files, its
  products two folders down, by year), Novation's Anniversary drops 1 to 3
  (about 1,230; folder names give key and tempo, such as
  `Acid Techno (Ab, 131bpm)`), Photek Full (326: breaks, hits, pads), 90s
  Pads & Drumfunk (133), Mutant Breaks (92), Stab Bass (57), and `voice/`,
  whose one file, `onebassline.wav`, is 47 seconds of spoken word despite
  its name.
- `full_tracks/`: whole songs Dean owns, such as the FLAC the Love Train
  remix starts from.
- Gaps: little African percussion (11 conga and 11 bongo files, and one
  djembe loop), no agogo, shekere or talking drum, no 909-named hits, and no
  spoken word apart from the file above. The north star benchmarks use
  SuperCollider stand-ins for the missing drums.

What the labels can be trusted for:

- File names carry tempo and role words (`MXE_128_...`, `kick`, `loop`,
  `fx`). Genre and artist names don't give a role.
- A folder's key is the pack song's key; a single loop often doesn't say it,
  and some files disagree with their folder. The analysed key matched the
  key or its relative minor on 25 of 50 loops with Temperley's profiles (18
  with Krumhansl's), so it is a hint, and search matches neighbouring keys.
- Loopmasters' ACID chunks nearly all say 120 BPM, a default stamped by the
  pack's tool. mph trusts a chunk's tempo only when its beats fill the file
  within 0.5%; beats over length matched the names on all 1,186 loops that
  had both.

## How long things take, and how much space

| Job | Time | Disk |
|---|---|---|
| `scripts/mac-check` | about 5 minutes | about 2.5 GB of test files, deleted when tests pass |
| Demucs stems of a 5.5-minute song | about 9 minutes on the CPU | about 0.5 GB for four float stems |
| Four 1-minute remix blocks in Renoise | 735 s in one command | under 1.1 GB each at peak |
| The 8-minute remix in Renoise | 26.5 minutes | peaks near 3.9 GB, keeps about 3 GB; needs about 6 GB free |
| The 8-minute remix in SuperCollider | | peaks near 7.7 GB, keeps about 2.2 GB |
| The 8-minute free remix in Renoise (16 voices) | 12.75 minutes | keeps about 3.1 GB; took 3.2 GiB of free space |

The free remix's time came after the choke check was made faster
(2026-11-21), which cut about 17 minutes from measuring a whole track in
a cloud copy; remix 1's 26.5 minutes came before it.

Renders stay in 32-bit float until the one conversion at delivery, at 96 kHz
unless a track sets its own rate (the remix renders at 48 kHz, REQ-TRK-06),
so they are large. Check `df -h ~` before a long render. `mph prune` shows
what can go, and `mph status` shows disk use.

## Habits that keep it working

- Quit Renoise before a render, or let mph start its own: mph won't use a
  Renoise it didn't start (REQ-REN-03). Several takes render best in one
  command.
- After an update to the bridge tool, run `uv run mph renoise install` and
  restart Renoise; until then mph says the tool is out of date.
- The first render of a new Renoise song starts a few samples late, so mph
  renders a warm-up first; after it, Renoise renders are bit for bit
  repeatable.
- Renoise's Lua is LuaJIT, which refuses a function with more than 65,536
  constants, so mph splits big jobs.
