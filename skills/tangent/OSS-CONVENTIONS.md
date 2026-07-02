# OSS Conventions — Tangent Agent-Skills on `tangle-cli`

> **This file is the single source of truth.** Every ported skill file (SKILL.md,
> `agents/*.md`, `references/*.md`) references this document for the CLI surface,
> invocation rule, auth, artifacts, learnings corpus, logs, annotations, and the
> resolved D1–D14 defaults. When a ported file needs a command, a flag, an env var,
> an auth recipe, or a corpus/artifact/log recipe, it cites *this* document rather
> than re-deriving it. If something here conflicts with an older internal habit
> (an internal dev-env shim, a cloud-bucket URI, an internal backend host), this document wins and the
> internal habit is a release blocker (see §9, Security mandate).

Target CLI: **`github.com/TangleML/tangle-cli`** — binary `tangle`, package
`tangle-cli`, Apache-2.0. The CLI splits the surface into two families:
`tangle api …` (auto-generated OpenAPI wrappers) and `tangle sdk …` (hand-written
SDK / local / compound commands). The ported skills drive the **OSS core** (`sdk`
and `api`) and **never** any internal wrapper/hook layer.

---

## 1. Invocation rule

**Run every command as `uv run tangle …` from a checkout of the `tangle-cli` repo.**

```bash
uv run tangle quickstart
uv run tangle --help
uv run tangle sdk --help
uv run tangle api --help
```

- **Never** prefix a command with an internal env-shim exec wrapper. That internal
  dev-env tooling must not appear in any ported file.
- As of 2026-06-12 `tangle-cli` is **not yet a public PyPI package** (it is
  consumed internally as a vendored git submodule). So install text must say
  `uv run tangle …` **from a checkout**. Present the public install path only as a
  future state:

  > *Once `tangle-cli` is promoted to the public OSS package, you will be able to
  > `pip install 'tangle-cli[native]'` and invoke `tangle …` directly. Until then,
  > use `uv run tangle …` from a checkout of the repo.*

- The `[native]` extra is what enables the static API-backed commands and the
  handwritten `TangleApiClient` wrapper (see §3). In the `tangle-cli` workspace, `uv`
  installs the workspace `tangle-api` package automatically for dev/tests.
- Help is standard cyclopts `--help`. **There is no `--help-extended` / `--help-full`.**
- Skills live **in-repo** (checked into the `tangle-cli` repo). There is **no internal
  bundle-refresh step**; relative cross-references (`agents/*.md`, `references/*.md`)
  resolve directly on disk.

---

## 2. CLI mapping table (internal `<deploy-cli> …` → OSS `tangle …`)

This table is the canonical rename. The left column is the internal command a
porting agent will encounter in the source skill; the right column is the exact
OSS replacement. **Verbs/flags below were verified against the `tangle-cli` source**
(`cli.py`, `pipeline_runs_cli.py`, `pipelines_cli.py`, `components_cli.py`,
`published_components_cli.py`, `artifacts_cli.py`, `secrets_cli.py`).

### Invocation / help / client

| Internal | OSS |
|---|---|
| `<env-shim> -- <deploy-cli> …` | `uv run tangle …` (or installed `tangle …` once promoted) |
| `<deploy-cli> quickstart` | `tangle quickstart` (real; static onboarding text) |
| `--help-extended` / `--help-full` | `--help` |
| `from <deploy_pkg> import TangleApiClient` | `from tangle_cli.client import TangleApiClient` (see §3) |

### `pipeline-runs` (note: **plural** group `pipeline-runs`)

