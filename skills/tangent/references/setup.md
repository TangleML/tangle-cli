# Tangent / Autoresearch Setup

Tangent uses the **Tangle CLI** (`tangle`, package `tangle-cli`) to run ML
pipelines against a Tangle API backend. Skills live in-repo (checked into the `tangle-cli` repo), so there is no separate sync or bundle-refresh step — relative
cross-references resolve directly on disk.

> See [`../OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) for the single source of
> truth on the CLI surface, auth, artifacts, and the resolved defaults. This file
> only covers install + connect + verify.

## 1. Install / run the CLI

The CLI is consumed from a checkout of the repo. Run every command as
`uv run tangle …`:

```bash
uv run tangle quickstart
uv run tangle --help
uv run tangle sdk --help
uv run tangle api --help
```

`uv` resolves dependencies against public PyPI. In the `tangle-cli` workspace, `uv`
installs the workspace `tangle-api` package automatically for dev/tests. The
`[native]` extra enables the static API-backed commands and the handwritten
`TangleApiClient` wrapper.

> Once `tangle-cli` is promoted to the public OSS package, you will be able to
> `pip install 'tangle-cli[native]'` and invoke `tangle …` directly. Until then,
> use `uv run tangle …` from a checkout of the repo.

Then discover available commands:
```bash
uv run tangle quickstart
```

Help is standard `--help` (there is no `--help-extended` / `--help-full`).

## 2. Configure the backend

Auth is **explicit and layered**: an explicit CLI option beats a `--config` file
value, which beats an environment default. There is no `auth` command group — you
point the CLI at a backend and attach a credential.

| CLI option | Env var(s) | Purpose |
|---|---|---|
| `--base-url` | `TANGLE_API_URL` | API origin. Defaults to the local dev API URL when omitted. |
| `--token` | `TANGLE_API_TOKEN` | Bearer-token shorthand. |
| `--auth-header` | `TANGLE_API_AUTH_HEADER`, `TANGLE_AUTH_HEADER` | Full `Authorization` value such as `Bearer …` or `Basic …`. |
| `-H` / `--header` | `TANGLE_API_HEADERS` | Extra headers. Repeatable as CLI flags; env accepts a JSON object or newline-separated `Name: value` entries. |
| `--config` | — | YAML/JSON defaults (single object, a list, or `_defaults` + `configs`). |
| — | `TANGLE_VERBOSE=1` | Redacted HTTP request/response diagnostics. |

Pick **one** credential mechanism:

```bash
# Bearer-token shorthand
export TANGLE_API_URL=https://api.example
export TANGLE_API_TOKEN='…'

# …or a full Authorization header (Basic / custom schemes)
export TANGLE_API_AUTH_HEADER='Bearer …'

# …or arbitrary extra headers (e.g. a gateway auth header)
export TANGLE_API_HEADERS='X-Gateway-Auth: …'
```

Or pass them per-command:

```bash
uv run tangle sdk pipeline-runs submit pipeline.yaml \
  --base-url https://api.example \
  --auth-header 'Bearer …' \
  -H 'X-Gateway-Auth: …' \
  --log-type console
```

If you omit `--base-url` / `TANGLE_API_URL`, the CLI targets the local dev backend
(`http://localhost:8000`), which needs no credentials. For a guided walkthrough of
picking a credential mechanism and interpreting auth failures, see
[`../agents/auth-wizard.md`](../agents/auth-wizard.md).

## 3. Verify access

Run a cheap, read-only call. If it returns (even with zero results), your backend
URL and credentials are wired correctly:

```bash
uv run tangle sdk pipeline-runs search --limit 1
```

Interpreting failures:

| Response | Meaning |
|---|---|
| `401 Unauthorized` | Missing/invalid credential — check `--token` / `--auth-header` / `-H`. |
| `403 Forbidden` | Authenticated, but not permitted — wrong identity or scope. |
| `429 Too Many Requests` | Rate-limited — back off and retry. |
| Connection error | Wrong/unreachable `--base-url`; confirm the backend is running. |

## 4. Running commands

The unified CLI is `tangle`, split into `tangle sdk …` (hand-written
SDK/local/compound commands) and `tangle api …` (auto-generated API wrappers):

```bash
uv run tangle quickstart
uv run tangle sdk pipeline-runs submit pipeline.yaml --arg key=value
uv run tangle sdk pipeline-runs details RUN_ID --include-execution-state
uv run tangle sdk pipeline-runs logs EXECUTION_ID
uv run tangle sdk artifacts get RUN_ID -q '{"tasks": {"TaskName": ["output"]}}'
```

For checking run status, see [`tangle-tools.md`](tangle-tools.md) — prefer the
light status summary (`tangle sdk pipeline-runs status RUN_ID`) and graph-state
(`tangle sdk pipeline-runs graph-state EXECUTION_ID`) for polling over the heavy
`details … --include-execution-state` payload.

**Do not memorize a static command list.** Run `uv run tangle quickstart` to
discover commands, and `uv run tangle sdk <group> --help` for detailed usage.

## 5. Artifacts

Use `tangle sdk artifacts` for artifact metadata — it returns records of the form
`{id, uri, size, hash}`. The `uri` is backend-agnostic (under the OSS `tangle`
backend it is a HuggingFace `hf://…` URI), so read the `uri` field rather than
assuming a scheme:

```bash
uv run tangle sdk artifacts get RUN_ID -q '{"tasks": {"TaskName": ["output"]}}'
```

`artifacts get` is **metadata-only** — there is no download-to-disk command. To
fetch bytes, use the signed-URL recipe in
[`../OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §5 (`artifacts get` →
`tangle api artifacts signed-artifact-url` → fetch with `curl` or `huggingface_hub`).

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `401 Unauthorized` from the verify call | Set a credential: `TANGLE_API_TOKEN`, `TANGLE_API_AUTH_HEADER`, or `-H`. See §2. |
| `403 Forbidden` | Authenticated but not permitted — re-run with a different `--token` for the right identity. |
| Connection error / timeout | Wrong or unreachable `--base-url` / `TANGLE_API_URL`; confirm the backend is up. |
| Want to see the raw HTTP exchange | Set `TANGLE_VERBOSE=1` for redacted request/response diagnostics. |
| Import error from `from tangle_cli.client import TangleApiClient` | Install the `[native]` extra (the client needs the native bindings). |
