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

SDK/scaffold commands live under `tangle sdk`. Local component generation/spec helpers are intentionally nested under `sdk components`; root-level `tangle components ...` is not registered in this lab CLI. API-backed published/registry component operations live separately under `sdk published-components` so local component authoring and registry calls do not share the same command group.

```bash
uv run tangle sdk components --help
uv run tangle sdk components annotations get
uv run tangle sdk components annotations set
uv run tangle sdk components generate from-python path/to/component.py --image python:3.12
uv run tangle sdk components generate from-python-function path/to/component.py  # compatibility alias
uv run tangle sdk components bump-version path/to/component.yaml
uv run tangle sdk published-components --help
uv run tangle sdk published-components search transformer
uv run tangle sdk published-components inspect transformer
uv run tangle sdk published-components inspect --digest sha256:...
uv run tangle sdk published-components library
uv run tangle sdk published-components publish path/to/component.yaml --base-url https://api.example
uv run tangle sdk published-components deprecate sha256:... --superseded-by sha256:...
```

`generate from-python` converts a local Python function into a component YAML
using inline source by default, or `--mode bundle` to embed local dependency
modules. The command accepts `--function`, `--output`, `--name`, `--image`,
`--dependencies-from`, `--strip-code`, `--use-legacy-naming`, and
`--resolve-root`. `bump-version` increments or sets component version metadata
in YAML, and updates/regenerates a referenced Python source when the component
contains `python_original_code_path` annotations.

Generation and version-bump commands accept `--config` YAML/JSON files via
`tangle_cli.args_container`. Use keys such as `python_file`, `image`,
`function`, `mode`, `resolve_root`, `yaml_file`, `set_version`, and
`update_timestamp`; explicit CLI values take precedence over config-file values.

Local components can also be published to, or deprecated in, a Tangle component
registry using the native generated/static API client under `sdk published-components`:

```bash
uv run tangle sdk published-components publish components/my-component.yaml \
  --base-url https://api.example \
  --image python:3.12 \
  --name "My component"

uv run tangle sdk published-components publish components/my-component.yaml --dry-run
uv run tangle sdk published-components deprecate sha256:old --superseded-by sha256:new
```

`publish` accepts `--image`, `--name`, `--description`, `--annotations` (JSON),
`--dry-run`, generic git metadata fields, generic API auth fields (`--base-url`,
`--token`, `--auth-header`, `-H/--header`), and `--config`. `deprecate` accepts
`--superseded-by`, the same generic API auth fields, and `--config`. These are
single-component OSS commands; batch `publish-all`, dbt generation,
from-container generation, and backend-specific search-v2 workflows remain out
of this lab CLI slice.

Example publish config:

```yaml
component_path: components/my-component.yaml
image: python:3.12
name: My component
annotations:
  owner: platform
base_url: https://api.example
```

Example deprecate config:

```yaml
digest: sha256:old
superseded_by: sha256:new
base_url: https://api.example
```

## API commands

API commands are pre-generated from the checked-in official Tangle FastAPI/OpenAPI snapshot, so native installs (`tangle-cli[native]`, or development installs with the workspace `tangle-api` package) show resource command groups immediately on a cold cache and command invocations do not require `refresh` first. By default, the CLI uses `--schema-source auto`: official static operations are always present when the native API package is installed, and cached live-backend operations discovered by `tangle api refresh` are included as extensions when they exist. Cached schemas do not override official operations with the same method/path; official definitions win.

Default `tangle-cli` installs without the native `tangle-api` package can still run cache-management commands such as `tangle api refresh` and can dispatch cached operations with `--schema-source cache`. Official static API commands require `tangle-cli[native]` so the packaged `tangle_api.schema` snapshot is available.

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

- `--schema-source auto` (default): official static operations plus cached-only backend extensions when a cache exists. Requires `tangle-cli[native]` for official operations.
- `--schema-source official`: only the checked-in official static schema (OSS-only/core commands). Requires `tangle-cli[native]`.
- `--schema-source cache`: only the schema previously written by `tangle api refresh` for the selected base URL. Does not require the native API package.

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

### Config files

Implemented API commands and `tangle sdk published-components` commands accept
`--config path/to/config.yaml` (or JSON) for command defaults. Explicit CLI
arguments take precedence over config-file values. Config files may contain a
single object, a list of objects, or a `_defaults` + `configs` object; with
multiple config entries, the command runs once per entry.

```yaml
_defaults:
  base_url: https://api.example
  header:
    - "Cloud-Auth: ..."

configs:
  - schema_source: cache
    filter: active
    limit: 10
  - schema_source: cache
    filter: finished
```

```bash
uv run tangle api pipeline-runs list --config api-config.yaml --limit 5
uv run tangle sdk published-components search --config components.yaml
```

