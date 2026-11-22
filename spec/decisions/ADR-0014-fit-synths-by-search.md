# ADR-0014: Fit synths to sounds with an evolution search

**Status:** accepted
**Date:** 2026-11-22

## Context
Dean rated the free remix's first block 1 star (rating 246): its synth
horn was "too video game like" and its voices didn't match. Dean then asked
(2026-11-22) for a system that takes a given sound and makes a synth sound
close to it, in the key and tempo the track needs, maybe as a
reinforcement learning problem, run on Dean's own Mac without cloud
credits, with a local LLM in the loop if that helps.

## Decision
- Fitting a synth to one sound is a search for knob values: differential
  evolution from scipy, which librosa already installs. Each candidate
  renders in SuperCollider as the track would play it and is scored with
  classic measurements (ADR-0008), so no neural network chooses or judges
  the sound (spec/vision.md).
- What comes out is named knobs, which a person pins in a score. The
  reference and the search stay out of renders.
- No LLM turns knobs. Later, a local LLM may propose new parts for a patch
  when the search stalls, choosing from a menu that our code checks.

## Alternatives considered
- *Reinforcement learning:* it learns a policy for decisions made one after
  another, with rewards that come late. Here each sound is one decision, a
  set of knob values, scored at once. Training would take hundreds of
  thousands of renders, and a network would choose the sound.
- *A network trained to map sounds to knobs:* it costs as much to train,
  and it chooses the sound too.
- *A differentiable synth (DDSP):* each synth would be rewritten in a
  machine-learning library, so the sound would come from outside
  SuperCollider and Renoise.
- *An LLM turning knobs:* it takes tens of seconds a guess and guesses
  differently each time. Asked for a train-horn SynthDef on Dean's Mac,
  qwen3:8b thought for 3 minutes and wrote code that wouldn't run
  (2026-11-22).

## Consequences
- scipy becomes a direct dependency. It was already installed with librosa.
- On 2026-11-22 the search found every knob of a hidden 8-knob horn patch
  within 5% of its range in 2,928 renders: 3.5 minutes in a cloud copy and
  4 on Dean's Mac. With the real mph_horn in SuperCollider, it found 6
  hidden knobs within 0.1% in 2,376 renders, a minute in a cloud copy.
- A match against a private recording, such as the song's own horn, can
  run again only where that recording is. The pinned knobs render anywhere.
