---
name: auth-wizard
description: Interactive wizard for configuring Tangle API credentials (base URL + token/header) and verifying access
tools: read, write, bash
---

# Tangent: Auth Wizard

Interactive wizard that guides users through pointing the CLI at a Tangle
backend and configuring credentials so that API-backed commands authenticate.
OSS auth is **explicit and layered** — there is no `auth` command group, no
service-account setup, and no cloud-identity mode. Auth is plain flags and
environment variables (see [OSS-CONVENTIONS.md §4](../OSS-CONVENTIONS.md)).

## Tools

**Use the published `tangle` CLI via Bash.** Install persistently with `uv tool install tangle-cli`, or run one-off commands with `uvx --from tangle-cli tangle …`. Examples below use bare `tangle …`; if intentionally validating a local `tangle-cli` checkout, prefix examples with `uv run`.
Help is standard `--help` (there is no `--help-extended` / `--help-full`).

Resolution precedence: explicit CLI option > `--config` file value > environment
default.

| CLI option | Env var(s) | Purpose |
|---|---|---|
| `--base-url` | `TANGLE_API_URL` | API origin. Defaults to the local dev API URL when omitted. |
| `--token` | `TANGLE_API_TOKEN` | Bearer-token shorthand. |
| `--auth-header` | `TANGLE_API_AUTH_HEADER`, `TANGLE_AUTH_HEADER` | Full `Authorization` value such as `Bearer …` or `Basic …`. |
| `-H` / `--header` | `TANGLE_API_HEADERS` | Extra headers. Repeatable as flags; env accepts JSON or newline-separated `Name: value`. |
| `--config` | — | YAML/JSON defaults (single object, a list, or `_defaults` + `configs`). |

---

## Wizard Flow

When invoked, walk the user through the steps in order. Ask one thing at a time
and explain each answer in plain language before moving on.

```
Tangle Auth Wizard

Let's point the CLI at your backend and get a credential working. I'll ask for:
  1. the backend base URL
  2. which credential mechanism your backend expects
  3. how you'd like to pass it (flag, env var, or --config file)
Then I'll verify access with a cheap read-only call.
```

### Step 1 — Base URL

Ask for the API origin the CLI should talk to.

- Example: `https://api.example` or `http://localhost:8000` (the default local
  dev backend).
- This becomes `--base-url <url>` on any command, or the `TANGLE_API_URL`
  environment variable. If the user is working entirely against the local dev
  backend, they can skip this — the default applies.

### Step 2 — Pick a credential mechanism

Ask how the backend expects to be authenticated, and pick exactly one:

- **`--token`** (`TANGLE_API_TOKEN`) — use when the backend takes a plain
  **bearer token**. The CLI sends `Authorization: Bearer <token>`.
- **`--auth-header`** (`TANGLE_API_AUTH_HEADER`) — use when you need to supply
  the **full `Authorization` value** yourself, e.g. `Basic dXNlcjpwYXNz` or a
  bearer with a non-standard prefix. Pass the entire header value.
- **`-H` / `--header`** (`TANGLE_API_HEADERS`) — use for a **gateway / custom
  header** scheme (e.g. `X-Gateway-Auth: …`), either instead of or in addition
  to the above. Repeatable.

If the user is unsure, ask what their backend's docs say to send, or have them
try `--token` first — it is the most common.

### Step 3 — Show the three ways to pass it

Show all three for the chosen mechanism so the user can pick what fits their
workflow. Using `--token` as the example (substitute `--auth-header` / `-H` as
chosen in Step 2):

**a) Inline flag** — explicit, per-command, highest precedence:

```bash
tangle sdk pipeline-runs search --limit 1 \
  --base-url https://api.example \
  --token '<token>'
```

**b) Environment variable** — set once per shell, applies to every command:

```bash
export TANGLE_API_URL='https://api.example'
export TANGLE_API_TOKEN='<token>'
tangle sdk pipeline-runs search --limit 1
```

For `--auth-header` set `TANGLE_API_AUTH_HEADER`; for `-H` set
`TANGLE_API_HEADERS` (JSON object or newline-separated `Name: value`).

**c) `--config` file** — checked-in/reusable defaults (single object):

```yaml
# tangle.config.yaml
base-url: https://api.example
token: "<token>"
```

```bash
tangle sdk pipeline-runs search --limit 1 --config tangle.config.yaml
```

**Advise against committing secrets.** Prefer the env var, or keep the
`--config` file out of version control if it carries a real token.

### Step 4 — Verify access

Run a cheap, read-only call to confirm the credential works:

```bash
tangle sdk pipeline-runs search --limit 1
```

(attach the auth flags from Step 3 if not using env/`--config`). A clean exit —
even with **zero results** — means auth succeeded and the CLI reached the
backend. An empty list is fine here; this is a connectivity/credential check,
not a data check.

### Step 5 — Interpret the result

Read the failure mode and explain it plainly:

| Symptom | Likely cause | Fix |
|---|---|---|
| **401 Unauthorized** | Missing or invalid credential | Recheck the token/header value; confirm it hasn't expired and matches the mechanism the backend expects (Step 2). |
| **403 Forbidden** | Authenticated, but not permitted | The identity is recognized but lacks access to this resource — confirm the credential is for the right backend/identity. |
| **429 Too Many Requests** | Rate-limited | Back off and retry after a short wait; reduce request frequency. |
| **Connection error / timeout / DNS** | Wrong or unreachable `--base-url` | Recheck the URL (scheme, host, port); confirm the backend is up and reachable from where you're running. |

For redacted HTTP request/response diagnostics, set `TANGLE_VERBOSE=1` and
re-run the verify call — it shows the wire-level exchange (with secrets
redacted) so you can see exactly what was sent.

---

## Key Principles

- **Explain as you go.** After each step, say what the answer means in plain language.
- **One credential mechanism.** Pick `--token` *or* `--auth-header` *or* `-H` (with `-H` optionally layered for a gateway); don't mix bearer schemes.
- **Don't print live secrets back.** Echo placeholders, not the real token value.
- **Prefer env vars for secrets** over inline flags (shell history) and over committed `--config` files.
- **Verify before declaring success.** A clean `pipeline-runs search --limit 1` — even with no results — is the proof that auth works.
