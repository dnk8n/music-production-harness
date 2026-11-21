# Agent workflow

**Status:** accepted

How Dean, Claude and the gates work together. This adapts the architect's
"brain" idea (ADR-0006): the spec is the knowledge graph, and gates filter
what enters it.

## Loop

1. **Converse.** Dean and the lead agent discuss until they share an
   understanding. The outcome is a spec change, not code.
2. **Gate the spec change.** `mph spec check` (deterministic) runs, then the
   advisory AI review (duplication, contradiction, vagueness), then Dean
   approves the spec PR.
3. **Implement.** An implementation agent works in its own branch or worktree,
   with the spec IDs it implements in the PR description and in
   `@pytest.mark.req` markers.
4. **Review.** A review agent runs CI-equivalent checks (lint, types,
   complexity, security, tests, spec check), then reviews against the spec
   items. Dean reviews the PR and judges benchmarks by ear.
5. **Merge.** Squash-merge to `main`. The spec file status moves from
   `accepted` to `built`.

Optional scaffolding: Dean can drop `NOTES.md` files into folders with short
"how it should look" notes. Agents follow them, and delete them in the PR
that implements them.

### REQ-PRC-01: Spec gate in CI
CI runs `mph spec check` on every PR and fails on structural errors. One of
them is an ID named anywhere in the repo's text (the spec, code, tests,
docs, example projects, skills and scripts) that the spec doesn't define;
a made-up ID in an example or a test ends in `-00`.
- **Why:** the spec is the product, so it gets the same regression guard as
  code. Docs and examples name IDs too (2026-11-21), and a reader who follows
  one must find it.
- **Verify:** test

### REQ-PRC-02: Traceability
Every requirement with `Verify: test` is linked by at least one test
marker. In a spec file whose status is `built`, an unlinked item is an
error. Elsewhere it's a warning (an error with `--strict`), which doubles as
the to-do list for upcoming steps.
- **Why:** proves each built requirement is actually tested.
- **Verify:** test

### REQ-PRC-03: Work is tracked in GitHub
Every outstanding task is a GitHub issue with one `type:` label, one
`area:` label, and one `step:` label where it belongs to a plan step.
PRs reference the issues and spec IDs they address.
- **Why:** context lives in GitHub, so any future agent can retrieve it on demand.
- **Verify:** inspect

### REQ-PRC-04: Labels are code
The label set lives in `.github/labels.yml` and is synced by a workflow.
- **Why:** labels stay consistent and reviewable, with no drift from clicking around in the UI.
- **Verify:** inspect

### REQ-PRC-05: PRs merge only when tested against the latest main
`main` is protected: changes arrive by pull request, the CI checks must
pass, and the branch must be up to date with `main` before it can merge.
Force pushes and deletion of `main` are blocked.
- **Why:** two PRs can each pass alone and still break `main` together
  (PRs #24 and #25 did). Requiring an up-to-date branch makes CI test the
  combination before it lands.
- **Verify:** inspect (repository ruleset on GitHub)

### REQ-PRC-06: Where work runs
Code changes are made and checked in cloud copies of the repo, each on its
own branch, so several pieces of work can run side by side without tying
up Dean's Mac. Anything that needs Renoise, demucs, SuperCollider with
Dean's plugins, the Keychain or the real sample folders runs on Dean's Mac
over Remote Control, from the local checkout, after the PR is pushed.
- **Why:** the cloud has no Mac apps or samples, and the Mac shouldn't be
  shared between parallel threads.
- **Verify:** inspect

### REQ-PRC-07: What we learn goes in the repo
What Dean and the agents learn while working goes into the repo, where
anyone who clones it finds it: rules, facts and measurements in the spec,
with their Why; ways of working (how to run things on the Mac, change the
Renoise bridge, make a remix, pick sounds, set up a cloud copy) as skills in
`.claude/skills/`; steps that can run as scripts; the short rules every
session needs in `CLAUDE.md`; Dean's projects, with what he thought of
them, in `examples/projects/` (spec/components/projects.md); and in `docs/`
the story so far, the reference Mac, the plan ahead, research notes and a
guide that takes a new user from a fresh Mac to a rendered demo. An agent's
memory and the chat are working notes, not the record: anything in them a
new session would need is in the repo, so the memory could be deleted and
nothing would be lost but what is on Dean's Mac (his samples, catalogue,
ratings and renders) or can be made again. A skill points to the spec items
it rests on rather than copying them, and a PR that learns something new
updates the skill, spec or doc it belongs to.
- **Why:** Dean's rule (2026-11-15): everything learned together must be
  reproducible and available to future users of the repo, who won't have
  this project's memory or chats. On 2026-11-21 he set the bar: a third
  party with a Mac like his can get to a similar state from the repo alone.
- **Verify:** inspect

## Labels

| Group | Labels | Meaning |
|---|---|---|
| type | `type:spec`, `type:dev`, `type:qa`, `type:infra`, `type:feedback`, `type:bug`, `type:research` | What kind of work |
| area | `area:config`, `area:doctor`, `area:catalogue`, `area:synthesis`, `area:vocals`, `area:renoise`, `area:mastering`, `area:benchmarks`, `area:ai` | Which component |
| step | `step:1-skeleton`, `step:2-catalogue`, `step:3-benchmarks`, `step:4-renoise` | Plan step |
| priority | `p0`, `p1`, `p2` | Now, next, later |
| status | `needs:decision`, `needs:listening`, `blocked` | Waiting on Dean or something else |
| licence | `licence:clear` (reserved) | Only for tracks whose every ingredient is publishable |
