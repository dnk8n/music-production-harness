# The spec

This folder is the product. Code under `src/` is regenerable from it and is
thrown away whenever it stops matching (ADR-0001). When code and spec
disagree, fix the spec first.

## Map

| File | What it holds |
|---|---|
| [vision.md](vision.md) | Why this exists, goals, non-goals, principles |
| [glossary.md](glossary.md) | Shared words. Use these terms in code and issues |
| [architecture.md](architecture.md) | Stages, data flow, boundaries |
| [components/](components/) | One file per component, each with numbered requirements |
| [process/](process/) | How we work: agent workflow, labels, engineering standards |
| [decisions/](decisions/) | ADRs: why we chose what we chose |

## Requirement format

Every requirement or benchmark is a heading with a stable ID, a statement,
a **Why** and a **Verify** method:

```markdown
### REQ-XXX-00: Short title
The system shall ...
- **Why:** the reason, so a future reader can judge whether it still holds.
- **Verify:** test | measure | listen | inspect
```

- `REQ-<AREA>-NN` is a requirement, `BM-<AREA>-NN` is a benchmark (a
  listening test plus objective measurements), `ADR-NNNN` is a decision.
- `test` means an automated test links the ID with `@pytest.mark.req("ID")`.
  `measure` means an automated audio measurement, `listen` means Dean's ear
  through the feedback page, `inspect` means human review.
- IDs never get reused. Retire one by striking it through and saying why.

## Gates

A spec change passes three gates, cheapest first (ADR-0006):

1. **Deterministic:** `mph spec check` runs in CI. It checks unique IDs, Why and
   Verify on every item, that references resolve, ADR status, and test traceability.
2. **Advisory AI review:** an LLM reviewer looks for duplication,
   contradiction and vagueness. It comments and never blocks.
3. **Human:** Dean approves the PR.

## Status

Each component file declares a status: `draft` (being discussed), `accepted`
(build to this) or `built` (code exists and its tests pass).
