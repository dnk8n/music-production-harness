# Audio policy

**Status:** accepted (rules), enforcement lands with step 3

Dean's rule: no pointless conversions and no empty bytes. Production runs
at higher quality, and delivery is 48 kHz / 24-bit, the highest the final
output needs (ADR-0003).

### REQ-AUD-01: Sources are never format-converted on disk
No resampled or re-encoded copies of source audio get made just to
normalise format. A 44.1 kHz / 16-bit sample or vocal enters the project
exactly as stored.
- **Why:** every conversion costs quality and disk, and the engine resamples
  on playback anyway.
- **Verify:** test

### REQ-AUD-02: Intermediates are 32-bit float at the production rate
Synth stems, processed derivatives and the Renoise master render are 32-bit
float WAV at `audio.production_sample_rate` (default 96 kHz). Derivatives of
a sample (trims, chops) keep the sample's native rate.
- **Why:** float has headroom (no clipping between stages) and needs no
  dither. A higher rate pushes synth aliasing out of the audible band.
- **Verify:** measure

### REQ-AUD-03: One SRC and one dither, at delivery only
The delivery step converts the master to `delivery_sample_rate` with
libsoxr at very high quality (linear phase), then applies TPDF dither once
while reducing to `delivery_bit_depth`. No other stage changes bit depth or
rate on purpose.
- **Why:** truncating or dithering more than once accumulates noise, and SRC
  belongs where its filter can be verified once.
- **Verify:** measure

### REQ-AUD-04: Synths oversample where aliasing matters
Non-linear synthesis (the 303 filter, distortion, waveshaping) runs
oversampled or uses band-limited oscillators, whatever the production rate.
- **Why:** aliasing from non-linear stages is audible as harsh inharmonic
  tones, and it's the main quality gap between cheap and pro synth renders.
- **Verify:** measure

### REQ-AUD-05: Deterministic renders
Rendering the same score, config and sources twice gives bit-identical
stems from SuperCollider NRT. For Renoise renders, the null test of two
renders is below -120 dBFS.
- **Why:** reproducibility is what makes the system debuggable and the spec
  meaningful.
- **Verify:** measure

### REQ-AUD-06: No empty bytes
Deliverables carry no padding beyond the spec. A render starts at the first
musical event (no more than 10 ms lead-in), ends after the declared tail, and
is never zero-padded to a fixed length.
- **Why:** silent padding wastes space and breaks DJ cueing.
- **Verify:** measure
