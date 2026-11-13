# ADR-0013: Stay in Python; the site's stack is chosen next, in issue #31

**Status:** accepted
**Date:** 2026-11-13

## Context

Dean asked (2026-11-13) whether the system should be rebuilt on more
sophisticated third-party frameworks or in another language, and whether
the rating redesign (ADR-0012) should wait for that.

mph orchestrates. Sound is made by SuperCollider and Renoise, and the
delivery conversion by SoX (ADR-0002, ADR-0003). What runs in Python is
config, the catalogue, score data, file generation, measurements and QA,
and the measurements lean on numpy, librosa and pyloudnorm (ADR-0008). The
code is typed (`mypy --strict`), linted with a complexity cap, and has
about 700 tests at over 90% coverage (REQ-ENG-01).

The weak spot is the page: one HTML file of inline JavaScript with no
tests, served by a hand-written standard-library server
(spec/components/browser.md, spec/components/feedback.md). Issue #31
already plans to harden it and pick its stack.

## Decision

- Python stays the language of the whole system. No rewrite.
- Third-party frameworks come in where they earn their place
  (REQ-ENG-05), starting with the site: its server, page and browser tests
  are chosen in issue #31, which comes next.
- Until then, new features keep their rules in plain Python modules that
  the page, the CLI and the query channel share, and the page only shows
  what those modules work out. Rebuilding the page then changes how things
  look, not what they mean.

## Alternatives considered

- *Rewrite in Rust, Go or TypeScript:* nothing here is bound by Python's
  speed, because the heavy work runs in other programs and in numpy. A
  rewrite would trade the audio analysis libraries for weaker ones, and
  weeks of tests for nothing Dean would hear.
- *Fold the rating redesign into #31:* Dean's daily feedback loop would wait
  for a new site, and two risky changes would land in one pull request. The
  ratings are mostly data, rules and query tools, which don't depend on the
  page.

## Consequences

- The rating and idea forms are written in the current page, and rewritten
  when #31 replaces it. They're a small part of the work.
- #31 is the next piece of platform work after ADR-0012's.
