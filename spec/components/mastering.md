# Mastering and delivery

**Status:** draft (built in step 4)

### REQ-MST-01: Mastering in Renoise first
The master chain (EQ, bus compression, limiter) lives on Renoise's master
track, so it can be tweaked live. An external stage is added only if
BM-MST-01 can't be met in Renoise.
- **Why:** Dean wants Renoise as the last creative tool, and an external
  stage only when it earns its place.
- **Verify:** measure

### REQ-MST-02: Delivery step
`mph deliver` takes the 32-bit float master, applies REQ-AUD-03 (one SRC,
one TPDF dither), writes AIFF with metadata (title, artist, BPM, key), and
then measures it.
- **Why:** one well-tested place for the format change.
- **Verify:** test

### REQ-MST-03: Loudness and peak QA
Delivery measures integrated loudness (ITU-R BS.1770-4 / EBU R128), LRA,
true peak (4x oversampled), clipping, DC offset and low-end correlation.
It fails if outside config targets.
- **Why:** the numbers mastering engineers check, applied every time.
- **Verify:** test

### REQ-MST-04: Licence marker
Every delivered file's report carries `licence:clear` if every ingredient
asset has a publishable licence, and `licence:private` otherwise.
- **Why:** private use is fine, but Dean wants to know which tracks *could*
  be published.
- **Verify:** test
