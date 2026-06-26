# tangle-cli

CLI for Tangle, the open-source ML pipeline orchestration platform.

This repository contains the public Tangle CLI package. The CLI is built with [Cyclopts](https://cyclopts.readthedocs.io/) and is intentionally split into two command families:

- `tangle api ...` — pure OpenAPI wrappers around Tangle backend endpoints.
- `tangle sdk ...` — hand-written SDK, local, and compound commands that may call the API or may run entirely locally.

Start here:

```bash
uv run tangle quickstart
uv run tangle --help
uv run tangle api --help
uv run tangle sdk --help
```

## Command families

### `tangle api ...`: direct OpenAPI wrappers

`tangle api` commands are generated/dynamic wrappers for backend HTTP endpoints. They are useful when you want to call the API directly with minimal CLI behavior layered on top.

API command sources are:

- **Official static schema**: the checked-in OpenAPI snapshot packaged in `tangle_api.schema` and generated into `tangle_api.generated`.
- **Dynamic cache**: live schemas fetched with `tangle api refresh` and merged in by default as cached-only extension commands.

By default `tangle api` uses `--schema-source auto`, which means official static operations plus cached live-backend extensions when a cache exists. Official operations win if a cached schema has the same method/path.

### `tangle sdk ...`: hand-written SDK commands

`tangle sdk` commands are hand-written workflows. They can be:

- **local-only**: no generated/native API bindings required, e.g. pipeline validation/layout and component generation;
- **API-backed**: use the generated client but add domain behavior, e.g. pipeline-run submit payload construction, hydration, artifact lookup, publishing/version checks, or config batching.

Current SDK groups include:

```bash
uv run tangle sdk artifacts --help
uv run tangle sdk components --help
uv run tangle sdk pipelines --help
uv run tangle sdk pipeline-runs --help
uv run tangle sdk published-components --help
uv run tangle sdk secrets --help
```

## Common parameters and environment

API-backed commands commonly accept these options. Explicit CLI options win over config-file values, and config-file values win over environment defaults.

| Option / env | Purpose |
| --- | --- |
| `--base-url`, `TANGLE_API_URL` | API origin. Defaults to local development API URL when omitted. |
| `--token`, `TANGLE_API_TOKEN` | Bearer token shorthand. |
| `--auth-header`, `TANGLE_API_AUTH_HEADER`, `TANGLE_AUTH_HEADER` | Full `Authorization` value such as `Bearer ...` or `Basic ...`. |
| `-H`, `--header`, `TANGLE_API_HEADERS` | Extra headers. Repeatable as CLI flags; env accepts a JSON object or newline-separated `Name: value` entries. |
| `--config` | YAML/JSON defaults. Many commands accept a single object, a list of objects, or `_defaults` + `configs`. |
| `--log-type` | SDK progress logs: `console`, `none`, or `file`. Logs go to stderr or a temp log file so structured stdout stays parseable. |
| `TANGLE_VERBOSE=1` | Redacted HTTP request/response diagnostics only. This is separate from normal progress logging. |

Examples for protected APIs:

```bash
uv run tangle api refresh --base-url https://api.example \
  --auth-header 'Bearer ...' \
  -H 'X-Gateway-Auth: ...'

uv run tangle api pipeline-runs list --base-url https://api.example \
  --auth-header 'Basic ...' \
  -H 'X-Api-Key: ...'

uv run tangle sdk pipeline-runs submit pipeline.yaml \
  --base-url https://api.example \
  --auth-header 'Bearer ...' \
  -H 'X-Gateway-Auth: ...' \
  --log-type console
```

Use `--log-type none` for quiet machine-readable runs, and `--log-type file` to capture progress logs in a temporary file while keeping stdout clean.

## Installation and package split

The repository contains two Python import packages with different responsibilities:

- `tangle_cli` is hand-written. It contains CLI wiring, SDK/business helpers, local pipeline/component workflows, dynamic API discovery, codegen, shared runtime classes, logging, and extension classes.
- `tangle_api` is generated/native. It contains checked-in generated Pydantic models, generated endpoint operation methods, and the official OpenAPI snapshot.

The default `tangle-cli` package keeps the top-level import and local-only SDK commands native-free. Install the native extra when you want static API-backed commands and the handwritten `TangleApiClient` wrapper to use the checked-in generated bindings:

```bash
pip install 'tangle-cli[native]'
```

In this workspace, `uv` installs the workspace `tangle-api` package for development and tests:

```bash
uv run tangle api --help
uv run tangle sdk pipelines validate pipeline.yaml
```

If you are embedding `tangle_cli` in a downstream project, you can provide your own local `tangle_api.generated` package produced from your backend schema instead of using this repo's official generated package.

## Quick command examples

Local-only SDK commands:

```bash
uv run tangle sdk pipelines validate pipeline.yaml
uv run tangle sdk pipelines diagram pipeline.yaml
uv run tangle sdk pipelines layout pipeline.yaml --recursive
uv run tangle sdk pipelines hydrate pipeline.yaml --output hydrated.yaml
uv run tangle sdk components generate from-python path/to/component.py --image python:3.12
uv run tangle sdk components bump-version path/to/component.yaml
```

API-backed SDK commands:

```bash
uv run tangle sdk published-components search transformer --base-url https://api.example
uv run tangle sdk published-components inspect transformer --base-url https://api.example
uv run tangle sdk published-components publish components/my-component.yaml --dry-run
uv run tangle sdk pipeline-runs submit pipeline.yaml --dry-run --log-type none
uv run tangle sdk pipeline-runs submit pipeline.yaml --base-url https://api.example --log-type console
uv run tangle sdk pipeline-runs status RUN_ID --base-url https://api.example
uv run tangle sdk artifacts get --run-id RUN_ID --query '{"artifact_ids":["artifact-id"]}'
uv run tangle sdk secrets list --base-url https://api.example
```

Direct API commands:

```bash
uv run tangle api refresh --base-url https://api.example
uv run tangle api pipeline-runs list --base-url https://api.example
uv run tangle api pipeline-runs get RUN_ID --base-url https://api.example
uv run tangle api components get DIGEST --base-url https://api.example
uv run tangle api published-components list --base-url https://api.example
```

Path parameters are positional arguments and query parameters become options. Check generated help for the exact options exposed by the active schema source:

```bash
uv run tangle api pipeline-runs list --help
uv run tangle api pipeline-runs list --include-execution-stats
uv run tangle api pipeline-runs create --body @pipeline-run.json
```

Responses are printed as JSON when the backend returns JSON.

## Config files

Implemented API-backed commands and many SDK commands accept `--config path/to/config.yaml` (or JSON). Config files may contain a single object, a list of objects, or a `_defaults` + `configs` object; with multiple config entries, the command runs once per entry.

```yaml
_defaults:
  base_url: https://api.example
  auth_header: Bearer ...
  header:
    - "X-Gateway-Auth: ..."
  log_type: none

configs:
  - filter: active
    limit: 10
  - filter: finished
```

```bash
uv run tangle api pipeline-runs list --config api-config.yaml --limit 5
uv run tangle sdk published-components search --config components.yaml
uv run tangle sdk pipeline-runs submit --config submit.yaml
```

For generated `tangle api` commands, config keys use generated CLI parameter names such as `base_url`, `schema_source`, `body`, and endpoint parameters like `limit`, `filter`, or `id`.

## API schema cache and dynamic commands

Refresh the local schema cache for a live backend with:

```bash
uv run tangle api refresh --base-url http://localhost:8000
uv run tangle api refresh --base-url https://api.example --auth-header 'Bearer ...'
```

`refresh` fetches:

```text
<base-url>/openapi.json
```

Schemas are cached under the OS-specific user cache directory via `platformdirs`, with an `openapi` subdirectory. Override that directory with:

```bash
export TANGLE_CLI_CACHE_DIR=/path/to/openapi-schema-cache
```

Delete a cached live schema without touching the checked-in official snapshot:

```bash
uv run tangle api reset-cache --base-url https://api.example
```

Schema source modes are:

- `--schema-source auto` (default): official static operations plus cached-only backend extensions when a cache exists. Requires the native `tangle-api` package for official operations.
- `--schema-source official`: only the checked-in official static schema. Requires the native `tangle-api` package.
- `--schema-source cache`: only the schema previously written by `tangle api refresh` for the selected base URL. Does not require the native package.

For resource help, put `--schema-source` on the resource group:

```bash
uv run tangle api published-components --schema-source official --help
uv run tangle api published-components --schema-source cache --help
```

For endpoint calls, put it on the endpoint command:

```bash
uv run tangle api published-components experimental-search \
  --schema-source cache \
  --base-url https://api.example \
  --body @query.json
```

## SDK command details

### Local components

`generate from-python` converts a local Python function into a component YAML using inline source by default, or `--mode bundle` to embed local dependency modules. Common options include `--function`, `--output`, `--name`, `--image`, `--dependencies-from`, `--strip-code`, `--use-legacy-naming`, and `--resolve-root`.

`bump-version` increments or sets component version metadata in YAML and updates/regenerates a referenced Python source when the component contains `python_original_code_path` annotations.

Generation and version-bump commands accept `--config` YAML/JSON files via `tangle_cli.args_container`. Use keys such as `python_file`, `image`, `function`, `mode`, `resolve_root`, `yaml_file`, `set_version`, and `update_timestamp`; explicit CLI values take precedence.

### Published components

Published/registry component operations live under `sdk published-components` so local component authoring and registry calls do not share a command group.

```bash
uv run tangle sdk published-components publish components/my-component.yaml \
  --base-url https://api.example \
  --image python:3.12 \
  --name "My component"

uv run tangle sdk published-components publish components/my-component.yaml --dry-run
uv run tangle sdk published-components deprecate sha256:old --superseded-by sha256:new
```

`publish` accepts `--image`, `--name`, `--description`, `--annotations` (JSON), `--dry-run`, `--published-by`, generic git metadata fields, generic API auth fields, `--log-type`, and `--config`. By default it scopes version checks and automatic old-version deprecation to the current authenticated user via `users_me()`; use `--published-by` to supply an explicit owner/publisher filter. Publishing fails closed if no owner can be determined.

There is no separate OSS `publish-all` command. To publish multiple components, pass a YAML/JSON config list, or `_defaults` + `configs`, to the same `published-components publish` command; the command aggregates results and exits nonzero if any component errors.

```yaml
_defaults:
  base_url: https://api.example
  image: python:3.12
configs:
  - component_path: components/first.yaml
    name: First component
  - component_path: components/second.yaml
    name: Second component
```

Batch `publish-all`, notification integrations, dbt generation, from-container generation, and backend-specific advanced search workflows remain out of this OSS CLI package.

### Pipelines and pipeline runs

Local pipeline commands live under `sdk pipelines`:

```bash
uv run tangle sdk pipelines validate pipeline.yaml
uv run tangle sdk pipelines hydrate pipeline.yaml --output hydrated.yaml
uv run tangle sdk pipelines diagram pipeline.yaml
uv run tangle sdk pipelines layout pipeline.yaml --recursive
```

Pipeline run API/submit commands live under `sdk pipeline-runs`:

```bash
uv run tangle sdk pipeline-runs submit pipeline.yaml --dry-run
uv run tangle sdk pipeline-runs submit pipeline.yaml --arg key=value --annotation owner=team
uv run tangle sdk pipeline-runs wait RUN_ID --max-wait 600 --poll-interval 10
uv run tangle sdk pipeline-runs logs EXECUTION_ID
uv run tangle sdk pipeline-runs annotations set RUN_ID key value
uv run tangle sdk pipeline-runs export RUN_ID --output pipeline.yaml
```

`submit` hydrates refs by default and builds an API submit payload with `root_task.componentRef.spec`. Use `--no-hydrate` to submit the local YAML structure as-is. Use `--dry-run` to print the payload without creating a run.

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

`TangleApiClient` is handwritten in `tangle_cli.client` and inherits generated endpoint methods from `tangle_api.generated.operations.GeneratedTangleApiOperations`. The generated endpoint methods call the handwritten transport/request logic. Handwritten semantic helpers such as `find_existing_components(...)` return domain models and normalize common compatibility cases.

The top-level `import tangle_cli` is lightweight and does not import native static bindings. Install the native extra or otherwise provide a local `tangle_api.generated` package before importing `tangle_cli.client`.

## Codegen/autogen from OpenAPI

Use codegen when you want to update the checked-in official generated package or generate bindings for your own Tangle-compatible API instance.

Official backend/submodule flow:

```bash
git submodule update --init --recursive
uv sync --group codegen
uv run --group codegen python -m tangle_cli.openapi.codegen
uv run pytest
```

With no source flags, codegen loads OpenAPI from the default official backend submodule at `third_party/tangle`, writes `packages/tangle-api/src/tangle_api/schema/openapi.json`, and regenerates `packages/tangle-api/src/tangle_api/generated`. The backend import creates a database engine at import time; codegen points it at a temporary SQLite database unless `--backend-database-uri` is provided.

Regenerate from the checked-in API-package snapshot:

```bash
uv run python -m tangle_cli.openapi.codegen --from-snapshot
```

Fetch a remote OpenAPI JSON document directly:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --openapi-url https://api.example/openapi.json \
  --out src/tangle_api/generated
```

Generate from a backend checkout explicitly:

```bash
uv run --group codegen python -m tangle_cli.openapi.codegen \
  --backend-path /path/to/tangle/backend \
  --backend-database-uri sqlite:////tmp/tangle-openapi.sqlite
```

Important codegen options:

- `--out`: directory that receives `__init__.py`, `models.py`, and `operations.py`. Defaults to `packages/tangle-api/src/tangle_api/generated`.
- `--operations-class-name`: generated operations mixin class name. Defaults to `GeneratedTangleApiOperations`.
- `--model-extension-module`: importable module with `MODEL_EXTENSIONS`; repeat to compose modules.
- `--model-alias`: expose a stable public model name from one or more source schema names, e.g. `ComponentSpec=ComponentSpecOutput,ComponentSpecInput`.
- `--request-body-schema` / `--request-body-schema-file`: override a specific operation's JSON request-body schema without mutating the fetched OpenAPI document.

At runtime, more `tangle api ...` commands become available in two ways:

1. Static codegen: regenerate and install/provide a `tangle_api.generated` package for the schema.
2. Dynamic cache: run `tangle api refresh --base-url ...` and use `--schema-source auto` or `--schema-source cache` to expose cached-only operations through the dynamic CLI.

## Generated model extension pattern

Generated models use a generated implementation base plus a stable public subclass. For example, codegen emits this shape for a model with a handwritten extension:

```python
class _ComponentSpecGenerated(TangleGeneratedModel):
    name: Any = None
    # generated OpenAPI fields...

class ComponentSpec(ComponentSpecExtensions, _ComponentSpecGenerated):
    pass
```

The public class is a subclass rather than an alias because the public class name is the stable contract while the generated base can be regenerated. Subclassing lets the public class keep the OpenAPI/Pydantic fields from `_ComponentSpecGenerated` and add or override behavior through normal Python MRO.

Extension bases are placed to the **left** of the generated base:

```python
class ComponentSpec(ComponentSpecExtensions, _ComponentSpecGenerated):
    pass
```

That means extension methods/properties override generated-base behavior when names overlap, while generated fields and `TangleGeneratedModel` runtime helpers such as `to_dict()` remain available.

The built-in default extension module is:

```text
tangle_cli.generated_model_extensions
```

It defines:

```python
MODEL_EXTENSIONS = {
    "ComponentSpec": "ComponentSpecExtensions",
    "GetExecutionInfoResponse": "GetExecutionInfoResponseExtensions",
    "GetGraphExecutionStateResponse": "GetGraphExecutionStateResponseExtensions",
}
```

During codegen, `tangle_api.generated.models` imports those extension classes from `tangle_cli.generated_model_extensions`. This preserves the package boundary: `tangle_api` remains generated bindings, while `tangle_cli` owns handwritten runtime and extension behavior.

Downstream projects can layer their own extensions:

```python
# my_project/tangle_model_extensions.py
class MyComponentSpecExtensions:
    @property
    def owning_team(self) -> str | None:
        return (self.metadata or {}).get("annotations", {}).get("team")

MODEL_EXTENSIONS = {
    "ComponentSpec": "MyComponentSpecExtensions",
}
```

```bash
uv run python -m tangle_cli.openapi.codegen \
  --openapi-url https://api.example/openapi.json \
  --out src/tangle_api/generated \
  --model-extension-module my_project.tangle_model_extensions
```

The default module is applied first. Repeated `--model-extension-module` values are applied in order, and later/downstream modules become leftmost in the generated public class MRO, so they override earlier/default extensions. If two modules export the same extension class name, codegen imports them with deterministic aliases.

Pass an empty string to disable built-in default extensions:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --from-snapshot \
  --model-extension-module ""
```

The same empty-string sentinel can disable built-in `--model-alias` defaults. Built-in aliases keep stable public model names such as `ComponentSpec` even when a backend schema uses names like `ComponentSpecOutput` or `ComponentSpecInput`.

Extension classes should be importable from their modules and should not import generated model classes. They should be mixins over generated data, not replacements for generated schemas.

## Extending SDK behavior

The CLI exposes small explicit seams rather than requiring downstream forks.

### Hydrator resolvers

`packages/tangle-cli/src/tangle_cli/pipeline_hydrator.py` exposes a resolver registry:

```python
from tangle_cli.pipeline_hydrator import PipelineHydrator, register_component_resolver


def resolve_from_catalog(hydrator: PipelineHydrator, value, path: str, base_dir):
    # return (digest, component_spec_dict) or None
    return "sha256:...", {"name": "Resolved", "implementation": {"container": {"image": "python:3.12"}}}

register_component_resolver("catalog", resolve_from_catalog)
```

Resolvers receive the hydrator instance, the reference value, a display path, and the current base directory. They can use `hydrator._api_client()` for API-backed lookups, `hydrator.log` for progress logs, and `hydrator.resolution_overrides` for template/config variables. There is also an instance method `hydrator.register_component_resolver(...)` for per-hydrator overrides. Built-in kinds include `digest`, `name`, `url`, `file`, `resolve`, `http`, `https`, `local`, and `local_from_python`.

Downstream-only features such as Docker/from-container materialization or cloud storage can be added by registering new resolvers while the OSS default remains explicit about unsupported kinds.

### Pipeline run hooks

`packages/tangle-cli/src/tangle_cli/pipeline_runs.py` defines `PipelineRunHooks`, passed into `PipelineRunManager`. Subclass it to customize submit/load/wait/log behavior:

```python
from tangle_cli.pipeline_runs import PipelineRunHooks, PipelineRunManager


class MyRunHooks(PipelineRunHooks):
    def read_pipeline_yaml(self, pipeline_path):
        if str(pipeline_path).startswith("s3://"):
            return load_from_s3(pipeline_path)
        return super().read_pipeline_yaml(pipeline_path)

    def extra_submit_annotations(self, *, pipeline_spec, pipeline_path, run_as=None):
        annotations = super().extra_submit_annotations(
            pipeline_spec=pipeline_spec,
            pipeline_path=pipeline_path,
            run_as=run_as,
        )
        annotations["submitted_by"] = "my-tool"
        return annotations

    def fetch_logs(self, client, execution_id):
        return client.executions_container_log(execution_id)

manager = PipelineRunManager(client=my_client, hooks=MyRunHooks())
```

Available hooks include:

- `read_pipeline_yaml(...)`
- `hydrate_pipeline(...)`
- `prepare_run_arguments(...)`
- `extra_submit_annotations(...)`
- `before_submit(...)`
- `after_submit(...)`
- `after_wait(...)`
- `fetch_logs(...)`

Use these for generic downstream behavior such as alternate storage, extra annotations, scheduling/time input defaults, mutex checks, notifications, or alternate log providers. The OSS defaults intentionally exclude provider-specific cloud, notification, and scheduler behavior.

### Component publish hooks

`packages/tangle-cli/src/tangle_cli/component_publisher.py` defines `ComponentPublishHook` with:

- `before_batch(components_config)`
- `after_component(component_path, result)`
- `after_batch(results)`

`ComponentPublisher(..., hooks=[...])` calls these around publish batches. Use them for downstream summaries, audit records, or notifications while keeping OSS publishing generic.

### Shared CLI helpers and logging

`cli_options.py` centralizes shared Cyclopts annotations such as `BaseUrlOption`, `TokenOption`, `AuthHeaderOption`, `HeaderOption`, `ConfigOption`, and `LogTypeOption`. `cli_helpers.py` centralizes config loading, JSON printing, credential-isolation helpers, and the native-safe `LazyTangleApiClient` proxy. `logger.py` provides `ConsoleLogger`, `NullLogger`, `CaptureLogger`, `logger_for_log_type(...)`, and `run_with_logging(...)`.

Use these helpers for new SDK commands so top-level imports remain native-free, `--config` behavior stays consistent, credentials from config do not accidentally mix with ambient environment auth, and progress logs stay off structured stdout.

## Development checks

Common validation commands:

```bash
uv run --frozen pytest -q
uv build --sdist --wheel
uv build --sdist --wheel --package tangle-api
git diff --check
```

Targeted CLI smoke:

```bash
uv run tangle quickstart
uv run tangle api --help
uv run tangle sdk --help
```