| Internal | OSS |
|---|---|
| `<deploy-cli> pipeline-run submit p.yaml -f c.yaml --hydrate --no-wait` | `tangle sdk pipeline-runs submit p.yaml [--arg K=V \| --args-json JSON] [--annotation K=V]` — **hydrate is the default; there is NO `--no-wait` (submit never waits); there is NO `-f config.yaml` (use `--arg`/`--args-json`, or `--config` for CLI-option defaults)** |
| `… submit … (submit-as-is, no version resolution)` | `tangle sdk pipeline-runs submit p.yaml --no-hydrate` |
| `… submit … --dry-run` (preview payload) | `tangle sdk pipeline-runs submit p.yaml --dry-run` (prints the submit body, creates no run) |
| `<deploy-cli> pipeline-run details RUN_ID --state` | `tangle sdk pipeline-runs details RUN_ID --include-execution-state` |
| `… details … --implementations` | `… details … --include-implementations` |
| `… details … --include-annotations` | `… details … --include-annotations` (unchanged) |
| `… details … --execution-id EXEC_ID` | `tangle sdk pipeline-runs details RUN_ID --execution-id EXEC_ID` |
| (light status) | `tangle sdk pipeline-runs status RUN_ID` (run + derived status summary) |
| (graph execution state) | `tangle sdk pipeline-runs graph-state EXECUTION_ID` |
| `<deploy-cli> pipeline-run logs EXECUTION_ID` | `tangle sdk pipeline-runs logs EXECUTION_ID` (backend-native container logs — see §7) |
| `<deploy-cli> pipeline-run search --name N` | `tangle sdk pipeline-runs search --name N` (also `--created-by`, `--annotation K=V`, `--start-date`, `--end-date`, `--limit N`, `--query JSON`, positional `QUERY`) |
| `<deploy-cli> pipeline-run cancel RUN_ID` | `tangle sdk pipeline-runs cancel RUN_ID` |
| `<deploy-cli> pipeline-run await … --max-wait` | `tangle sdk pipeline-runs wait RUN_ID --max-wait N --poll-interval N [--exit-on-first-failure]` (defaults: max-wait 600s, poll-interval 10s) |
| `<deploy-cli> pipeline-run export RUN_ID out.yaml --dehydrate` | `tangle sdk pipeline-runs export RUN_ID --output out.yaml` (**drop `--dehydrate`; use `--output` — there is no `-o` alias for `export`; omit `--output` to print to stdout**) |
| `<deploy-cli> pipeline-run annotations set RUN_ID K V` | `tangle sdk pipeline-runs annotations set RUN_ID K V` (also `annotations list RUN_ID`, `annotations delete RUN_ID K`) |

### `pipelines` (local pipeline ops — **these live under `pipelines`, NOT `pipeline-runs`**)

| Internal | OSS |
|---|---|
| `<deploy-cli> pipeline-run validate p.yaml` | `tangle sdk pipelines validate p.yaml` |
| `<deploy-cli> pipeline auto-layout p.yaml` | `tangle sdk pipelines layout p.yaml [--recursive] [-o out.yaml]` |
| `<deploy-cli> pipeline hydrate t.yaml out.yaml` | `tangle sdk pipelines hydrate t.yaml -o out.yaml [--var K=V]` |
| `<deploy-cli> pipeline view … --hydrate` | `tangle sdk pipelines diagram p.yaml` (Mermaid) / `tangle sdk pipelines layout p.yaml` — **no GUI viewer** |
| `<deploy-cli> …-dehydrate-…` / any `dehydrate` verb/flag | **No OSS `dehydrate` command or `--dehydrate` flag.** Drop it (see §10, D1). `pipeline_dehydrator.py` exists only as unwired library code. |

### `components` (local authoring) vs `published-components` (registry)

