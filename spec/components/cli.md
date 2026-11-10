# CLI

**Status:** built

Implementation: `src/mph/cli.py`. The CLI is a thin layer that parses,
wires dependencies, calls one module and renders the result.

### REQ-CLI-01: Commands
`mph status`, `mph doctor`, `mph install <component>`, `mph secrets
list|set|delete`, `mph prune` (REQ-RET-05), `mph spec check`. Later steps add `mph catalogue ...`,
`mph bench ...`, `mph render ...`.
- **Why:** one entry point that's discoverable with `--help`.
- **Verify:** test

### REQ-CLI-02: Status is one screen
`mph status` shows config files in use, data dir, sources, the audio policy,
mastering targets, disk use (REQ-RET-07) and a health summary, in under 15 lines.
- **Why:** a glance tells Dean whether the system is ready.
- **Verify:** test

### REQ-CLI-03: Exit codes
0 is success, 1 is a failed check, 2 is a usage or config error.
- **Why:** scripts and CI depend on stable exit codes.
- **Verify:** test
