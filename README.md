# tangle-cli

[WIP] Private experimental/lab CLI for Tangle, the open-source ML pipeline orchestration platform.

This lab repo is used to iterate on the next CLI shape before promoting changes to the public OSS package. The CLI is built with [Cyclopts](https://cyclopts.readthedocs.io/) and exposes two top-level command groups:

- `tangle api` for OpenAPI-driven commands generated from the Tangle FastAPI schema.
- `tangle sdk` for local SDK/scaffold commands and published-component inspection helpers.

## Run locally

```bash
uv run tangle --help
uv run tangle api --help
uv run tangle sdk --help
uv run tangle sdk components --help
uv run tangle sdk published-components --help
```

## SDK commands

SDK/scaffold commands live under `tangle sdk`. Local component generation/spec helpers are intentionally nested under `sdk components`; root-level `tangle components ...` is not registered in this lab CLI. Published/registry component inspection lives separately under `sdk published-components` so local component authoring and published component lookup do not share the same command group.

```bash
uv run tangle sdk components --help
uv run tangle sdk components annotations get
uv run tangle sdk components annotations set
uv run tangle sdk published-components --help
uv run tangle sdk published-components search transformer
uv run tangle sdk published-components inspect transformer
uv run tangle sdk published-components inspect --digest sha256:...
uv run tangle sdk published-components library
```

## API commands

API commands are pre-generated from the checked-in official Tangle FastAPI/OpenAPI snapshot, so `tangle api --help` shows resource command groups immediately on a cold cache and command invocations do not require `refresh` first. By default, the CLI uses `--schema-source auto`: official static operations are always present, and cached live-backend operations discovered by `tangle api refresh` are included as extensions when they exist. Cached schemas do not override official operations with the same method/path; official definitions win.

You can refresh the local schema cache explicitly for a live backend with:

```bash
uv run tangle api refresh
```

When refreshing, the CLI fetches the schema from:

```text
$TANGLE_API_URL/openapi.json
```

or, when `TANGLE_API_URL` is unset:

```text
http://localhost:8000/openapi.json
```

You can also pass a base URL explicitly. This caches non-official/live backend schemas such as Oasis for automatic extension discovery:

```bash
uv run tangle api refresh --base-url http://localhost:8000
uv run tangle api refresh --base-url https://oasis.shopify.io
```

To delete the live/dynamic schema cache for a base URL without touching the
checked-in official snapshot, run:

```bash
uv run tangle api reset-cache --base-url https://oasis.shopify.io
```

When `--base-url` is omitted, `reset-cache` uses the same base URL resolution as
`refresh`: `TANGLE_API_URL`, then the default local API URL.

Schemas are cached under the OS-specific user cache directory via `platformdirs`, with an `openapi` subdirectory. Common examples include:

```text
macOS:   ~/Library/Caches/tangle-cli/openapi/
Linux:   ~/.cache/tangle-cli/openapi/
Windows: %LOCALAPPDATA%\\TangleML\\tangle-cli\\Cache\\openapi\\
```

Override the OpenAPI schema cache directory with:

```bash
export TANGLE_CLI_CACHE_DIR=/path/to/openapi-schema-cache
```

If your backend requires bearer auth, set a token:

```bash
export TANGLE_API_TOKEN=...
```

or pass one per command:

```bash
uv run tangle api refresh --token ...
```

For other `Authorization` schemes, use `--auth-header` or `TANGLE_API_AUTH_HEADER` (also accepts the reference-compatible `TANGLE_AUTH_HEADER`). Values can be either the raw authorization value or `Authorization: value`:

```bash
export TANGLE_API_AUTH_HEADER='Basic ...'
uv run tangle api refresh --auth-header 'Bearer ...'
```

For arbitrary auth or routing headers, including `Cloud-Auth`, use `--header` (alias `-H`). `TANGLE_API_HEADERS` accepts a JSON object (or a newline-separated list of `Name: value` entries):

```bash
export TANGLE_API_HEADERS='{"Cloud-Auth":"...","X-Api-Key":"..."}'
uv run tangle api refresh --header 'Cloud-Auth: ...'
uv run tangle api pipeline-runs list -H 'Cloud-Auth: ...'
```

Repeated `--header 'Name: value'` flags can be used with both `tangle api refresh` and generated API commands. Header values are sent to the backend but are not printed by the CLI.

Schema source modes are:

- `--schema-source auto` (default): official static operations plus cached-only backend extensions when a cache exists.
- `--schema-source official`: only the checked-in official static schema (OSS-only/core commands).
- `--schema-source cache`: only the schema previously written by `tangle api refresh` for the selected base URL.

For resource help, put the option on the resource group:

```bash
uv run tangle api published-components --schema-source official --help
uv run tangle api published-components --schema-source cache --help
```

For endpoint calls, put it on the endpoint command:

```bash
uv run tangle api published-components experimental-search --schema-source cache --base-url https://oasis.shopify.io --body @query.json
```

Omit `--schema-source` to use `auto`, which includes cached-only backend
extensions after refresh while preserving official definitions for core
operations. Use `--schema-source official` to force OSS-only/core commands.

## Static and dynamic command examples

OpenAPI resource paths are available as command groups from the checked-in official schema, with cached-only backend extensions included in auto mode after refresh. For example, `/api/pipeline_runs/` becomes `pipeline-runs`, `/api/components/{digest}` becomes `components`, and `/api/published_components/` becomes `published-components`:

```bash
uv run tangle api pipeline-runs list
uv run tangle api pipeline-runs get RUN_ID
uv run tangle api pipeline-runs cancel RUN_ID
uv run tangle api components get DIGEST
uv run tangle api published-components list
uv run tangle api component-libraries get LIBRARY_ID
```

Path parameters are positional arguments and query parameters become options. Check generated help for the exact options exposed by the active schema source:

```bash
uv run tangle api pipeline-runs list --help
uv run tangle api pipeline-runs list --include-execution-stats
uv run tangle api pipeline-runs list --auth-header 'Bearer ...'
uv run tangle api pipeline-runs list --header 'Cloud-Auth: ...'
```

Simple JSON request body fields are exposed as options when possible. For complex bodies, pass JSON directly or read it from a file with `@file`:

```bash
uv run tangle api pipeline-runs create --help
uv run tangle api pipeline-runs create --body @pipeline-run.json
```

Responses are printed as JSON when the backend returns JSON.

## Programmatic client

The stable public wrapper for downstream Python tools is:

```python
from tangle_cli.client import TangleApiClient

client = TangleApiClient("http://localhost:8000")
run = client.get_pipeline_run("run-id")
```

`TangleApiClient` uses checked-in endpoint methods generated offline from
`tangle_cli/openapi/openapi.json`, so normal imports do not fetch or parse the
OpenAPI schema.

To refresh the checked-in generated methods/models from the official Tangle
backend submodule, run:

```bash
git submodule update --init --recursive
uv sync --group codegen
uv run --group codegen python -m tangle_cli.openapi.codegen \
  --model-extension-module tangle_cli.generated_model_extensions
uv run pytest
```

With no source flags, codegen loads OpenAPI from the default official backend
submodule at `third_party/tangle`, writes `tangle_cli/openapi/openapi.json`, and
regenerates `tangle_cli/generated`. The backend import creates a database engine
at import time; codegen points it at a temporary SQLite database unless
`--backend-database-uri` is provided. If the submodule is missing, initialize it
with `git submodule update --init --recursive`.

`--out` controls where generated support modules are written. It defaults to
`tangle_cli/generated`, which is the package support module used by the public
`tangle_cli/client.py` wrapper. `--operations-class-name` controls the generated
operations class name in `<out>/operations.py`; it defaults to
`GeneratedTangleApiOperations`. `--model-extension-module` points codegen at an
importable module with a `MODEL_EXTENSIONS` mapping from generated model class
names to extension class names. Matching generated models inherit those
extensions before `TangleGeneratedModel`, e.g.:

```python
MODEL_EXTENSIONS = {
    "GetGraphExecutionStateResponse": "GetGraphExecutionStateResponseExtensions",
}
```

The extension classes must be importable from that module and should not import
generated model classes. Codegen writes exactly these support files:

```text
<out>/__init__.py
<out>/models.py
<out>/operations.py
```

The public client remains handwritten at `tangle_cli/client.py`; codegen does not
create a default generated public client wrapper.

To regenerate from the already checked-in snapshot instead of the backend, pass
`--from-snapshot` explicitly:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --from-snapshot \
  --model-extension-module tangle_cli.generated_model_extensions
```

If you already have a remote OpenAPI JSON document, fetch that directly instead:

```bash
uv run python -m tangle_cli.openapi.codegen --openapi-url https://example.com/openapi.json --out tangle_cli/generated
```

For example, the raw GitHub snapshot form is expressible as:

```bash
uv run python -m tangle_cli.openapi.codegen --openapi-url https://raw.githubusercontent.com/TangleML/tangle/master/openapi.json
```

Downstream tools can point `--out` at their own generated support package, e.g.:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --openapi-url https://oasis.shopify.io/openapi.json \
  --out src/tangle_deploy/generated_api \
  --operations-class-name GeneratedTangleApiExtensions \
  --model-extension-module tangle_deploy.tangle_api_model_extensions
```

At the time of writing the official repository does not commit that raw
`openapi.json`, so the submodule backend import flow above is the recommended
official regeneration path.