| Internal | OSS |
|---|---|
| `<deploy-cli> component generate from-python s.py` | `tangle sdk components generate from-python s.py [--function NAME] [--image REG/IMG:TAG] [--output OUT] [--name NAME] [--mode inline\|bundle] [--dependencies-from REQ] [--strip-code] [--resolve-root DIR]` |
| `<deploy-cli> component generate from-docker …` / `--quick` / `--engine` | **No image build/push in the CLI.** Build/push the image yourself, then `generate from-python --image <registry/img:tag>`. `set-container-image` is a `NotImplementedError` stub. (See §10, D7.) |
| `<deploy-cli> component generate from-dbt …` | **Not in the CLI surface.** Drop; treat as out of scope. |
| `<deploy-cli> component bump-version c.yaml` | `tangle sdk components bump-version c.yaml [--set-version V] [--update-timestamp]` (**bump-version is under `components`**) |
| `<deploy-cli> component publish c.yaml` | `tangle sdk published-components publish c.yaml [--image …] [--name …] [--description …] [--annotations JSON] [--dry-run] [--published-by …]` (**publish is under `published-components`**) |
| `<deploy-cli> publish-all …` | **No `publish-all`.** Pass a `--config` YAML/JSON list (or `_defaults` + `configs`) to the same `published-components publish`; it aggregates and exits nonzero on any error. |
| `<deploy-cli> component inspect --name N --full-spec` | `tangle sdk published-components inspect --name N --full-spec` (or `inspect --digest DIGEST`; exactly one of NAME or `--digest`) |
| `<deploy-cli> component search-v2 …` / `search … --semantic`/`--fuzzy`/`--regex`/`--schema` | `tangle sdk published-components search NAME [--digest D] [--published-by U] [--include-deprecated]` — **keyword/name/digest only; NO v2/semantic/fuzzy/regex/schema variants** (see §10, D11). May return empty on a fresh OSS install (feature off by default). |
| `<deploy-cli> component deprecate DIGEST` | `tangle sdk published-components deprecate DIGEST [--superseded-by DIGEST]` |
| `<deploy-cli> docs standard_components` | `tangle sdk published-components library` (curated standard library) |

### `secrets`, `artifacts`, `docs`

| Internal | OSS |
|---|---|
| `<deploy-cli> secrets {list,create,update,delete}` | `tangle sdk secrets {list,create,update,delete}` — create/update take `--value`/`-v`, **prefer `--from-env`/`-e ENVVAR`** (avoids shell-history exposure), `--description`/`-d`, `--expires-at`; delete prompts unless `--force` |
| `<deploy-cli> artifacts get RUN_ID -q …` | `tangle sdk artifacts get RUN_ID -q '<JSON>'` (**metadata-only**, see §5). `-q`/`--query` is a JSON string with optional keys `tasks`, `components`, `executions`, `artifact_ids` |
| `<deploy-cli> artifacts download RUN_ID -q … -o ./dir` | **No download-to-disk command.** Use the signed-URL fetch recipe in §5 / §10 D2. |
| `<deploy-cli> docs {pipeline,component,debugging_runs,standard_components}` | **No `docs` command.** Point to `tangle sdk <group> --help`, `tangle sdk published-components library`, and the public OSS docs at `github.com/TangleML/website/tree/master/docs` (see §10, D10). |

### `auth` / SA setup / scheduler / run-as

| Internal | OSS |
|---|---|
| `<deploy-cli> auth …` / `<sa-setup>` / Workload-Identity / Terraform | **No `auth` command group and no SA-setup command in the CLI.** Auth is plain env/flags (§4). The replacement auth-wizard is a thin token/header credential helper (§10, D5). |
| `<schedule-pipeline>` / scheduler | **No scheduler in OSS.** Reduce "do not schedule" guidance to a generic conceptual caution; do not name a `schedule` command (§10, D9). |
| `--run-as IDENTITY` | `--run-as` exists on `submit` but the **OSS default hooks do not support it** (downstream extension seam only). Drop run-as examples (§10, D9). |

**Invocation rule for the whole table:** every left-column command, however it was
written internally, becomes the right-column form prefixed with `uv run` (e.g.
`uv run tangle sdk pipeline-runs submit …`). Auth flags from §4 attach to any
API-backed command.

---

## 3. Programmatic client

The stable public wrapper for Python tooling is:

