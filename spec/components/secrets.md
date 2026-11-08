# Secrets

**Status:** built

Implementation: `src/mph/secrets.py` (ADR-0005).

A third-party service needs a **credential**, which is one or more named
fields. For example, Freesound needs a client ID and a client secret, and
Gemini needs one API key.

### REQ-SEC-01: macOS Keychain only, one item per field
Each field is a generic-password item in the login Keychain under service
`secrets.keychain_service` (default `mph`), with account
`<credential>.<field>` (e.g. `freesound.client_secret`).
- **Why:** Dean asked for the Apple password system. It's encrypted at rest,
  unlocked with the login session, and each field shows up by name in
  Keychain Access.
- **Verify:** test

### REQ-SEC-02: Values never pass through argv, logs or files
Values reach `security` over stdin (`security -i`), never as arguments.
Presence checks use a query that never prints the value. Values are
restricted to token characters (letters, digits, `.`, `_`, `-`) so they can't
break the command quoting. Every write is verified by reading it back.
- **Why:** argv is visible in the process list and shell history, logs get
  shared in issues, and `security -i` doesn't report failures in its exit code.
- **Verify:** test

### REQ-SEC-03: Closed set of documented credentials
Only credentials declared in `CREDENTIALS` can be set. Each declares its
provider, purpose and sign-up URL, and each field declares a label, whether
it's hidden while typing, and exactly where on the provider's site to find it.
- **Why:** doctor can report every missing field with a concrete fix, and
  typos can't create stray Keychain items.
- **Verify:** test

### REQ-SEC-04: Optional credentials degrade gracefully
Every credential is optional unless marked required. A missing one produces a
doctor warning, and the feature falls back (e.g. Gemini to Ollama).
- **Why:** free first. The harness must work with zero accounts.
- **Verify:** test

### REQ-SEC-05: Guided setup asks for every field
`mph secrets set <credential>` explains the service, links where to get the
values, then asks for each field in turn, saying where to find it. Secret
fields aren't echoed. A value with the wrong shape is re-asked up to 3 times.
Nothing is written until every field is collected, so an abandoned setup
leaves no partial credential. Notes about extra steps (e.g. Freesound's
one-time OAuth2 sign-in for original-quality downloads) are shown at the end.
- **Why:** Dean found the first version only asked for one opaque "password"
  with no guidance, when Freesound needs two values.
- **Verify:** test
