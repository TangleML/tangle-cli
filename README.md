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

- **local-only**: no generated API bindings required, e.g. pipeline validation/layout and component generation;
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
| `--config` | YAML/JSON defaults. Many commands accept a single object, a list of objects, or `_defaults` + `configs`, optionally wrapped in a top-level `_select` environment selector. Values may be read from the environment with `{_env: NAME}`. |
| `--log-type` | SDK progress logs: `console`, `none`, or `file`. Logs go to stderr or a temp log file so structured stdout stays parseable. |
| `TANGLE_VERBOSE=1` | Redacted HTTP request/response diagnostics only. This is separate from normal progress logging. |
| `--ca-bundle` | Global CLI flag: path to a PEM CA bundle used as the TLS trust store for every transport. Overrides `TANGLE_API_CA_BUNDLE`. Place before the subcommand. |
| `--verify-tls` / `--no-verify-tls` | Global CLI flag: enable or disable TLS verification for every transport. Overrides `TANGLE_API_VERIFY_TLS`. `--no-verify-tls` is local-development only. Place before the subcommand. |
| `TANGLE_API_CA_BUNDLE` | Path to a PEM CA bundle used to verify TLS for every transport. Use this to trust a private or corporate CA without disabling verification. |
| `TANGLE_API_VERIFY_TLS` | TLS verification toggle. Values `0`, `false`, or `no` (case/space-insensitive) disable verification; any other nonempty value keeps it on. |

### TLS verification

TLS certificate verification is enabled by default for all HTTP transports (schema
fetches, `tangle api` calls, and the programmatic clients). The effective setting is
resolved with the following precedence, highest to lowest:

1. An explicit `verify=` argument to the Python clients (a `bool` or a path to a CA bundle).
2. The global CLI flags `--ca-bundle` / `--verify-tls` / `--no-verify-tls`.
3. `TANGLE_API_CA_BUNDLE` — verify against the given CA bundle.
4. `TANGLE_API_VERIFY_TLS` — enable or disable verification.
5. The secure default: verification enabled against the system trust store.

The global CLI flags are true root options that apply to every command — the static
`tangle sdk ...` clients, the dynamic `tangle api ...` commands, and `tangle api refresh`.
Place them **before** the subcommand, for example `tangle --ca-bundle ca.pem api ...` or
`tangle --no-verify-tls sdk ...`. They are honored even by the dynamic OpenAPI schema
discovery that runs before command dispatch. A defaulted (absent) flag does not override
the environment: when a flag is not supplied, the `TANGLE_API_*` variables and the standard
`REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` handling still apply. `--ca-bundle` combined with an
explicit `--no-verify-tls` is contradictory and fails fast before any request; `--ca-bundle`
with `--verify-tls` is redundant but accepted.

If both env vars are set, `TANGLE_API_CA_BUNDLE` wins and TLS stays verified against the
bundle. Empty values are treated as unset. A `--ca-bundle` or `TANGLE_API_CA_BUNDLE` that
does not point to an existing file fails fast with an actionable error before any request is
made. When no Tangle-specific setting is provided, the standard `REQUESTS_CA_BUNDLE` /
`CURL_CA_BUNDLE` handling and any caller-supplied `requests.Session.verify` are left
untouched.

For a private CA, prefer `--ca-bundle` / `TANGLE_API_CA_BUNDLE` over disabling verification:
it keeps certificates verified against a trusted root. `--no-verify-tls` /
`TANGLE_API_VERIFY_TLS=0` disables verification entirely and is intended for local
development only — never use it against production endpoints.

```bash
# Trust a private CA with the global flag (recommended for internal/self-hosted APIs)
uv run tangle --ca-bundle /etc/ssl/private-ca.pem \
  api refresh --base-url https://internal.example

# Or via environment variable
TANGLE_API_CA_BUNDLE=/etc/ssl/private-ca.pem \
  uv run tangle api refresh --base-url https://internal.example

# Disable verification (local development only)
uv run tangle --no-verify-tls api refresh --base-url https://localhost:8443
```

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
- `tangle_api` is generated/static. It contains checked-in generated Pydantic models, generated endpoint operation methods, and the official OpenAPI snapshot.

The default public `tangle-cli` package depends on the matching `tangle-api` package, so normal installs include the checked-in generated bindings used by static API-backed commands and the handwritten `TangleApiClient` wrapper:

```bash
pip install tangle-cli
```

The `native` extra remains as a compatibility no-op alias for older install instructions. In this workspace, `uv` installs the workspace `tangle-api` package for development and tests:

```bash
uv run tangle api --help
uv run tangle sdk pipelines validate pipeline.yaml
```

Custom API/codegen users can still run codegen from the fully capable install; generating bindings does not require removing the official `tangle-api` package. For project-local generated APIs, generate into a local source tree such as `src/tangle_api/generated` (and `src/tangle_api/schema/openapi.json` when you want `tangle api --schema-source official`) and run from that project so local `src/tangle_api` shadows site-packages. For packaged custom APIs, publish/provide a distribution named `tangle-api` with a version compatible with this `tangle-cli` release (for example `0.1.0+yourorg` for a `tangle-cli` dependency on `tangle-api==0.1.0`) via a private index, `--find-links`, or uv sources. As an expert escape hatch, `--no-deps` installs only `tangle-cli` and skips all dependencies, so that environment must manually provide every required runtime dependency plus its generated/custom `tangle_api`; this is acceptable for controlled codegen/custom scenarios but not normal UX.