```python
from tangle_cli.client import TangleApiClient

# Defaults to the local dev backend; pass base_url + auth for a remote backend.
client = TangleApiClient(
    "http://localhost:8000",
    token=None,          # Bearer token shorthand
    auth_header=None,    # full Authorization value, e.g. "Bearer …" / "Basic …"
    header=None,         # one "Name: value" string or a list of them
    headers=None,        # mapping form, if you prefer a dict
)
run = client.pipeline_runs_get("run-id")
existing = client.find_existing_components(
    ["component-name"],
    published_by_substring="alice@example.com",
)
```

- Constructor signature: `TangleApiClient(base_url, *, token=, auth_header=, header=, headers=, …)`.
  A bare `TangleApiClient()` only works against the default localhost backend.
- `TangleApiClient` lives in `tangle_cli.client` and inherits generated endpoint
  methods from `tangle_api.generated.operations.GeneratedTangleApiOperations`.
  **Importing it requires the native bindings** — install the `[native]` extra (or
  provide a local `tangle_api.generated` package) before `from tangle_cli.client import …`.
  The top-level `import tangle_cli` is intentionally native-free.
- **Prefer the CLI over Python snippets for status/polling.** Any internal snippet
  that calls unverified methods like `get_pipeline_run`, `get_execution_graph_state`,
  `set_verbose`, `.status_totals`, or `.root_execution_id` must be replaced. The
  verified surface is `client.pipeline_runs_get(run_id)` and the
  `find_existing_components(...)` semantic helper; for status/graph state the
  **preferred** approach is the purpose-built CLI:
  - `tangle sdk pipeline-runs status RUN_ID` (run + derived status summary)
  - `tangle sdk pipeline-runs graph-state EXECUTION_ID` (graph execution state)

  Skills should call those CLI commands rather than hand-rolling a light-poll loop
  in Python (see §10, D14).

---

## 4. Auth & environment

OSS auth is **explicit and layered**: explicit CLI option > `--config` file value >
environment default. **There is no `auth` command group.** These flags/env vars
replace the internal identity/session systems, cloud SA impersonation, the
internal backend's auth verification, and the internal package index **entirely**.

| CLI option | Env var(s) | Purpose |
|---|---|---|
| `--base-url` | `TANGLE_API_URL` | API origin. Defaults to the local dev API URL when omitted. |
| `--token` | `TANGLE_API_TOKEN` | Bearer-token shorthand. |
| `--auth-header` | `TANGLE_API_AUTH_HEADER`, `TANGLE_AUTH_HEADER` | Full `Authorization` value such as `Bearer …` or `Basic …`. |
| `-H` / `--header` | `TANGLE_API_HEADERS` | Extra headers. Repeatable as CLI flags; env accepts a JSON object **or** newline-separated `Name: value` entries. |
| `--config` | — | YAML/JSON defaults (single object, a list, or `_defaults` + `configs`). |
| — | `TANGLE_VERBOSE=1` | Redacted HTTP request/response diagnostics (separate from progress logs). |

- **Env-var rename:** internal `TANGLE_AUTH` (username:password) → OSS
  `TANGLE_API_TOKEN` (Bearer) **or** `TANGLE_API_AUTH_HEADER` (full header for
  Basic/other schemes). The deploy-time source-attribution env var → dropped (see §8).
- **Run links:** replace internal run-URL links (`https://<internal-backend-host>/runs/<id>`) with
  `<base-url>/runs/<id>` **or** "inspect via `tangle sdk pipeline-runs details RUN_ID`".
- **No internal package index.** Resolve dependencies against public PyPI
  (`uv sync` / `tangle-cli[native]`).
- Example for a protected backend:

  ```bash
  uv run tangle sdk pipeline-runs submit pipeline.yaml \
    --base-url https://api.example \
    --auth-header 'Bearer …' \
    -H 'X-Gateway-Auth: …' \
    --log-type console
  ```

- **Auth-wizard replacement (D5):** a thin wizard that (1) asks for `--base-url`,
  (2) picks one credential mechanism (`--token` vs `--auth-header` vs `-H`),
  (3) shows the flag / env / `--config` setup, (4) verifies with a cheap call
  (`tangle sdk pipeline-runs search --limit 1`), and (5) interprets 401/403/429.
  No GCP/Workload-Identity/Terraform modes.

