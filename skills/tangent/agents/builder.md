---
name: builder
description: Build, modify, and iterate on Tangle pipelines and components
tools: read, write, grep, glob, bash
---

# Tangent: Builder Agent

Build new pipelines and components, iterate on existing ones, and prepare
components with local code changes for testing.

## Tools

**Run every `tangle` command via Bash as `uv run tangle …` from a checkout of the `tangle-cli` repo** (see [`../OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §1). Once `tangle-cli` is promoted to the public OSS package you will be able to
`pip install 'tangle-cli[native]'` and invoke `tangle …` directly; until then use
`uv run tangle …`.

Run `uv run tangle quickstart` to discover available commands. Use `--help` on any
command or group for detailed usage (there is no `--help-extended` / `--help-full`).
For schema and concept docs, use `uv run tangle sdk <group> --help`,
`uv run tangle sdk published-components library`, and the public OSS docs at
`github.com/TangleML/website/tree/master/docs`.

| What you need | Command |
|---|---|
| Export run as YAML | `uv run tangle sdk pipeline-runs export RUN_ID --output output.yaml` |
| Inspect a published component | `uv run tangle sdk published-components inspect --name "Name" --full-spec` |
| Search components (optional; may be empty) | `uv run tangle sdk published-components search "keyword"` |
| Curated standard library | `uv run tangle sdk published-components library` |
| Generate from Python | `uv run tangle sdk components generate from-python source.py [--image REG/IMG:TAG]` |
| Bump version | `uv run tangle sdk components bump-version component.yaml` |
| Hydrate refs | `uv run tangle sdk pipelines hydrate template.yaml -o output.yaml` |
| Validate pipeline | `uv run tangle sdk pipelines validate pipeline.yaml` |
| Auto-layout DAG | `uv run tangle sdk pipelines layout pipeline.yaml` |
| Submit pipeline | `uv run tangle sdk pipeline-runs submit pipeline.yaml [--arg K=V \| --args-json JSON]` |
| Run details | `uv run tangle sdk pipeline-runs details RUN_ID --include-execution-state` |
| Component as used | `uv run tangle sdk pipeline-runs details RUN_ID --execution-id EXEC_ID --include-implementations` |

Component discovery (search/library) is **optional** and **off by default** on a
fresh OSS install — treat it as a best-effort lookup and tolerate empty results;
never block a workflow step on it.

## Credentials & secrets — read before wiring any API key

If a pipeline argument is or looks like a credential (API key, bearer/OAuth
token, HF token, storage/registry token, anything matching `*_KEY` / `*_TOKEN`
/ `*_SECRET` / `*_PASSWORD`, anything a component reads from `os.environ`), it
MUST be wired through `dynamicData.secret`, never inlined.

**Hard rule:** do not paste a credential value into a pipeline YAML, an input
field, a `constantValue:`, a `cli_args:` entry, a run-arg passed via `--arg` /
`--args-json` / `--config`, or a component image — even if you can read the
plaintext from your environment or a secret store. Treat raw credential values
as poison.

The correct flow is: `uv run tangle sdk secrets list` → if the secret doesn't exist,
ask the human to create it with `uv run tangle sdk secrets create NAME --from-env NAME`
(`--from-env`/`-e` reads from an env var so the value never lands in shell
history; the agent never touches the value) → reference it via
`dynamicData.secret: { name: "NAME" }` on the consuming argument. See
[`../references/secrets.md`](../references/secrets.md) for the full workflow,
detection heuristics, identity-scoping rules, and anti-patterns. When in doubt,
stop and ask the human — never inline a value to unblock yourself.

## Pipeline YAML Structure

Tangle pipelines are nested subgraphs. Inputs flow through the hierarchy via
`graphInput` wiring: top-level task output → subgraph input → nested subgraph
input → leaf task argument. Trace the wiring at each level before modifying.

## Workflows

### Ingesting a staged artifact

When a pipeline needs to consume a locally-staged file or directory (dataset,
model checkpoint, config bundle, …), the artifact must first live somewhere the
backend can read, and then be wired into a task argument. There is no built-in
upload command in OSS, so:

1. Put the artifact in storage your backend can reach and obtain its `uri`.
   Under the OSS backend, artifact URIs are **scheme-agnostic** and typically
   HuggingFace-shaped (e.g. `hf://datasets/<user>/<repo>@main/<path>`), not a
   single hard-coded cloud scheme. See [`../OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §5
   for the metadata/`uri` model and the signed-URL fetch recipe.
2. Wire the `uri` into the consuming task argument (as a `constantValue:` string,
   or — better — as a run-arg so it is not baked into the spec).

[`../references/uploading-artifacts.md`](../references/uploading-artifacts.md)
covers the portable wiring kernels: file-vs-directory trailing-slash semantics,
unique-filename guidance, and the `componentRef` shape. Pass the `uri` as a
run-config parameter (`--arg uri=<uri>`) when you want to swap the input without
editing the spec.

### Preserving a run's output for reuse

When a pipeline produces an artifact worth keeping past the run's TTL —
a curated eval set, a fine-tuned checkpoint, a generated annotation set,
a frozen feature snapshot — record its identity so a later pipeline can
re-reference it: capture the producing `run_id` and the artifact `uri`
(`uv run tangle sdk artifacts get RUN_ID -q '<JSON>'` returns `{id, uri, size, hash}`
records; see [`../OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §5) and save them
in the scenario's `MEMORY.md` / session log. A downstream pipeline re-references
the artifact by feeding that `uri` into the consuming task argument (typically
as a run-arg).

**⚠️ Sensitive data warning.** Anything you persist for reuse may outlive the run
and be readable by others on a shared backend. **Never persist PII, sensitive
data, contractually restricted datasets, embargoed model weights, or anything
sourced from `secrets`.** If unsure, ask the user — or escalate to the data
owner — before persisting.

**⚠️ Conceptual caution on recurring runs.** Persisting a snapshot on every run
of a recurring job mints a fresh permanent artifact each time and quickly floods
storage. OSS has no scheduler command, but if you wire any external recurring
trigger around a persisting pipeline, get explicit sign-off and a cleanup plan
first.

If your backend exposes dedicated data-source components (`Promote to data
source` / `Load data source` / `Find data source`), you can use them instead of
hand-tracking `uri`s — that is a backend-conditional pattern, not a prerequisite.
See [`../references/data-sources.md`](../references/data-sources.md) for those
recipes; they apply only if your backend provides those components.

### Reusing a preserved artifact

If the pipeline needs a curated artifact someone has already preserved (a prior
round's eval set, a baseline checkpoint, a teammate's annotation set), re-reference
it by its recorded `uri`: feed the `uri` into the consuming task argument, ideally
as a run-arg so the spec stays generic.

If your backend provides a `Load data source` component, you can instead wire it
with the recorded identifier; it exposes the data (file or directory matching the
original shape) plus a provenance record. This is backend-conditional — see Recipe
D in [`../references/data-sources.md`](../references/data-sources.md) for the
guarded pattern. Do not assume those components exist.

### Iterating on an existing run

See [`../references/iterating-on-runs.md`](../references/iterating-on-runs.md) for
the full workflow.

1. **Export**: `uv run tangle sdk pipeline-runs export RUN_ID --output /tmp/pipeline.yaml`
   — exports the root spec as-is (there is no `--dehydrate`). Omit `--output` to print
   to stdout.
2. **Inspect**: `uv run tangle sdk pipeline-runs details RUN_ID --include-execution-state`
   — identify task statuses. (`uv run tangle sdk pipeline-runs status RUN_ID` gives a
   lighter run + derived status summary.)
3. **Modify**: Edit the exported YAML. To swap a component, replace its `digest:`
   or `url: file://` reference with a new `url: file://` pointing to your
   replacement.
4. **Validate**: `uv run tangle sdk pipelines validate /tmp/pipeline.yaml`
5. **Submit** (see Submission Rules in
   [`../references/tangle-tools.md`](../references/tangle-tools.md)):
   ```bash
   uv run tangle sdk pipeline-runs submit /tmp/pipeline.yaml \
     --args-json @/tmp/pipeline.args.json
   ```
   `submit` hydrates by default (it resolves component versions), so there is no
   "dehydrate first" guard and no `--no-wait` — `submit` returns as soon as the
   run is created; poll with `uv run tangle sdk pipeline-runs wait RUN_ID`. Pass run
   arguments with `--arg K=V` / `--args-json '<JSON>'` (or `--args-json @file.json`);
   `--config` carries CLI-option defaults (base-url/auth/log-type), not run args.

### Building a component with local code changes

There is no image build/push step in the CLI. Build and push the container image
yourself with your own tooling (docker/podman), then point the component at it.
`set-container-image` is a `NotImplementedError` stub — do not use it.

1. **Find source code**: Inspect the published component to get source annotations:
   ```bash
   uv run tangle sdk published-components inspect --name "Component Name" --full-spec
   ```
   Check the annotations the publisher attached (e.g. source path, repo, image,
   docs references) to locate the source.

2. **Build and push the image** with your own container tooling, producing a
   tagged image in a registry the backend can pull (e.g.
   `registry.example/img:tag`).

3. **Generate the component YAML** from your Python entrypoint, pointing at the
   image you pushed:
   ```bash
   uv run tangle sdk components generate from-python source.py --image registry.example/img:tag
   ```

4. **Insert into pipeline**: In the exported YAML, change the task's `componentRef`
   from `digest: ...` to `url: file://<path-to-component.yaml>`. Submit normally
   (hydrate is the default).

### Generating a new component

**Before generating, optionally search for existing components** that already do
what you need. Component discovery is off by default on a fresh OSS install, so
treat this as best-effort and tolerate empty results:
```bash
# Optional keyword/name/digest search (may return nothing on a fresh install):
uv run tangle sdk published-components search "<keyword>"
# Curated standard library:
uv run tangle sdk published-components library
```
There are no v2/semantic/fuzzy/regex/schema search variants in OSS. If discovery
returns nothing, just generate the component you need.

**From Python**:
```bash
uv run tangle sdk components generate from-python my_module.py
```
Generates component YAML from a Python function. Looks for a function matching the
filename by default; use `--function <name>` to pick a different one. Pass
`--image registry/img:tag` to bind a container image you built and pushed
yourself. `generate from-python` is the only generation path — there is no
`from-docker` or `from-dbt`.

### Publishing components

```bash
# Bump version first
uv run tangle sdk components bump-version component.yaml

# Publish
uv run tangle sdk published-components publish component.yaml
```
`bump-version` lives under `components`; `publish` lives under
`published-components`. To publish several at once, pass a `--config` YAML/JSON
list (or `_defaults` + `configs`) to the same `published-components publish` — it
aggregates and exits nonzero on any error.

### Validating before submission

Always validate before submitting. See Submission Rules in
[`../references/tangle-tools.md`](../references/tangle-tools.md) for the full
pre-submit checklist.
```bash
uv run tangle sdk pipelines validate pipeline.yaml
```
Use `--verbose` only if validation fails and you need full error details.

## Key Gotchas

- **Image registry access**: The image you bind with `--image` must be pullable by
  the backend. If you get pull errors for private base images, check that the
  registry credentials available to the backend are valid.
- **Image tag verification**: After modifying pipeline YAML (especially with
  `yaml.dump` which reorders keys), verify the image reference is correct before
  submitting.
- **Run details vs inspect**: Use `pipeline-runs details` with `--execution-id`
  and `--include-implementations` to see the component as it was actually used in
  a run. `published-components inspect` shows the latest published version, which
  may differ.
- **Hydrate on submit**: `submit` hydrates by default — it resolves component
  references (`digest:`, `url: file://`, or `name:`) to full inline specs. Pass
  `--no-hydrate` only when you intend to submit a spec exactly as written;
  hydration is a no-op on an already-inline export.
