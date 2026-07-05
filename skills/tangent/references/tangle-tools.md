# Tangle CLI Reference

This is the in-skill mirror of the CLI surface defined in
[`OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md). When a command, flag, env var, or
recipe is ambiguous, that document is the source of truth — this file does not
re-derive it.

**Always use the `tangle` CLI commands via Bash.** The skills drive the OSS core
(`tangle sdk …` and `tangle api …`) and never any wrapper/hook layer.

**Do not rely on a static list of commands.** The `tangle-cli` repo is updated frequently
with new commands and flags — discover the current surface with `--help` and
`quickstart` (below) rather than memorizing it.

## Install / Invoke

For normal Tangent usage, install the published CLI as a persistent uv tool:

```bash
uv tool install tangle-cli
tangle quickstart
tangle --help
tangle sdk --help
tangle api --help
```

For one-off execution without a persistent install, use `uvx`:

```bash
uvx --from tangle-cli tangle --help
uvx --from tangle-cli tangle quickstart
```

Generic Python environments may also use `pip install tangle-cli`; use
`uv pip install tangle-cli` only inside an explicitly managed virtualenv.

When intentionally validating a local checkout of the `tangle-cli` repo, prefix
examples with `uv run` (for example, `uv run tangle quickstart`). Skill examples
otherwise use the installed-tool form (`tangle …` / `tangle-cli …`).

The default `tangle-cli` install includes `tangle-api` and enables static API-backed commands plus the handwritten `TangleApiClient` (see [Programmatic client](#programmatic-client-python)). In the `tangle-cli` workspace, `uv` installs the workspace `tangle-api` package automatically for dev/tests. The old `native` extra is a compatibility/no-op alias.

## Discover Available Commands

```bash
tangle quickstart
```

This prints static onboarding text. Use it at the start of every session to learn
what's available, then drill in with `--help`.

## Get Help on a Specific Command

Help is standard cyclopts `--help` — there is no `--help-extended` /
`--help-full`:

```bash
tangle sdk <group> <command> --help
tangle api <group> <command> --help
```

For broader docs, point at `tangle sdk <group> --help`, the curated standard
library (`tangle sdk published-components library`), and the public OSS
docs at `github.com/TangleML/website/tree/master/docs`.

## Running Commands

The CLI splits into two families: `tangle api …` (auto-generated OpenAPI
wrappers) and `tangle sdk …` (hand-written SDK / local / compound commands).
Most workflow commands live under `tangle sdk`:

```bash
tangle sdk pipeline-runs submit pipeline.yaml --arg shop=acme --annotation session=2026-06-23-ranking
tangle sdk pipeline-runs details RUN_ID --include-execution-state
```

Auth flags (see [Auth & environment](#auth--environment)) attach to any
API-backed command.

## Submitting a Run (applies to ALL pipelines, ALL agents)

The OSS `submit` reality is simpler than the internal one — there is no
dehydration step, no wait flag, and no source-attribution annotation:

### 1. Hydrate is the default

`submit` resolves component versions by default. There is **no** dehydration
check and **no** "dehydrate first" guard. If you want to submit a spec exactly as
written (no version resolution), pass `--no-hydrate`. Hydration is a no-op on a
spec that is already fully inline.

### 2. `submit` never waits

There is **no** `--no-wait` flag — `submit` returns as soon as the run is
created. To block until completion, use a separate `wait` (see
[Waiting / polling](#waiting--polling)).

### 3. Run arguments via `--arg` / `--args-json`

There is **no** `-f config.yaml`. Pass pipeline run arguments with repeatable
`--arg K=V` flags, or `--args-json '<JSON>'` (or `--args-json @file.json` when a
file is preferred):

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg model=baseline --arg epochs=3

tangle sdk pipeline-runs submit pipeline.yaml \
  --args-json '{"model": "baseline", "epochs": 3}'
```

`--config` carries CLI-option defaults (base-url / auth / log-type), **not**
pipeline run args.

### 4. Annotations are generic and optional

There is no mandatory annotation. Use portable annotations to make runs
searchable; all are optional:

| Key | Value | Typical use |
|-----|-------|-------------|
| `session` | `YYYY-MM-DD-scenario` | Auto-loop grouping |
| `round` | `"1"`, `"2"`, … | Auto-loop iteration |
| `type` | experiment type | Auto-loop |
| `label` | short description | Auto-loop |
| `source` | `tangle-cli` | Optional provenance marker (only if you want it) |

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg model=baseline \
  --annotation session=2026-06-23-ranking \
  --annotation round=1 \
  --annotation type=baseline \
  --annotation label="initial ranking run"
```

Or set after submission:

```bash
tangle sdk pipeline-runs annotations set RUN_ID session 2026-06-23-ranking
tangle sdk pipeline-runs annotations list RUN_ID
tangle sdk pipeline-runs annotations delete RUN_ID round
```

### Preview without submitting

```bash
tangle sdk pipeline-runs submit pipeline.yaml --dry-run
```

`--dry-run` prints the submit body and creates no run.

### Canonical submit command

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg K=V --annotation session=YYYY-MM-DD-scenario
```

## Validating & Editing Pipelines

Local pipeline operations live under `pipelines` (NOT `pipeline-runs`):

```bash
tangle sdk pipelines validate pipeline.yaml
tangle sdk pipelines layout pipeline.yaml [--recursive] [-o out.yaml]
tangle sdk pipelines hydrate template.yaml -o out.yaml [--var K=V]
tangle sdk pipelines diagram pipeline.yaml   # Mermaid (no GUI viewer)
```

There is no `dehydrate` command or `--dehydrate` flag in OSS. To iterate on an
existing run, `export` the root spec as-is, hand-edit, then `submit` (hydrate is
the default) — see [`references/iterating-on-runs.md`](iterating-on-runs.md).

## Checking Run Status (Light vs Heavy)

**Light** — use for polling. Prefer the purpose-built CLI commands over a
hand-rolled Python loop:

```bash
tangle sdk pipeline-runs status RUN_ID            # run + derived status summary
tangle sdk pipeline-runs graph-state EXECUTION_ID  # graph execution state
```

**Heavy** — use only after completion, for debugging or extracting
`execution_id`s:

```bash
tangle sdk pipeline-runs details RUN_ID --include-execution-state
tangle sdk pipeline-runs details RUN_ID --include-implementations
tangle sdk pipeline-runs details RUN_ID --include-annotations
tangle sdk pipeline-runs details RUN_ID --execution-id EXEC_ID
```

## Waiting / Polling

To block until a run completes (bounded):

```bash
tangle sdk pipeline-runs wait RUN_ID \
  --max-wait 600 --poll-interval 10 [--exit-on-first-failure]
```

Defaults: `--max-wait` 600s, `--poll-interval` 10s.

## Searching Runs

```bash
tangle sdk pipeline-runs search --name NAME \
  [--created-by USER] [--annotation K=V] \
  [--start-date DATE] [--end-date DATE] [--limit N] [--query JSON] [QUERY]
```

## Exporting a Run

```bash
tangle sdk pipeline-runs export RUN_ID --output out.yaml   # omit --output to print to stdout
```

Exports the root spec as-is; there is no `--dehydrate`.

## Fetching Container Logs

For application logs (stack traces, code errors), keyed by **EXECUTION_ID**:

```bash
tangle sdk pipeline-runs logs EXECUTION_ID
```

This backend-native container log surface is the **only** log surface the Tangle
backend stores.

### System events (OOM, eviction, scheduling, "pod not found")

The Tangle backend does **not** store Kubernetes/system events — these are
**launcher-specific**. Container logs answer "what did the code print?";
system events answer "why did the pod disappear?". Consult your launcher's
runtime:

| Launcher | System-event source |
|---|---|
| `kubernetes`, `google_kubernetes` | `kubectl get events`, `kubectl describe pod <pod>` |
| `local_docker` | `docker logs <container>`, `docker inspect <container>` |
| `skypilot` | the cluster console / `sky logs` |
| `huggingface` | the Space's logs in the HF UI |

This is a genuine degradation vs a unified system-event search: there is no
single place to search infra failures across launchers. For infra-failure
diagnosis, lean on container logs plus your launcher's native events — see
[`agents/debugger.md`](../agents/debugger.md).

## Artifacts

`tangle sdk artifacts get` is **metadata-only**. It returns records of the form
`{id, uri, size, hash}` (and a `count`); the `uri` is backend-agnostic — read the
`uri` field, do not assume a scheme. Under the OSS backend, URIs look like
`hf://datasets/<user>/<repo>@<branch>/<path>`.

```bash
tangle sdk artifacts get RUN_ID -q '{"artifact_ids":["<artifact-id>"]}'
```

`-q`/`--query` is a JSON string with optional keys `tasks`, `components`,
`executions`, `artifact_ids`.

There is **no** `artifacts download` / `-o`-to-disk command. To fetch bytes (the
standard recipe — see [`OSS-CONVENTIONS.md` §5](../OSS-CONVENTIONS.md)):

1. Get metadata and read the `uri` (above).
2. Ask the backend for a signed URL:
   ```bash
   tangle api artifacts signed-artifact-url --id <artifact-id>
   ```
3. Fetch with a generic client — `curl -L "<signed-url>" -o ./out`, or for
   `hf://` URIs `huggingface_hub` (`hf_hub_download` / `snapshot_download`).

Metadata-only is sufficient for many checks (existence / size / hash). Only fetch
bytes where per-example or metric-content analysis genuinely requires it.

## Components

Local authoring lives under `components`; the registry lives under
`published-components`.

```bash
# Generate a component from a Python source file (user-built image)
tangle sdk components generate from-python source.py \
  [--function NAME] [--image REG/IMG:TAG] [--output OUT] [--name NAME] \
  [--mode inline|bundle] [--dependencies-from REQ] [--strip-code] [--resolve-root DIR]

# Bump a local component's version
tangle sdk components bump-version component.yaml [--set-version V] [--update-timestamp]
```

There is no image build/push in the CLI. Build and push the image yourself (your
own docker/podman), then pass `--image <registry/img:tag>` to
`generate from-python`. `set-container-image` is a stub and must not be used. See
[`agents/builder.md`](../agents/builder.md).

```bash
# Publish to the registry
tangle sdk published-components publish component.yaml \
  [--image …] [--name …] [--description …] [--annotations JSON] [--dry-run] [--published-by …]

# Inspect (exactly one of NAME or --digest)
tangle sdk published-components inspect --name NAME --full-spec
tangle sdk published-components inspect --digest DIGEST

# Search — keyword/name/digest only
tangle sdk published-components search NAME \
  [--digest D] [--published-by U] [--include-deprecated]

# Deprecate
tangle sdk published-components deprecate DIGEST [--superseded-by DIGEST]

# Curated standard library
tangle sdk published-components library
```

To publish many components at once, pass a `--config` YAML/JSON list (or
`_defaults` + `configs`) to the same `published-components publish` — it
aggregates and exits nonzero on any error.

Component search is keyword/name/digest only (no semantic / fuzzy / regex /
schema variants). The remote-component-library feature is **off by default** in
OSS, so treat component discovery as **optional** and tolerate empty results on a
fresh install — never a hard prerequisite of a workflow step.

## Secrets

```bash
tangle sdk secrets list
tangle sdk secrets create NAME --from-env ENVVAR [-d DESCRIPTION] [--expires-at WHEN]
tangle sdk secrets update NAME --from-env ENVVAR
tangle sdk secrets delete NAME [--force]
```

Prefer `--from-env`/`-e ENVVAR` over `--value`/`-v` to avoid leaking the value
into shell history. `delete` prompts unless `--force`. Secrets belong to the
authenticating identity — see [`references/secrets.md`](secrets.md).

## Cancelling a Run

```bash
tangle sdk pipeline-runs cancel RUN_ID
```

## Programmatic client (Python)

Prefer the CLI for status/polling. When you genuinely need Python, the stable
public wrapper is `tangle_cli.client.TangleApiClient`:

```python
from tangle_cli.client import TangleApiClient

# Defaults to the local dev backend; pass base_url + auth for a remote backend.
client = TangleApiClient(
    "http://localhost:8000",
    token=None,        # Bearer token shorthand
    auth_header=None,  # full Authorization value, e.g. "Bearer …" / "Basic …"
    header=None,       # one "Name: value" string or a list of them
    headers=None,      # mapping form, if you prefer a dict
)
run = client.pipeline_runs_get("run-id")
existing = client.find_existing_components(
    ["component-name"],
    published_by_substring="alice@example.com",
)
```

- Constructor: `TangleApiClient(base_url, *, token=, auth_header=, header=, headers=, …)`.
  A bare `TangleApiClient()` only works against the default localhost backend.
- Importing it requires generated bindings. The default `tangle-cli` install
  includes `tangle-api`; custom API projects can provide a compatible local or
  packaged `tangle_api.generated` package before `from tangle_cli.client import …`.
  The top-level `import tangle_cli` intentionally does not eagerly import generated bindings.
- The verified surface is `client.pipeline_runs_get(run_id)` and the
  `find_existing_components(...)` helper. For status/graph state, prefer the CLI:
  `tangle sdk pipeline-runs status RUN_ID` and
  `tangle sdk pipeline-runs graph-state EXECUTION_ID`.

## Auth & environment

OSS auth is explicit and layered: explicit CLI option > `--config` file value >
environment default. There is no `auth` command group.

| CLI option | Env var(s) | Purpose |
|---|---|---|
| `--base-url` | `TANGLE_API_URL` | API origin. Defaults to the local dev API URL when omitted. |
| `--token` | `TANGLE_API_TOKEN` | Bearer-token shorthand. |
| `--auth-header` | `TANGLE_API_AUTH_HEADER`, `TANGLE_AUTH_HEADER` | Full `Authorization` value such as `Bearer …` or `Basic …`. |
| `-H` / `--header` | `TANGLE_API_HEADERS` | Extra headers. Repeatable as CLI flags; env accepts a JSON object or newline-separated `Name: value` entries. |
| `--config` | — | YAML/JSON defaults (single object, a list, or `_defaults` + `configs`). |
| — | `TANGLE_VERBOSE=1` | Redacted HTTP request/response diagnostics. |

Run links: there is no hosted dashboard URL to assume — use `<base-url>/runs/<id>`
or inspect via `tangle sdk pipeline-runs details RUN_ID`.

Example for a protected backend:

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --base-url https://api.example \
  --auth-header 'Bearer …' \
  -H 'X-Gateway-Auth: …' \
  --log-type console
```

For first-time credential setup, see [`agents/auth-wizard.md`](../agents/auth-wizard.md)
and [`references/setup.md`](setup.md).