## Agent skills

This repo includes the Tangent agent-skill bundle under `skills/tangent/`. The bundle was ported from `tangle-cli-lab` and now treats this repository as the canonical source. It drives the public `tangle` / `tangle-cli` command surface, assumes the default `tangle-cli` install includes `tangle-api`, and keeps relative references (`agents/*.md`, `references/*.md`) self-contained for Pi-style skill loaders. The source distribution includes `skills/**` so downstream source-based consumers can inspect or vendor the skill docs with the release.

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

### Environment-selected configs (`_select`)

Any command that accepts `--config`, and any Python pipeline `config.yaml` (see [Environment-selected pipeline config](#environment-selected-pipeline-config-_select-_env)), can pick one of several config documents from an environment variable by making `_select` the top-level node:

```yaml
_shared: &shared
  log_type: none

_select:
  env: TANGLE_ENV
  cases:
    dev:
      <<: *shared
      base_url: https://api.dev
    prod:
      _defaults:
        <<: *shared
        base_url: https://api.prod
      configs:
        - filter: active
        - filter: finished
```

The selected branch is a complete config document — a single object, a list of objects, or `_defaults` + `configs` — and is then loaded exactly as if it had been written on its own. A branch may itself be another `_select` node, which composes multiple dimensions (for example environment and region).

Selection fails closed by default. To opt into a fallback, author an explicit `default` branch:

```yaml
_select:
  env: TANGLE_ENV
  cases:
    prod:
      base_url: https://api.prod
  default:
    base_url: https://api.dev
```

With `default`, an unset variable or a value matching no case resolves to that branch; an exact case match always wins over it. Without `default`, both remain errors. `default` is a sibling of `cases`, not an entry inside it: a case *named* `default` stays an ordinary exact-match case. Like any branch, `default` must be a complete valid config shape and may itself be another `_select`; an inner selector does not inherit the outer `default`.

Rules:

- `_select` is an exact reserved key name rather than a prefix (the only other reserved key is the [`_env`](#environment-variable-values-_env) value directive). Configs without `_select` are unchanged byte-for-byte and semantically.
- At a `_select` node, only `_select` and other underscore-prefixed helper keys (YAML anchor holders such as `_shared`) may appear; ordinary sibling keys are rejected.
- `_select` accepts only `env`, `cases`, and the optional `default`. There are no aliases: `else`, `fallback`, and `defaults` are rejected.
- The selector shape, the `env` name, every `cases` key, and every case and `default` branch are validated before the environment is read, so a malformed selector fails identically in every environment. Branches are checked as complete config documents with the same rules the loader applies to a whole file, and nested selectors are validated recursively.
- Only environment *lookups* are lazy. A dormant branch is fully shape-checked, but its `env` variable is never required unless that branch is actually selected. Command-specific field names and types are still validated later, against the selected branch only.
- Selector nesting is capped at 32 levels, which also stops a self-referential YAML alias. A node shared by several anchors is validated once, so anchor-heavy files stay fast.
- Matching uses `os.environ[NAME]` exactly: case sensitive, with no trimming, case folding, or interpolation.
- Fallback exists only where it is authored. Without `default`, an unset variable or an unmatched value is an error; there is never an implicit default or implicit production branch. The raw environment value is never echoed — diagnostics list only the configured case names.

### Environment-variable values (`_env`)

Any config value — in `--config` files and in [Python pipeline configs](#environment-selected-pipeline-config-_select-_env) — can be read from an environment variable with the `_env` value directive, so a checked-in config can reference a secret or per-machine value without containing it:

```yaml
base_url: https://api.prod
token: {_env: TANGLE_PROD_TOKEN}
header:
  - {_env: GATEWAY_HEADER}
  - "X-Team: search"
limit: {_env: RUN_LIMIT, default: 10}
```

- The directive is exactly `{_env: NAME}` or `{_env: NAME, default: <scalar>}`. Any other key beside `_env` — including underscore-prefixed keys and aliases such as `fallback` — is rejected, and `NAME` follows the same rule as `_select.env` (`[A-Za-z_][A-Za-z0-9_]*`).
- It may appear anywhere a value may appear: under a config key, in `_defaults`, in `configs` entries, and inside nested maps and lists. A document or config-entry mapping is never itself a directive, so an `_env:` helper/anchor key at the top level of a config object is unaffected.
- A missing variable without `default` fails closed with the variable name, the key path (for example `configs[1].token`), and the config file. An empty string counts as set and is used as-is.
- The resolved value is always a string, and a `default` is stringified the same way: numbers and booleans use their JSON spelling (`10`, `1.5`, `true`), dates use ISO format, and `null`/maps/lists are rejected (quote `''` for an empty default). A field therefore has one type whether or not the variable is set, and the command's usual conversion (JSON fields, repeatable options, enums, typed converters) applies downstream in both cases.
- Diagnostics name the variable, never its value, and no directive value is logged. There is no `${VAR}` string interpolation.

With `_select`, selection happens first, and `_env` applies to the selected document:

```yaml
_select:
  env: TANGLE_ENV
  cases:
    prod:
      token: {_env: TANGLE_PROD_TOKEN}
    dev:
      token: {_env: TANGLE_DEV_TOKEN, default: dev-token}
```

Every `_env` directive — in every case and `default` branch, and in helper sections — is shape-checked before any variable is read, so a malformed directive fails identically in every environment. Only the directives in the selected document's config entries and `_defaults` are looked up; a dormant branch never requires its variables. `_select` itself is unchanged.

Precedence per field is **CLI > config > environment > default**: an explicit CLI value wins, then the config key (including a value read through `_env`), then an environment variable the command has opted that field into (see [`EnvField`](#shared-cli-helpers-and-logging)), then the default. As before, a CLI value equal to the option default is indistinguishable from an omitted one.

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

- `--schema-source auto` (default): official static operations plus cached-only backend extensions when a cache exists. Normal `tangle-cli` installs include the `tangle-api` package needed for official operations; custom API projects can shadow or replace that package as described in the codegen section.
- `--schema-source official`: only the checked-in official static schema from `tangle-api` (or a compatible custom `tangle-api` package on your environment's import path).
- `--schema-source cache`: only the schema previously written by `tangle api refresh` for the selected base URL. This is the custom/source-checkout fallback when a consumer environment does not provide an importable `tangle_api.schema` package.

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

`publish` accepts `--image`, `--name`, `--description`, `--annotations` (JSON), `--dry-run`, `--allow-downgrade`, `--published-by`, generic git metadata fields, generic API auth fields, `--log-type`, and `--config`. By default it scopes version checks and automatic old-version deprecation to the current authenticated user via `users_me()`; use `--published-by` to supply an explicit owner/publisher filter. Publishing fails closed if no owner can be determined.

#### Monotonic publishing and result digests

Publishing is monotonic against the highest **non-deprecated, owner-scoped** published version of the component (ordering comes from `compare_versions`, which zero-pads shorter versions so `1.0.1 > 1.0`):

| Local vs latest published | Outcome | Notes |
| --- | --- | --- |
| nothing published (no non-deprecated owner-scoped version) | `proceed` | first publish |
| local strictly newer | `proceed` | publishes, then deprecates owner-scoped versions proven older |
| local equal | `skip` | no create/deprecate calls |
| local strictly older | `skip` | no-op; never publishes an older version and never deprecates a newer one |
| published version unreadable, or ambiguous tie at the latest version | `error` | fails closed; no create/deprecate calls |

Every result carries the digest of the version it compared against:

- `digest` — digest of a **newly created** publication (SUCCESS only, unchanged meaning).
- `latest_digest` — exact digest of the selected latest published version (set on PROCEED/SKIP, and carried through the SUCCESS/ERROR results that follow a version check). JSON output includes it as `latest_digest`.
- `ProcessingResult.resolved_digest` — the digest a caller should pin: `digest or latest_digest`, but deliberately `None` for any outcome other than SUCCESS/SKIP, so a failed publish never hands back a stale-but-plausible digest.

The check **fails closed** (an `error`, with nothing published and nothing deprecated) whenever the published state cannot be read completely:

- any non-deprecated owner-scoped candidate whose digest is missing, or whose spec/version cannot be fetched or parsed — an unreadable row could be newer than the local version, and must never be deprecated sight-unseen;
- two or more non-deprecated candidates tied at the selected latest version, where no exact digest can be chosen. The reason names the tied digests instead of guessing from API ordering.

Deprecated components are never selected as "latest", and all digest lists in results/logs are sorted, so diagnostics do not depend on API response order.

The published state is re-read immediately before create, and the same policy is re-applied to that fresh observation: a version that appeared concurrently since the first check can still turn the publish into a skip or an error. After a successful create, only digests **proven strictly older** in that final observation are deprecated — a row first seen after the publish decision is never deprecated on the strength of the earlier one. A race after the final read is not preventable client-side and needs a server-side conditional/CAS operation.

**Contract change:** republishing an older version used to proceed (publishing the older spec and deprecating the newer one). It is now a skip. `--allow-downgrade` publishes the older spec but still never deprecates a strictly newer row; deprecate those explicitly with `published-components deprecate` if that is really intended. Deliberate downgrades must opt in with `--allow-downgrade` on the CLI, or `ComponentPublisher(allow_downgrade=True)` / `allow_downgrade=True` on the `publish_component_to_tangle` / `perform_version_check` wrappers. Republishing the same version is still a skip, as before.

There is no separate OSS `publish-all` command. To publish multiple components, pass a YAML/JSON config list, or `_defaults` + `configs`, to the same `published-components publish` command; the command aggregates results and exits nonzero if any component errors. A top-level `_select` node can choose between such documents per environment (see [Environment-selected configs](#environment-selected-configs-_select)).

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
uv run tangle sdk pipeline-runs submit-from-python pipeline.py --arg key=value
uv run tangle sdk pipeline-runs wait RUN_ID --max-wait 600 --poll-interval 10
uv run tangle sdk pipeline-runs logs EXECUTION_ID
uv run tangle sdk pipeline-runs annotations set RUN_ID key value
uv run tangle sdk pipeline-runs export RUN_ID --output pipeline.yaml
```

#### Python pipeline authoring

Python-authored pipelines live in normal `.py` files and compile with:

```bash
uv run tangle sdk pipelines compile pipeline.py -o pipeline.yaml
uv run tangle sdk pipelines compile pipeline.py -o pipeline.yaml --pipeline pipeline_fn_name
```

To compile and submit in one step, without keeping the compiled YAML around, use
`pipeline-runs submit-from-python`:

```bash
uv run tangle sdk pipeline-runs submit-from-python pipeline.py \
  --override batch_size=100 \
  --image eval-slim=registry.example/eval-slim@sha256:... \
  --arg shop=acme --annotation owner=team
```

It compiles the script, hydrates the bundle, submits the run, and removes the
compiled artifacts again — including on dry runs and failures. Its run-tier
flags are the same as `pipeline-runs submit`; the extra compile-tier flags are
`--pipeline`, repeatable `--override KEY=VALUE`, and repeatable `--image ID=REF`.
Use `pipelines compile` instead when the compiled YAML itself is what you want.

The two value tiers are distinct and easy to confuse:

| Flag | Tier | Meaning |
| --- | --- | --- |
| `--override KEY=VALUE` | compile | `cfg` value used while the graph is built |
| `--image ID=REF` | compile | resolves `@task(image_id=ID)` to a registry ref |
| `--arg` / `--args-json` / `--arg-secret` | run | pipeline arguments for the created run |

Notes:

- Hydration is always on: the compiled bundle references local sidecars
  (`resolve://./<stem>.components.yaml#…`, `file://<stem>.subgraphs/…`) that the
  server cannot read. There is no `--no-hydrate` here.
- The bundle is compiled next to the script (never in `/tmp`) under a unique
  hidden `.tangle-submit-*.yaml` name, because relative refs such as
  `ref(url="file://./component.yaml")` resolve against the *output* directory.
  The name is allocated with an exclusive create, so concurrent compiles of the
  same script never collide or overwrite a sibling YAML. A symlinked script
  compiles next to its resolved target, where its `config.yaml` and relative
  refs actually live.
- The bundle stays readable until every run has been submitted, so
  run-lifecycle hooks still see an existing `pipeline_path`; cleanup then
  removes exactly that stem's files (`.yaml`, `.components.yaml`,
  `.subgraphs/`) and nothing else.
- Submission never waits; use `pipeline-runs wait RUN_ID`.
- With a multi-entry `--config` file, entries may carry different `--override` /
  `--image` values, and **every entry is fully prepared before any run is
  created**: each is compiled exactly once, hydrated, merged with its run
  arguments and secrets, validated, and frozen into a submit body; only then are
  the frozen bodies submitted in order (no recompilation). An unsupported config
  key (`hydrate:`, `pipeline_path:`), a malformed `override` / `image` /
  `arg-secret` value, an input given as both `--arg` and `--arg-secret`, a
  missing script, or a compile/hydrate error in *any* entry therefore creates no
  runs and leaves no artifacts behind. A runtime failure while submitting entry
  N can still follow the successful submits of entries 1..N-1 — that is inherent
  to creating N runs.

A minimal graph uses `@pipeline` for the graph and `@task` for local Python components. `@task` functions are not executed at compile time; the compiler records call sites, emits a sibling `<output>.components.yaml` with `local_from_python` entries, and rewrites task component refs to that sidecar. Hydrate later regenerates the same component YAML from the Python source.

Sidecar entries are deduplicated by the **generated component**, not by function name. The dedup key is the module-qualified function identity — a logical module namespace derived from the project-relative source layout (`package_a/tasks.py` -> `package_a.tasks`, `pkg/__init__.py` -> `pkg`), plus `__qualname__` and the function name — together with every generation-affecting option: image (explicit or resolved `image_id`), `mode`, `resolve_root`, `dependencies_from`, the `unwrap` schema, and the source file. Runtime `__module__` is deliberately not used, because pipeline scripts are imported under throwaway UUID module names.

Call sites sharing that whole identity collapse to one entry keyed by the readable hyphenated function name (`run_dbt` -> `run-dbt`). Otherwise every colliding variant — a shared helper re-decorated with different task-level options, or `package_a.tasks.run` alongside `package_b.tasks.run` — is emitted as `run-dbt--<hash>`, where `<hash>` is a content digest of the canonical identity; no variant keeps the unsuffixed name, and each task ref points at its own. Digests are anchored at the project source directory, so fragment names are unchanged by relocating the project, compiling into a different output directory, or reordering the pipeline's calls, and they never embed image, path, or credential-bearing values.

```python
from cloud_pipelines import components
from tangle_cli.python_pipeline import In, Out, pipeline, task


@task(image="python:3.12")
def write_greeting(out: components.OutputPath("Text"), who: str, greeting: str = "hello"):
    with open(out, "w") as fh:
        fh.write(f"{greeting} {who}")


@pipeline("Greeting pipeline", output_name="greeting_file")
def greeting_pipeline(who: In[str], cfg) -> Out[str]:
    greeting = write_greeting(who=who, greeting=cfg.greeting)
    return greeting.out
```

`In[T]` parameters become runtime graph inputs. A single `-> Out[T]` return exposes one graph output; use `@pipeline(output_name=...)` to name that output. For multiple outputs, define a frozen dataclass subclass of `Outputs` with `Out[T]` fields and return an instance. A pipeline that accepts a `cfg` parameter reads `config.yaml` (or the path passed via `@pipeline(config="...")`) at compile time, with `--override key=value` values overlaid by the compile command.

##### Environment-selected pipeline config (`_select`, `_env`)

A pipeline `config.yaml` — root or child, including one broadcast with `propagate_config=True` — is resolved by the same loader as `--config` files (see [`_select`](#environment-selected-configs-_select) and [`_env`](#environment-variable-values-_env)), with one shape rule: the selected document, and every case and `default` branch, must be a mapping. `_defaults`/`configs` have no special meaning in a pipeline config; they are ordinary keys.

```yaml
_select:
  env: TANGLE_ENV
  cases:
    prod: {dataset: prod_ds, batch_size: 500, token: {_env: PROD_TOKEN}}
    dev: {dataset: dev_ds, batch_size: 10}
  default: {dataset: local_ds, batch_size: 1}
```

- Resolution happens first; overrides are layered on the result. Precedence is `--override` / `.override_config` / `propagate_config` broadcast > selected branch. An `.override_config` key must exist in the child's *selected* branch, and a broadcast carries resolved values, never `_select`/`_env` nodes. Each child resolves its own config.
- `_select` branch values keep their native YAML types (`batch_size` is an `int`). `_env` values are strings (a `default` is stringified) and are **not** YAML-coerced the way raw `--override` strings are: an environment value is opaque (`007` stays `"007"`, `no` stays `"no"`). Convert explicitly in pipeline code (`int(cfg.limit)`), or put typed values in `_select` branches.
- Every branch is structure-checked in every environment, including the `template_file:` rejection, and a dormant branch never requires its variables. An unset or unmatched selector without `default` fails closed without echoing the value.
- Compile identity follows the resolved values. For a config that uses `_select` or `_env`, the child sidecar name (`<child>-<hash8>.yaml`) includes a digest of the resolved config, so different selections that produce different values never share a sidecar and identical values keep the same name. Configs without `_select`/`_env` keep their existing names.

Task IDs default from the left-hand variable name at the call site, converted to title case. If there is no simple left-hand variable, or if you want a stable explicit label, call `.named("Task Id")` before invoking the task. Use `.bind(...)` to pre-fill task arguments and `.with_annotations({...})` to add per-task annotations.

##### Root pipeline annotations

`@pipeline(annotations={...})` writes the compiled pipeline's root `metadata.annotations` block. A caller that compiles programmatically can supply the block instead — typically from its own per-environment config file, so the values do not have to be hard-coded in source:

```python
from tangle_cli.pipelines import compile_pipeline_file

compile_pipeline_file(
    "pipeline.py",
    "pipeline.yaml",
    pipeline_annotations={"environment": "staging", "owner": "search-platform"},
)
```

The same keyword exists on `tangle_cli.pipeline_compiler.compile_pipeline` and on `PipelineCompiler.compile_file`. There is no CLI flag: the source route already exists, and what the keyword adds is a programmatic/config route for the part of the block that varies by environment.

Semantics:

- **Per-key merge, caller wins.** `@pipeline(annotations={"author": "a", "version": "1.0"})` compiled with `pipeline_annotations={"version": "2.0", "environment": "staging"}` emits all three keys, with `version: "2.0"`. Source keys the caller does not mention are preserved, so invariants stay in source and only the varying subset is passed in.
- **Omitted or `{}` is a no-op**, byte for byte — an empty mapping is not a destructive clear of the source block.
- **Root only.** `subpipeline` children never inherit it, so child subgraph sidecar names, bytes, and component digests are unaffected. A child that wants annotations declares its own.
- **Descriptive only.** Root metadata is not read by the orchestrator, so it cannot influence placement, routing, scheduling, or run identity. Use pipeline-run annotations for anything execution-bearing.
- **`str -> str`, validated up front.** A non-mapping argument, a non-string key or value, an empty key, a `system/`-prefixed key (reserved by Tangle), or a template delimiter (`{{`, `{%`, `{#`) in a key or value raises `InvalidPipelineAnnotationsError` (a `CompileError`) before anything is imported or written. Annotations usually come from an untrusted config file, so the diagnostics name the key and the type and never echo a value. Values are baked into the compiled YAML and the stored pipeline definition: labels only, never secrets.

The rules live in one place, `tangle_cli.schema_validation`: `check_annotations(mapping, policy=..., error_cls=...)` applied under a named `AnnotationPolicy`. `CALLER_ANNOTATION_POLICY` is the strict input policy described above; `DOCUMENT_ANNOTATION_POLICY` is the lenient policy every pipeline document is validated against (scalar-or-null values, no key rules), matching the schema and hand-authored YAML. Only the caller-supplied input surface is strict: existing documents are accepted exactly as before, and the document check still runs on the merged result, so annotations reaching the output by any route are validated.

A distribution that reads these annotations from its own config file should call `check_annotations(mapping, policy=CALLER_ANNOTATION_POLICY, error_cls=...)` at config-parse time — passing its own error type and adding the config path and key to the message — so one user mistake produces one diagnostic instead of two competing ones. The compiler's own call is then the backstop for anything arriving by another route.

##### Conditional task execution

Pipeline inputs used as conditions are ordinary `In[str]` values; there is no special conditional input annotation. Pass the value through the reserved task-call metadata keyword `is_enabled=`:

```python
@pipeline("Conditional greeting")
def conditional_greeting(enabled: In[str]) -> Out[str]:
    greeting = write_greeting(who="world", is_enabled=enabled)
    return greeting.out
```

This emits the canonical task field rather than a component argument:

```yaml
isEnabled:
  graphInput:
    inputName: enabled
```

`is_enabled=` supports Python booleans (serialized as lowercase `"true"` / `"false"` strings), string constants, `In[...]` graph inputs, and previous task outputs such as `is_enabled=gate.Output`. It is container-component task metadata; component function parameters are not implicitly conditions.

If a component itself declares an input named `is_enabled`, bind that component argument separately while using the call-site keyword for task metadata:

```python
@task(image="python:3.12")
def work(is_enabled: str, message: str) -> str:
    return message

@pipeline("Input-name collision")
def collision(runtime_condition: In[str]) -> Out[str]:
    result = work.bind(is_enabled="component-input-value")(
        message="hello",
        is_enabled=runtime_condition,
    )
    return result.Output
```

The bound value remains under `arguments.is_enabled`; the call-site value emits as `isEnabled`. Tangle does not evaluate conditions on graph-component tasks, so `subpipeline(...)(is_enabled=...)` is rejected with guidance to condition tasks inside the child pipeline. A child graph input with that name remains available through `subpipeline(...).bind(is_enabled=...)(...)`. There is no `condition` alias.

##### Task execution options and caching

Tangle caches task results, so a task that reads state which changes between runs (a run's `createdBy`, wall-clock time, an external table that the graph does not depend on) must opt out of caching explicitly. Use the reserved task-call metadata keyword `max_cache_staleness=`:

```python
@pipeline("Scheduled-run gate")
def scheduled_gate() -> Out[str]:
    is_scheduled = read_runtime_state(
        name="CLOUD_PIPELINES_PIPELINE_RUN_CREATED_BY",
        max_cache_staleness="P0D",
    )
    return is_scheduled.Output
```

This emits the canonical task field rather than a component argument:

```yaml
executionOptions:
  cachingStrategy:
    maxCacheStaleness: P0D
```

`P0D` means "never reuse a cached result"; any other ISO-8601 duration (`P7D`) caps how stale a reusable result may be. For the rest of `ExecutionOptionsSpec`, use the general `execution_options=` passthrough:

```python
uploaded = flaky_upload(
    payload=data.Output,
    execution_options={"retryStrategy": {"maxRetries": 3}},
)
```

Both keywords may be combined; `max_cache_staleness=` wins over a `cachingStrategy.maxCacheStaleness` supplied through `execution_options=`, and every other passthrough field is preserved. A mapping passed as `execution_options=` is never mutated, so one shared constant can be reused across tasks.

Tangle models exactly two execution-option groups today — `cachingStrategy.maxCacheStaleness` and `retryStrategy.maxRetries` (required whenever `retryStrategy` is present). Any other key is rejected at compile time: the backend ignores unmodeled keys silently, so accepting one would advertise a setting that never takes effect.

Execution options are STATIC compile-time settings, so `max_cache_staleness` takes an RFC3339 duration string and `retryStrategy.maxRetries` a non-negative integer. Graph inputs, task outputs, `dynamic_secret(...)`, and `raw(...)` values are rejected because the backend does not resolve them for `executionOptions`. Passing an empty `execution_options={}` is an error — omit the keyword instead.

If a component itself declares an input named `max_cache_staleness` or `execution_options`, bind that component argument separately while using the call-site keyword for task metadata:

```python
result = work.bind(max_cache_staleness="component-input-value")(
    message="hello",
    max_cache_staleness="P0D",
)
```

The bound value remains under `arguments.max_cache_staleness`; the call-site value emits as `executionOptions`. Tangle applies caching and retries to container-component tasks, so `subpipeline(...)(max_cache_staleness=...)` and `subpipeline(...)(execution_options=...)` are rejected with guidance to set them on tasks inside the child pipeline. A child graph input with either name remains available through `subpipeline(...).bind(...)`.

See `examples/python_pipeline/execution_options_pipeline.py` for a runnable example.

##### Task images, dependencies, and image IDs

Use `@task(image="...")` to write the component image directly. Use `dependencies_from="pyproject.toml"` when generated components need to install Python dependencies. Several tasks can share one authoring-only `TaskEnv`:

```python
from tangle_cli.python_pipeline import TaskEnv, task

EVAL = TaskEnv(image="python:3.12", dependencies_from="pyproject.toml")

@task(env=EVAL)
def score(...):
    ...
```

Declare the environment in a config file instead with `TaskEnv.from_config(path)`. A relative `path` resolves against the **calling pipeline module**, never the working directory, so the same script selects the same config wherever `tangle` runs from:

```python
EVAL = TaskEnv.from_config("envs.yaml")  # relative to THIS file
```

```yaml
# envs.yaml
image: python:3.12
dependencies_from: pyproject.toml  # relative to THIS config file
```

The file is read with the same loader `--config` uses, so the `_select` directive works here identically — the case is chosen exactly and case-sensitively by an environment variable, and an unset or unmatched variable is a hard error unless an explicit `default` branch is authored (no implicit default, no implicit production):

```yaml
_select:
  env: DEPLOY_ENVIRONMENT
  cases:
    production:
      image: registry.example/scoring:prod
    staging:
      image: registry.example/scoring:staging
  default:
    image: python:3.12
```

The document must resolve to exactly one object, and its keys must be named parameters of the generated `__init__` of the class `from_config` was called on — `InitVar` pseudo-fields included, `ClassVar`s and `field(init=False)` excluded — so these files stay environment-only: pipeline concerns such as file paths, versioning, annotations, schedules, or subscriptions have no field to land in and are rejected with the allowed field names. `from_config` is generic — any `TaskEnv` dataclass subclass inherits it unchanged, returns its own type, and is validated against its own fields:

```python
from tangle_cli.python_pipeline import TaskEnv

@dataclass(frozen=True)
class GpuEnv(TaskEnv):
    accelerator: str = ""

    def __post_init__(self):
        super().__post_init__()
        if self.accelerator not in ("gpu", "tpu"):
            raise ValueError("GpuEnv.accelerator must be one of: gpu, tpu")

GPU = GpuEnv.from_config("envs.yaml")  # accepts image, dependencies_from, accelerator
```

Failures raise `CompileError` naming the resolved config path. A config file is untrusted input, so **no diagnostic echoes a config value**. Keys are rendered through a capped, control-character-scrubbing renderer, and a constructor's own validation text is never quoted — it could embed a value directly, nested inside a structure, or transformed (lower cased, sliced, re-encoded). The rejected exception is also kept off `__cause__`/`__context__`, since `traceback.format_exception` would otherwise print it into the same CI log. The message names the class and the fields present, and points at constructing the class directly to see the validation error:

```
GpuEnv.from_config: /repo/envs.yaml case is not a valid GpuEnv (fields present:
accelerator, image). Its validation message is withheld because it can contain
config values; construct GpuEnv(...) directly to see it.
```

Calling `GpuEnv(...)` directly is unaffected and raises the ordinary `ValueError` with its full message.

See `examples/python_pipeline/task_env_from_config/` for a runnable example.

Use `@task(image_id="eval-slim")` when source should carry a logical image name instead of a concrete registry reference. Downstream code can register defaults with `register_image_id(...)`, and callers can override at compile time with repeatable `--image ID=REF`:

```bash
uv run tangle sdk pipelines compile pipeline.py -o pipeline.yaml \
  --image eval-slim=registry.example/eval-slim@sha256:...
```

An explicit `image="..."` wins over `image_id=...`. If an `image_id` has neither a registered default nor a `--image` override, compile fails.

##### Subpipelines and existing components

Use `subpipeline(child_pipeline)(...)` to call another Python `@pipeline` as one task in a parent graph. The child compiles to a subgraph sidecar under `<output>.subgraphs/`, and the returned handle exposes the child pipeline's declared outputs.

```python
from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline

@pipeline("Child")
def child(seed: In[str]) -> Out[str]:
    ...

@pipeline("Parent")
def parent(seed: In[str]) -> Out[str]:
    child_result = subpipeline(child).named("Run child")(seed=seed)
    return child_result.wait_for_output
```

Use `ref(url=...)`, `ref(name=...)`, or `ref(digest=...)` to call an existing component YAML or published component instead of authoring a local `@task`. Use `@registered(fragment=..., gen_config=...)` for operation wrappers that are already present in an existing `gen_config.yaml`; the compiler rewrites those calls to `resolve://...#fragment` without generating a new sidecar.

##### Dynamic arguments and runtime placeholders

Task argument values can be literals, graph inputs, task outputs, or supported dynamic data. Use `dynamic_secret("NAME")` to emit a runtime secret reference:

```python
from tangle_cli.python_pipeline import dynamic_secret

call_api(api_key=dynamic_secret("OPENAI_API_KEY"))
```

Use `raw("...")` only for string values that intentionally contain a `{{name}}` runtime placeholder substituted by the component itself. `raw()` is not a compile-time Jinja escape hatch; `{% ... %}` and `{# ... #}` are rejected.

##### Unwrapped dict task inputs

Python-authored pipelines can mark one or more `dict[str, T]` task parameters for unwrapping:

```python
from cloud_pipelines import components
from tangle_cli.python_pipeline import Out, pipeline, task


@task(image="python:3.12")
def make_greeting(name: str) -> str:
    return f"hello {name}"


@task(image="python:3.12", unwrap="items")
def join_greetings(out: components.OutputPath("Text"), items: dict[str, str], prefix: str = "joined"):
    with open(out, "w") as fh:
        fh.write(f"{prefix}: " + " | ".join(items[key] for key in sorted(items)))


@pipeline("Greeting pipeline")
def greeting_pipeline() -> Out[str]:
    world = make_greeting.named("world_source")(name="world")
    tangle = make_greeting.named("tangle_source")(name="tangle")
    joined = join_greetings.named("join")(
        prefix="demo",
        items={
            "who_1": world.Output,
            "who_2": tangle.Output,
            "literal": "plain value",
        },
    )
    return joined.out
```

`unwrap="items"` tells the compiler that the caller-provided entries in `items` should become explicit component inputs. The call above compiles the consumer task arguments as `items__who_1`, `items__who_2`, and `items__literal`; task-output values remain normal graph edges and literal values remain literals. The generated components sidecar persists the exact flattened schema under `local_from_python.unwrapped_inputs`, including the generated input names and inferred value type. Hydrate passes that schema back into Python component generation so the regenerated component has the same flattened inputs even though hydrate no longer has access to the original Python call-site dict. The generated runtime wrapper then re-wraps those CLI arguments back into the original `items` dict before calling `join_greetings(...)`.

Use `unwrap=["items", "metadata"]` to unwrap multiple dict parameters. The caller owns the key names; keys may contain letters, numbers, `_`, and `-`, and become `param__<key>` component inputs. Empty dicts are rejected because they do not define a component interface. If a generated name would collide with a fixed parameter or another generated name, compile fails before writing artifacts. Equivalent key sets are canonicalized for schema hashing, so two call sites with the same keys in different insertion orders dedupe to the same component fragment.

#### Pipeline run submission and validation

`submit` hydrates refs by default and builds an API submit payload with `root_task.componentRef.spec`. Use `--no-hydrate` to submit the local YAML structure as-is. Use `--dry-run` to print the payload without creating a run.

Before creating a run—or printing a `--dry-run` payload—`submit` runs the same authoring validation as `tangle sdk pipelines validate` on the hydrated/resolved pipeline spec (or on the as-is spec when `--no-hydrate` is used). Invalid specs fail locally with `Pipeline validation failed` errors before the run-submission API call. For example, the pipeline root must be a graph (`implementation.graph`), so a bare `implementation.container` root is rejected before the run is submitted; missing required component inputs and invalid task output references are rejected when component specs are available.

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

The top-level `import tangle_cli` is lightweight and does not import static bindings eagerly. Normal installs include `tangle-api`; source checkouts or downstream embeddings may instead provide a local `tangle_api.generated` package before importing `tangle_cli.client`.

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

For a project-local custom API package, write both the schema snapshot and generated modules under that project's source tree, then run tools/tests from the project environment so `src/tangle_api` is earlier on `sys.path` than the official site-packages package:

```bash
uv run python -m tangle_cli.openapi.codegen \
  --openapi-url https://api.example/openapi.json \
  --openapi src/tangle_api/schema/openapi.json \
  --out src/tangle_api/generated
```

That project-local `tangle_api` package can be an editable/package source tree. If you ship the custom API bindings as a wheel or source distribution, use the distribution name `tangle-api` and a compatible version for the `tangle-cli` release you are using. A PEP 440 local version such as `0.1.0+yourorg` can satisfy a public `==0.1.0` dependency while distinguishing your private build. Provide that package through your private index, `--find-links`, or uv source configuration so the resolver chooses it instead of the public official package.

Generate from a backend checkout explicitly:

```bash
uv run --group codegen python -m tangle_cli.openapi.codegen \
  --backend-path /path/to/tangle/backend \
  --backend-database-uri sqlite:////tmp/tangle-openapi.sqlite
```

Important codegen options:

- `--out`: directory that receives `__init__.py`, `runtime.py`, `models.py`, and `operations.py`. Defaults to `packages/tangle-api/src/tangle_api/generated`.
- `--operations-class-name`: generated operations mixin class name. Defaults to `GeneratedTangleApiOperations`.
- `--model-alias`: expose a stable public model name from one or more source schema names, e.g. `ComponentSpec=ComponentSpecOutput,ComponentSpecInput`.
- `--request-body-schema` / `--request-body-schema-file`: override a specific operation's JSON request-body schema without mutating the fetched OpenAPI document.

At runtime, more `tangle api ...` commands become available in two ways:

1. Static codegen: regenerate and install/provide a local or packaged `tangle_api` package containing `tangle_api.generated` and, for official-schema CLI discovery, `tangle_api.schema`.
2. Dynamic cache: run `tangle api refresh --base-url ...` and use `--schema-source auto` or `--schema-source cache` to expose cached-only operations through the dynamic CLI.

The supported workaround hierarchy for custom API consumers is: prefer a project-local `src/tangle_api` package that shadows site-packages for that project; if distributing bindings, prefer a compatible private `tangle-api` distribution; reserve `--no-deps` installs or manual uninstalls of the official package for controlled expert environments where you manually provide all dependencies and the generated/custom `tangle_api` package.

## Runtime generated model extension pattern

`tangle_api.generated.models` is a leaf package and codegen emits plain generated Pydantic models directly:

```python
class ComponentSpec(TangleGeneratedModel):
    name: Any = None
    # generated OpenAPI fields...
```

Generated models do not import `tangle_cli` and codegen does not bake downstream extension modules into `tangle_api`. Downstream packages compose their own extended model namespace at runtime. In `tangle_cli.models`, the default CLI mixins are declared in `tangle_cli.generated_model_extensions`:

```python
MODEL_EXTENSIONS = {
    "ComponentSpec": "ComponentSpecExtensions",
    "GetExecutionInfoResponse": "GetExecutionInfoResponseExtensions",
    "GetGraphExecutionStateResponse": "GetGraphExecutionStateResponseExtensions",
}
```

`tangle_cli.models.compose_models(...)` reads those mappings and creates subclasses in the `tangle_cli.models` namespace, e.g. `ComponentSpec(ComponentSpecExtensions, tangle_api.generated.models.ComponentSpec)`, without mutating `tangle_api.generated.models`. The generated operations layer also calls `_response_model(model_name, default)` so `TangleApiClient` can deserialize responses into the CLI-composed classes while the base `GeneratedTangleApiOperations` remains downstream-agnostic.

Downstream projects can use the same pattern in their own namespace: import base classes from `tangle_api.generated.models`, define method/property-only mixins plus a `MODEL_EXTENSIONS` mapping, and compose subclasses locally. Avoid global monkey-patching of `tangle_api.generated.models`.

Built-in `--model-alias` defaults still keep stable public model names such as `ComponentSpec` even when a backend schema uses names like `ComponentSpecOutput` or `ComponentSpecInput`.

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

`ArgsContainer.load(...)` field specs are tuples (see `ArgsContainer._resolve`). To give one field an environment tier, wrap its unchanged spec: `token=EnvField("TANGLE_PROD_TOKEN", (token, None))`. The field then resolves CLI > config > `os.environ["TANGLE_PROD_TOKEN"]` > default; the raw string (empty counts as set) goes through the spec's usual converter, and a conversion error names the variable without echoing its value. Nothing is mapped automatically: fields that are not wrapped never read the environment. `args.origin(name)` reports where each field came from — `cli`, `config`, `env:NAME`, or `default` — without the value.

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
