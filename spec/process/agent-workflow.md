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
CI runs `mph spec check` on every PR and fails on structural errors.
- **Why:** the spec is the product, so it gets the same regression guard as code.
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

## Labels

| Group | Labels | Meaning |
|---|---|---|
| type | `type:spec`, `type:dev`, `type:qa`, `type:infra`, `type:feedback`, `type:bug`, `type:research` | What kind of work |
| area | `area:config`, `area:doctor`, `area:catalogue`, `area:synthesis`, `area:vocals`, `area:renoise`, `area:mastering`, `area:benchmarks`, `area:ai` | Which component |
| step | `step:1-skeleton`, `step:2-catalogue`, `step:3-benchmarks`, `step:4-renoise` | Plan step |
| priority | `p0`, `p1`, `p2` | Now, next, later |
| status | `needs:decision`, `needs:listening`, `blocked` | Waiting on Dean or something else |
| licence | `licence:clear` (reserved) | Only for tracks whose every ingredient is publishable |
