# ADR-0012: Star ratings with a resolved state replace verdicts

**Status:** accepted
**Date:** 2026-11-13

## Context

Dean judged takes as pass, close or fail, with notes (REQ-FBK-02 until
2026-11-13). Three words couldn't rank takes that all pass, and one word
carried two questions: how good is it, and does anything still have to
happen? The render rule read both from it (REQ-BEN-14): a fail rendered
again on every `mph bench render`, even with nothing changed, and notes on
a pass counted as feedback to act on, whatever they said.

Dean asked (2026-11-13) for 0 to 5 stars, where 3 to 5 is a pass, plus a
resolved or unresolved switch he clicks on purpose, with feedback. 0 stars
without a click is unrated. A resolved take renders again only when forced.
An unresolved one renders again when something changed, and is work for
agents, who find it through MCP. He also asked to start new projects,
benchmarks and variants from the page, with ids filled in when he gives
none.

## Decision

- **A rating is a snapshot:** stars, the status Dean clicked (or none),
  feedback, scope (one take or the whole benchmark), the render it judged
  (score hash, take, `inputs`) and when. Every save is a new row, and the
  newest stands. The history is the table itself.
- **The stars decide what Dean didn't click:** a pass is resolved, a fail
  unresolved. The stored status stays as he gave it (none), and the status
  that follows is worked out when read, in one function, so the rule can
  change without rewriting rows.
- **Resolved freezes a take.** It doesn't render again when code changes,
  only when forced.
- **The render rule asks whether a render could differ:** `inputs`, or for
  a take that chooses its sounds afresh, the sounds it would choose now.
  Feedback alone doesn't render again, because renders are deterministic.
- **Verdicts carry over once,** in the catalogue migration, and the
  `verdict` table stays as it was.
- **Ideas are rows too,** in the catalogue DB, with typed or filled-in ids.
  Whether one is built is worked out from the scores and renders, not
  stored, so nothing has to mark it.
- **Agents only read.** They find unresolved takes and open ideas through
  the query channel (REQ-QRY-12), which stays read-only (ADR-0011). Their
  answers are spec, score and code changes, and the next render's report
  names the commit (REQ-BEN-03), which links a rating to the change that
  answered it.

## Alternatives considered

- *Keep pass, close and fail, and add a priority:* still can't rank passes,
  and it's two controls for what stars and one switch say.
- *Separate star and status events,* each saved on its own click: closer to
  how the buttons look, but the standing rating would have to be stitched
  together from two streams, and a status change would need the stars
  repeated anyway for the history to read well.
- *Make the status a required click:* every rating would cost an extra
  click for the common case, where the stars already say it.
- *Keep rendering resolved takes when code changes,* as passed takes did:
  that was the old rule's way of catching regressions, but Dean asked for
  resolved takes to stay put, and a forced `--all` render still checks
  everything when that's wanted.
- *Drop the `verdict` table:* branches from before ratings would fail on the
  upgraded catalogue until they merge `main`.
- *Let agents write through MCP* (mark a rating addressed, claim work):
  convenient, but it breaks the channel's guarantee that asking can't change
  anything. The commit in each report and the "rendered again since it was
  rated" mark (REQ-FBK-06) cover the same need by reading.

## Consequences

- Takes stop rendering just because they failed: something has to change.
- Old passes show 3 stars until Dean rates them again, and passes that had
  notes show as unresolved, which is what the old rule did with them.
- A branch from before ratings still runs, but it reads and writes verdicts
  the newer code ignores.
- The page's JavaScript grows by the rating and idea forms. Rebuilding the
  page on a tested stack is issue #31 (ADR-0013).
