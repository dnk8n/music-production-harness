# Dean's preferences

The standing choices Dean made while building mph, each with where it is
written down in full. Most are rules in the spec, with their Why; this page
is the index, so nobody has to ask Dean again. When a new preference comes
up, add it to the spec, skill or doc it belongs to, and list it here.

## What mph is

| Preference | Where |
|---|---|
| The spec is the product; code can be made again from it | ADR-0001, spec/README.md |
| No neural model makes music; neural tools only analyse or separate | spec/vision.md |
| Free tools first; paid services plug in through config, with local fallbacks | spec/vision.md |
| Private use; a track whose every ingredient is publishable is marked `licence:clear` | spec/vision.md, REQ-MST-04 |
| Benchmarks pass Dean's ear before any track gets a spec | spec/vision.md, spec/components/benchmarks.md |
| Renoise holds the patterns, as songs a person can open and change; SuperCollider makes sounds and coded parts | ADR-0009 |
| Laptop keyboard first, MIDI controllers later, and live performance only after full tracks render | ADR-0007, docs/roadmap.md |

## Sounds and audio

| Preference | Where |
|---|---|
| Sample folders are read-only; everything derived can be made again | REQ-CAT-01, spec/architecture.md |
| Convert once: samples at their own rate, one conversion to 48 kHz / 24-bit AIFF at delivery | ADR-0003, spec/components/audio-policy.md |
| Keep only fresh renders; make old ones again when needed | ADR-0010, spec/components/retention.md |
| Hats and cymbals hiss: tonal ones are passed over | REQ-BEN-05 |
| Passwords and keys live in the Keychain, and setup asks for every field and says where it is | ADR-0005, REQ-SEC-05 |

## Listening and rating

| Preference | Where |
|---|---|
| Takes play from the top, at matched loudness | REQ-FBK-01 |
| Every take is equal; there is no main take | REQ-FBK-01, REQ-FBK-02 |
| Folds open and close only when clicked, and survive saves and reloads | REQ-FBK-01 |
| 0 to 5 stars, 3 or more a pass; resolved or unresolved, clicked on purpose | ADR-0012, REQ-FBK-02, REQ-FBK-06 |
| A pass is a pass: a passed take renders again only when something it depends on changed, and keeps its sounds | REQ-BEN-14, REQ-BEN-15 |
| Unresolved takes are the agents' work queue | REQ-QRY-12, CLAUDE.md rule 2 |
| Every benchmark gets a take per piece of software, built the way that software's users would | spec/components/benchmarks.md (North star), issue #30 |

## Music

| Preference | Where |
|---|---|
| Sidechain: `exaggerated` (24 dB, 430 ms) is the production duck; over-the-top for short moments around build-ups and drops | BM-SC-01 in spec/components/benchmarks.md, examples/projects/sidechain/README.md |
| Drum patterns: the synth-kit BM-DRUM-03 was "too mechanical"; patterns like it want breakbeats or chopped samples | examples/projects/drum-patterns/README.md |
| A remix keeps only the vocals and a signature sound, and builds the rest new, with real sound design | spec/tracks/love-train.md, the `remix` skill |
| A remix runs at the source's tempo, so the vocals are never stretched; nobody quotes the lyrics | the `remix` skill |
| The first track's palette: spoken word, Detroit techno, a Jersey garage twist, West African drums | spec/components/benchmarks.md (North star) |

## Working with agents

| Preference | Where |
|---|---|
| Agents may take the next best step without asking; stability and quality over speed | CLAUDE.md, docs/roadmap.md |
| What we learn goes in the repo, not only in an agent's memory | REQ-PRC-07, CLAUDE.md rule 15 |
| Plain words, and no gendered pronouns for people whose pronouns aren't stated | CLAUDE.md rule 14 |
| One branch per PR from the latest main, no stacking; Dean merges | CLAUDE.md rule 4, REQ-PRC-05 |
| Changes to Dean's checkout need Dean's own go-ahead, typed in the thread and naming the action; mph's data folder may be managed with judgement | the `mac-work` skill |
| Don't offer to clear caches outside mph's data folder | the `mac-work` skill |
| Leaving mph's Renoise open after a render is fine | the `mac-work` skill |