---

## 5. Artifacts

`tangle sdk artifacts get RUN_ID -q '<JSON>'` is **metadata-only**. It returns
records of the form `{id, uri, size, hash}` (and a `count`); the `uri` is
**backend-agnostic**. There is **no `artifacts download` / `-o` to disk** in the CLI.

- **URI scheme is HuggingFace, not an internal cloud-bucket scheme.** Under the OSS `tangle` backend the
  storage provider is `huggingface_repo_storage.py`, so artifact URIs look like:

  ```
  hf://{model|dataset|space}s/<user>/<repo>@<branch>/<path>
  ```

  e.g. `hf://datasets/acme/eval-artifacts@main/run-123/metrics.json`. **Any skill
  text that parses, echoes, or hard-codes a specific storage scheme (e.g. a
  cloud-bucket URI) must be made scheme-agnostic** — read the `uri` field; do not assume a scheme.

- **To fetch bytes** (the standard recipe — cite this from reporter/debugger/
  researcher/reviewer/scenario-builder/step-5):

  1. Get metadata and read the `uri`:
     ```bash
     uv run tangle sdk artifacts get RUN_ID -q '{"artifact_ids":["<artifact-id>"]}'
     ```
  2. Ask the backend for a signed URL:
     ```bash
     uv run tangle api artifacts signed-artifact-url --id <artifact-id>
     ```
  3. Fetch with a generic client — `curl -L "<signed-url>" -o ./out`, or for
     `hf://` URIs `huggingface_hub` (`hf_hub_download` / `snapshot_download`).

  Keep this recipe in one place (here); other files point at it rather than
  repeating it.

- **Metadata-only is sufficient for many checks.** Reviewers can verify
  existence/size/hash from `artifacts get` alone. Only mandate byte-fetching where
  per-example or metric-content analysis genuinely requires it (reporter), and there
  point at the signed-URL recipe above (see §10, D2).

---

## 6. Learnings corpus

Replaces the internal cloud-storage learnings bucket. Two tiers:

- **Default — local directory.** A configurable `LEARNINGS_DIR`, default
  `$SCENARIO_DIR/learnings/`, overridable via env. Records are **run_id-keyed**
  JSON. Replace the internal cloud-storage upload command with a plain copy/write:

  ```bash
  mkdir -p "$LEARNINGS_DIR/<scenario>"
  cp learning.json "$LEARNINGS_DIR/<scenario>/learning-<run_id>.json"
  ```

  Keep the run_id-keying scheme (`active_run_id` for research, `best_run_id` for
  the learning) and keep the resilience event — but reword "upload" → "record"
  (the event `learning_upload_failed` becomes `learning_record_failed`; see §8/§10
  D3). Genericize the word "bucket" → "corpus directory".

