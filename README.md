# tangle-cli

[WIP] CLI for Tangle, the open-source ML pipeline orchestration platform.

The CLI is built with [Cyclopts](https://cyclopts.readthedocs.io/) and includes an OpenAPI-driven `tangle api` command tree generated from the Tangle FastAPI schema.

## Run locally

```bash
uv run tangle --help
uv run tangle components --help
uv run tangle api --help
```

## API commands

API commands are generated from the FastAPI/OpenAPI schema exposed by the Tangle backend. On cache miss, `tangle api --help` and generated command invocations try to fetch the schema once using the same `--base-url`, `--token`, `--auth-header`, and `--header` options. You can also refresh the local schema cache explicitly with:

```bash
uv run tangle api refresh
```

By default the CLI fetches the schema from:

```text
$TANGLE_API_URL/openapi.json
```

or, when `TANGLE_API_URL` is unset:

```text
http://localhost:8000/openapi.json
```

You can also pass a base URL explicitly:

```bash
uv run tangle api refresh --base-url http://localhost:8000
```

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

For arbitrary auth or routing headers, use `--header` (alias `-H`). `TANGLE_API_HEADERS` accepts a JSON object (or a newline-separated list of `Name: value` entries):

```bash
export TANGLE_API_HEADERS='{"Cloud-Auth":"...","X-Api-Key":"..."}'
uv run tangle api refresh --header 'Cloud-Auth: ...'
```

Repeated `--header 'Name: value'` flags can be used with both `tangle api refresh` and generated API commands. Header values are sent to the backend but are not printed by the CLI.

## Dynamic command examples

After refreshing (or once a cached schema exists), OpenAPI resource paths become command groups. For example, `/api/pipeline_runs/` becomes `pipeline-runs`, `/api/components/{digest}` becomes `components`, and `/api/published_components/` becomes `published-components`:

```bash
uv run tangle api pipeline-runs list
uv run tangle api pipeline-runs get RUN_ID
uv run tangle api pipeline-runs cancel RUN_ID
uv run tangle api components get DIGEST
uv run tangle api published-components list
uv run tangle api component-libraries get LIBRARY_ID
```

Path parameters are positional arguments and query parameters become options. Check generated help for the exact options exposed by your backend schema:

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
