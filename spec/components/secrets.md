# Secrets

**Status:** built

Implementation: `src/mph/secrets.py` (ADR-0005).

### REQ-SEC-01: macOS Keychain only
Secrets are generic-password items in the login Keychain under service
`secrets.keychain_service` (default `mph`), with one account per secret name.
- **Why:** Dean asked for the Apple password system. It's encrypted at rest,
  unlocked with the login session, and visible in Keychain Access.
- **Verify:** test

### REQ-SEC-02: Values never pass through argv, logs or files
Setting a secret delegates the prompt to `security` itself on the terminal.
Presence checks use a query that never prints the value.
- **Why:** argv is visible in the process list and shell history, and logs get
  shared in issues.
- **Verify:** test

### REQ-SEC-03: Closed set of known secrets
Only secrets declared in `KNOWN_SECRETS` can be read or written. Each
declares its purpose and where to sign up.
- **Why:** doctor can then report every missing key with a concrete fix, and
  typos can't create stray Keychain items.
- **Verify:** test

### REQ-SEC-04: Optional keys degrade gracefully
Every secret is optional unless marked required. Missing optional keys
produce a doctor warning, and the feature falls back (e.g. Gemini to Ollama).
- **Why:** free-first. The harness must work with zero accounts.
- **Verify:** test