- **Shared tier — HuggingFace dataset repo.** For a team-shared corpus (the shared
  bucket's real purpose), the OSS-native equivalent matches the backend's own
  storage provider:

  ```
  hf://datasets/<org>/<corpus>@main/<scenario>/<run_id>.json
  ```

  pushed with `huggingface_hub` (`HfApi().upload_file(...)`). Document local-dir as
  the default and the HF dataset as the optional shared tier. **Never** reintroduce
  an internal cloud-storage bucket.

---

## 7. Logs

Drop the **internal observability stack** (its hosts, datasets, `--source`
selectors, and auth env vars) entirely. The OSS log surface is split by what stores the data:

- **Container logs (application stdout/stderr)** — backend-native, the **only** CLI-native
  log surface:

  ```bash
  uv run tangle sdk pipeline-runs logs EXECUTION_ID
  ```

  (Note: this is keyed by **EXECUTION_ID**, not run id.)

- **Kubernetes/system events (OOM, eviction, scheduling, "pod not found")** — the
  Tangle backend does **not** store these; they are **launcher-specific**. Frame as
  "consult your launcher's runtime," with concrete per-launcher hints:

  | Launcher | System-event source |
  |---|---|
  | `kubernetes`, `google_kubernetes` | `kubectl get events`, `kubectl describe pod <pod>` |
  | `local_docker` | `docker logs <container>`, `docker inspect <container>` |
  | `skypilot` | the cluster console / `sky logs` |
  | `huggingface` | the Space's logs in the HF UI |

- This is a genuine **degradation** vs the internal observability path for *infra-failure*
  diagnosis (no unified system-event search). The debugger skill must note the
  weaker INFRA-failure signal and lean on container logs + launcher-native events.

---

## 8. Annotations

- **Drop the internal dashboard-compatibility annotation marker** and the entire
  source-attribution mechanism (the deploy-source env var / `--source` flag).
  These were pure downstream attribution for the internal dashboards and are safe to
  remove for OSS.
- **Keep the generic annotation mechanism.** Portable annotations stay:
  `--annotation session=… --annotation round=… --annotation type=… --annotation label=…`
  (and `annotations set RUN_ID K V` after the fact).
- **Optional provenance only.** If a skill wants to mark its runs, use a single
  optional `--annotation source=tangle-cli`. Do **not** make it mandatory and do
  **not** reintroduce the internal dashboard marker.

---

## 9. Security mandate (strip-list — release blocker)

Per the team's security mandate, internal references in an OSS repo are
**attack vectors** (supply-chain + reconnaissance), not cosmetic. Treat **any**
residual internal reference in a ported file as a **release blocker**.

No internal identifier may appear in **any** ported file — prose, code,
examples, comments, links, YAML, or annotations. The forbidden **categories**
(named here generically, so this public file does not itself carry the literal
strings it forbids):

- Internal backend / gateway / observability **hostnames**, and any internal
  corporate domain.
- Cloud-storage **bucket URIs and bucket names** tied to internal infra, and the
  helper code or CLIs that read or upload them.
- Internal **observability**, **data-warehouse**, **experimentation**, and
  **session / identity** systems — their hosts, datasets, source selectors, and
  auth/session env vars.
- Internal or vendor **compute** project IDs and ML-platform endpoints.
- Internal **package-index** URLs.
- Internal **dev-environment** tooling and monorepo source / zone paths.
- Employee usernames / emails as data (PII), and service-account names.
- Internal component digests presented as resolvable.
- Internal dashboard-attribution annotations and source-attribution env vars.

**Pre-release sweep.** The authoritative literal deny-list and the `grep`
patterns that enforce it **must live in private validation tooling, not in this
public tree** — shipping the literal internal strings, even inside a deny-list,
would itself violate the mandate. Before declaring any file done, run that
private sweep over the ported tree; then verify two non-sensitive structural
invariants directly here: every relative `../references/*.md` / `agents/*.md`
link still resolves, and no dropped subagent (the uploader) is still spawned.

---

## 10. Resolved D1–D14 defaults

These are **final** (from plan §4, several pre-resolved by team Slack in §0c).
Apply them identically across all files.

- **D1 — Dehydrate.** Reframe the iteration loop around plain `export` (root spec
  as-is) + hand-edit + `submit` (hydrate is the default). **Drop all `--dehydrate`
  flags and the `grep -q '  spec:'` "dehydrate first" guards.** Do **not** add a new
  dehydrate command. Accept hydrate-as-no-op on already-inline exports.

- **D2 — Artifact bytes.** Document the `artifacts get` → `tangle api artifacts
  signed-artifact-url` → fetch recipe **once** (here, §5). Keep reporter's per-example
  analysis requirement but point it at that recipe. Reviewer relies on
  **metadata-only** verification.

- **D3 — Learnings corpus.** Canonical local path `LEARNINGS_DIR`, default
  `$SCENARIO_DIR/learnings/` (env-overridable). Keep the corpus concept and the
  resilience event, renamed `learning_record_failed`. Shared tier = HF dataset repo
  (§6).

- **D4 — Data-source components.** **Drop the dedicated data-source recipes**
  (`Promote/Load/Find data source`, `Download from cloud storage`). Reframe artifact reuse as
  "record `run_id` / artifact `uri` locally and re-reference." Gate
  `data-sources.md` as a **conditional** pattern doc ("if your backend provides
  these components"), never a prerequisite.

- **D5 — Auth wizard.** **Replace** the GCP/Workload-Identity/Terraform wizard with a
  minimal token/header wizard (see §4: ask base-url → pick `--token`/`--auth-header`/`-H`
  → show flag/env/`--config` setup → verify with `tangle sdk pipeline-runs search
  --limit 1` → interpret 401/403/429). Drop Modes 1/2 wholesale.

- **D6 — Uploader agent.** **Drop the agent** (no OSS upload command; it relied on
  the user's cloud-CLI auth + a hard-coded cloud-storage-download component). Preserve only the
  portable kernels (file-vs-directory trailing-slash semantics, unique-filename
  guidance, `componentRef` wiring shape) inside `uploading-artifacts.md`. Fix every
  cross-reference that spawned the uploader to self-contained generic text.

- **D7 — Containerized component build.** **Drop**
  `containerized-component-iteration.md`. Document `generate from-python --image
  <registry/img:tag>` (image built/pushed by the user with their own docker/podman)
  as the only component-generation path in `builder.md`. `set-container-image` is a
  stub; do not use it.

- **D8 — Secrets.** **Drop GCP Secret Manager "Pattern B"** and the SA-impersonation
  account-scoping section. **Keep Pattern A** (`dynamicData.secret`). Reduce
  account-scoping to one generic paragraph: "secrets belong to the authenticating
  identity; re-run with a different `--token` to create under another identity."
  Prefer `secrets create --from-env ENVVAR` over `--value` in examples.

- **D9 — Scheduler / run-as.** No scheduler command to name — reduce "do not
  schedule promote" to a generic conceptual caution. **Drop `--run-as` examples**
  (OSS default hooks reject it; it is a downstream extension seam only).

- **D10 — `docs` command.** **Drop `docs` invocations.** Point to
  `tangle sdk <group> --help`, `tangle sdk published-components library`, in-repo
  schema docs, and the public OSS docs at
  `github.com/TangleML/website/tree/master/docs` (confirmed canonical OSS docs home).

- **D11 — Component search.** Collapse to `tangle sdk published-components search
  NAME` (keyword/name/digest only); **drop semantic/fuzzy/regex/schema variants.**
  The PublishedComponents / remote-component-library feature is **off by default in
  OSS**, so researcher/builder must treat component discovery as **optional** and
  **tolerate empty results** on a fresh install — never a hard prerequisite of a
  workflow step.

- **D12 — Run arguments.** Standardize on `--arg K=V` and `--args-json '<JSON>'`
  (or `--args-json @file.json` where a file is wanted). **Rewrite every
  `-f *.config.yaml` submit example** accordingly. `--config` carries CLI-option
  defaults (base-url/auth/log-type), **not** pipeline run args.

- **D13 — Example-scenarios bundle.** **Drop all 16 files** (`INDEX.md` + `01`–`15`)
  — internal-domain case studies with near-zero portable CLI content and many
  strip-list violations. **Author 1–2 fresh synthetic examples** on a public dataset
  (e.g. MSLR/LETOR for ranking; a public image-text set for matching) plus a new
  `INDEX.md`. Any synthetic scenario must name only public datasets/models and
  treat scheduling/run-as/data-source promotion as extension-only.

- **D14 — Step-4 light-poll client API.** **Replace the Python snippet** with the
  CLI: `tangle sdk pipeline-runs status RUN_ID` and `tangle sdk pipeline-runs
  graph-state EXECUTION_ID`. Do not ship a snippet that calls unverified client
  methods.