For generated `tangle api` commands, config keys use the generated CLI
parameter names such as `base_url`, `schema_source`, `body`, and endpoint
parameters like `limit`, `filter`, or `id`.

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
run = client.pipeline_runs_get("run-id")
existing = client.find_existing_components(
    ["component-name"],
    published_by_substring="alice@example.com",
)
```

`TangleApiClient` uses checked-in endpoint methods generated offline from the
native `tangle_api.schema` OpenAPI snapshot into the native
`tangle_api.generated` package, so normal imports do not fetch or parse the
OpenAPI schema. Handwritten semantic
helpers such as
`find_existing_components(...)` return domain models; that helper accepts
component specs, mapping references, or plain names plus optional names/digests
and publisher filters, and returns a de-duplicated `list[ComponentInfo]`.
`ComponentSpec` is a generated OpenAPI model extended with legacy convenience
helpers, and remains re-exported from `tangle_cli.models`. Execution detail
helpers use the generated `GetExecutionInfoResponse` model directly. The top-level
`import tangle_cli` is lightweight and does not import native static bindings;
install the `native` extra or otherwise provide a local `tangle_api.generated`
package before importing `tangle_cli.client`.

The repository is split into two import packages: `tangle_cli` contains the CLI,
business helpers, dynamic discovery, codegen, runtime base classes, and default
model extensions; `tangle_api` contains only the native checked-in generated
models, operation proxies, and official OpenAPI snapshot for the official OSS
API. Downstream consumers that vendor `tangle_cli` can generate their own local
`tangle_api.generated` package from their schema without vendoring cli-lab's
native generated package or official snapshot.

To refresh the checked-in generated methods/models from the official Tangle
backend submodule, run:

```bash
git submodule update --init --recursive
uv sync --group codegen
uv run --group codegen python -m tangle_cli.openapi.codegen
uv run pytest
```

With no source flags, codegen loads OpenAPI from the default official backend
submodule at `third_party/tangle`, writes
`packages/tangle-api/src/tangle_api/schema/openapi.json`, and regenerates
`packages/tangle-api/src/tangle_api/generated`. The backend import creates a database engine
at import time; codegen points it at a temporary SQLite database unless
`--backend-database-uri` is provided. If the submodule is missing, initialize it
with `git submodule update --init --recursive`.

`--out` controls where generated support modules are written. It defaults to
`packages/tangle-api/src/tangle_api/generated`, which is the native generated
package used by the public `tangle_cli/client.py` wrapper. `--operations-class-name` controls the generated
operations class name in `<out>/operations.py`; it defaults to
`GeneratedTangleApiOperations`. `--model-extension-module` points codegen at an
importable module with a `MODEL_EXTENSIONS` mapping from generated model class
names to extension class names. The built-in `tangle_cli.generated_model_extensions`
module is applied first by default, and repeated `--model-extension-module`
values are applied after it in order. Pass an empty string
(`--model-extension-module ""`) to disable the default module. Generated object
models are emitted as private schema-derived bases plus public model classes. Codegen also applies a built-in model alias so FastAPI schemas such as `ComponentSpecOutput` or `ComponentSpecInput` are additionally exposed as the stable public `ComponentSpec` class when `ComponentSpec` is absent from the schema. Add or override aliases with `--model-alias PublicModel=SourceSchema[,OtherSourceSchema]`; pass `--model-alias ""` to disable built-in aliases. For example:

```python
MODEL_EXTENSIONS = {
    "GetGraphExecutionStateResponse": "GetGraphExecutionStateResponseExtensions",
}
```

```python
class _ComponentSpecGenerated(TangleGeneratedModel):
    name: Any = None

class ComponentSpec(ComponentSpecExtensions, _ComponentSpecGenerated):
    pass
```

For models without extensions, codegen still emits a public subclass such as
`class OtherResponse(_OtherResponseGenerated): pass` so the exported class keeps
its public OpenAPI name. When multiple extension modules target the same model,
later/downstream extensions are leftmost in the public class MRO and override
earlier/default extensions while schema-derived data remains available via
`to_dict()`. Duplicate extension class names from different modules are imported
with deterministic aliases. Extension classes must be importable from their
modules and should not import generated model classes.

Downstream generators can explicitly override a specific operation's JSON request-body schema without mutating the fetched OpenAPI document. This is useful when a backend schema is too specific or recursive for generated keyword arguments and the operation should accept an open-ended raw body. Use the OpenAPI `operationId`, generated method name, or `group.command` name:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --request-body-schema 'search_create={"type":"object","additionalProperties":true,"title":"SearchQuery"}'
```

For larger schemas, use a JSON file:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --request-body-schema-file search_create=search_query.json
```

These request-body overrides are generic and opt-in; OSS codegen has no built-in behavior for experimental downstream endpoints. Codegen writes exactly these support files:

```text
<out>/__init__.py
<out>/models.py
<out>/operations.py
```

The generated models import shared runtime helpers from `tangle_cli.generated_runtime`.
The public client remains handwritten at `tangle_cli/client.py`; codegen does not
create a default generated public client wrapper.

To regenerate from the already checked-in API-package snapshot instead of the
backend, pass `--from-snapshot` explicitly:

```bash
uv run python -m tangle_cli.openapi.codegen --from-snapshot
```

If you already have a remote OpenAPI JSON document, fetch that directly instead:

```bash
uv run python -m tangle_cli.openapi.codegen --openapi-url https://example.com/openapi.json --out src/tangle_api/generated
```

For example, the raw GitHub snapshot form is expressible as:

```bash
uv run python -m tangle_cli.openapi.codegen --openapi-url https://raw.githubusercontent.com/TangleML/tangle/master/openapi.json
```

Downstream tools can point `--out` at their own generated support package, e.g.:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --openapi-url https://oasis.shopify.io/openapi.json \
  --out src/tangle_api/generated \
  --operations-class-name GeneratedTangleApiExtensions \
  --model-extension-module tangle_deploy.tangle_api_model_extensions \
  --request-body-schema 'search_create={"type":"object","additionalProperties":true,"title":"SearchQuery"}'
```

At the time of writing the official repository does not commit that raw
`openapi.json`, so the submodule backend import flow above is the recommended
official regeneration path.
