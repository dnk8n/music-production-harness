# ADR-0010: Regenerate renders instead of keeping them

**Status:** accepted

## Context

Benchmark renders are 96 kHz 32-bit float: a master, one stem per voice, the
delivered AIFF, and a `work/` folder with two more full renders and a solo
render per voice for measuring. Each one is hundreds of megabytes, and every
change to a score, kit or level makes a new folder. On Dean's laptop they
reached tens of gigabytes within days. Renders are deterministic: SuperCollider
renders are bit-identical, and Renoise renders null below -120 dBFS after a
warm-up (REQ-AUD-05).

## Decision

Keep only fresh renders (spec/components/retention.md): the newest of each
benchmark variant. Delete work files when a render finishes. Superseded
renders keep only their `report.json`. Anything the page needs that is gone is
rendered again on demand.

## Consequences

- Disk use stays around one render per benchmark variant.
- A superseded render made from an older score can only come back by checking
  out the commit that made it (its report names the mph version). Rendering
  from the page gives the current score. This is accepted: the page only plays
  the newest render, and reports and verdicts survive.
- Rendering on demand costs time (about a minute, more when Renoise starts).
- If renders ever stop being deterministic, this decision has to be revisited,
  because "the same again" would no longer hold. BM-MST-02 guards that.
