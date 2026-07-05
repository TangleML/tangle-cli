# Ingesting staged artifacts into Tangle pipelines

The canonical way to bring a locally-staged artifact (data file, model checkpoint,
config bundle, reference dataset, …) into a Tangle pipeline is:

1. **Stage** the artifact somewhere your pipeline can reach it at run time — an
   object store, an HF dataset/model repo, or any URI your backend's ingest
   component understands. (Where bytes live is backend-specific; see
   [Staging the bytes](#staging-the-bytes).)
2. **Add or update** a task in the pipeline that uses your backend's **ingest
   component** to pull the artifact at run time and emit it as a `Data` output.
3. **Wire** the `Data` output into the downstream task that needs the artifact.

An ingest component is the transparent, reproducible ingest path. Do not write a
custom shell-out component or bake the artifact into a container image just to get
it into a pipeline — that hides the dependency from the graph.

> **Backend-dependent.** The OSS `tangle` core does **not** ship a built-in ingest
> component. Whether one is available — and what its name, inputs, and outputs are
> — depends on what components your backend has published. Discover what's
> available with `tangle sdk published-components library` and
> `tangle sdk published-components search <NAME>` (search is **optional** and
> may return empty on a fresh OSS install — see
> [`OSS-CONVENTIONS.md` §10 D11](../OSS-CONVENTIONS.md)). If your backend provides
> no ingest component at all, fall back to a Python component that reads the URI
> itself (see [Gotchas](#gotchas)).

## The ingest component shape

A typical ingest component looks like this:

| Field | Value |
|---|---|
| Name | backend-specific (e.g. `Download artifact`, `Fetch URI`) |
| Input | a URI or path (single blob or directory; **trailing slash means directory** — see [Gotchas](#gotchas)) |
| Output | `Data` (file path or directory path the next task can consume) |

Inspect whatever your backend provides before wiring it in:

```bash
tangle sdk published-components inspect --name "<ingest component>" --full-spec
# or, if you already have the digest:
tangle sdk published-components inspect --digest <DIGEST> --full-spec
```

## Recipe A — Add a new ingest task to an existing pipeline

When the pipeline does not already have an ingest task and you need to introduce
one — e.g., a new feature dataset, a model checkpoint to fine-tune from, an
external evaluation set.

1. **Export the pipeline's root spec** so you can edit it locally:
   ```bash
   tangle sdk pipeline-runs export <RUN_ID> --output /tmp/pipeline.yaml
   ```
   `export` writes the root spec as-is (there is no `--dehydrate`; omit `--output` to
   print to stdout). To iterate on the run, hand-edit this file and `submit` it —
   hydration is the default and resolves component versions for you.

2. **Add the ingest task** to the top-level graph or the appropriate subgraph:
   ```yaml
   tasks:
     ingestInputData:
       componentRef:
         name: "<your backend's ingest component>"
         # Optional: pin by digest after first use for reproducibility.
       arguments:
         uri:
           # Hard-code for a one-shot run:
           constant: "<scheme>://path/to/artifact/"
           # Or, to make the path a run-time parameter:
           # graphInput: {inputName: input_artifact_uri}
   ```

3. **Wire the `Data` output** into the consumer task by replacing whatever
   argument used to supply the artifact:
   ```yaml
   tasks:
     myTrainer:
       arguments:
         training_data:
           taskOutput:
             taskId: ingestInputData
             outputName: Data
   ```

4. **Validate, then submit** (hydrate is the default; `submit` never waits — block
   afterward with `pipeline-runs wait` if you need to):
   ```bash
   tangle sdk pipelines validate /tmp/pipeline.yaml
   tangle sdk pipeline-runs submit /tmp/pipeline.yaml \
     --arg input_artifact_uri="<scheme>://path/to/artifact/"
   tangle sdk pipeline-runs wait <RUN_ID> --max-wait 600
   ```

   (Pass `--arg` only if you wired the URI as a `graphInput` in step 2; for a
   hard-coded `constant` you don't need it.)

## Recipe B — Swap an existing ingest URI

When the pipeline already has an ingest task and you only need to point it at a
new artifact.

1. Find the task in the exported YAML — search for the ingest component (by
   `name:` or `digest:`) or for its `arguments.<uri argument>:`.
2. Replace the value:
   ```yaml
   # Before
   arguments:
     uri:
       constant: "<scheme>://old/path/"
   # After
   arguments:
     uri:
       constant: "<scheme>://new/path/"
   ```
3. Validate + submit as in Recipe A.

## Recipe C — Pass the URI through the run arguments

When you want to keep the pipeline YAML stable across runs and only change the
artifact URI per submission — useful for auto-loop iterations that rotate through
staged variants.

1. In the pipeline YAML, declare an input on the top-level graph:
   ```yaml
   inputs:
     - name: input_artifact_uri
       type: URI
   ```
2. Wire it through to the ingest task:
   ```yaml
   tasks:
     ingestInputData:
       componentRef:
         name: "<your backend's ingest component>"
       arguments:
         uri:
           graphInput: {inputName: input_artifact_uri}
   ```
3. Supply the URI per run with `--arg` (or `--args-json`). **Run arguments bind to
   the pipeline's top-level `inputs:`** — they are distinct from task-level
   `arguments:` wiring inside the pipeline YAML (as shown in step 2 above). See
   `tangle sdk pipeline-runs submit --help` for the full surface, and
   [`references/tangle-tools.md`](tangle-tools.md) for the canonical submit form.
   ```bash
   tangle sdk pipeline-runs submit pipeline.yaml \
     --arg input_artifact_uri="<scheme>://path/to/artifact/"
   ```

## `arguments:` vs. run arguments (`--arg`)

Two different things in two different places:

| What | Where it lives | What it does |
|---|---|---|
| `arguments:` | Pipeline YAML, under each `tasks.<TaskName>:` | Wires a task input to a `constant`, `graphInput`, or `taskOutput`. |
| `--arg K=V` / `--args-json` | Passed to `pipeline-runs submit` on the CLI | Binds values to the pipeline's top-level `inputs:` for a single run. |

If you mix them up — e.g. you intended a per-run value but left a stale
`constant` wired into the task — the submit may succeed with the wrong input,
leaving the pipeline pointed at a stale or unrelated artifact. Verify before
submit: grep the pipeline YAML for the `graphInput` you expect, and confirm the
`--arg` name matches the declared `inputs:` name exactly.

## Staging the bytes

Where the artifact lives is backend-specific. Under the OSS `tangle` backend the
storage provider is HuggingFace, so URIs are scheme-agnostic and look like:

```
hf://{model|dataset|space}s/<user>/<repo>@<branch>/<path>
```

- **Push bytes** with a generic client — for `hf://` URIs, `huggingface_hub`
  (`HfApi().upload_file(...)` / `upload_folder(...)`).
- **Read the resulting `uri` scheme-agnostically.** Don't hard-code a scheme; the
  ingest component receives whatever URI you give it. See
  [`OSS-CONVENTIONS.md` §5](../OSS-CONVENTIONS.md) for the artifact-metadata and
  signed-URL fetch recipes.

If the upload requires a credential, prefer reading it without leaving it in shell
history:

```bash
read -rs HF_TOKEN     # paste, then Enter — input is not echoed
export HF_TOKEN
```

Pipeline-side credentials belong in `secrets` (`dynamicData.secret`), never
hard-coded into the YAML.

## Gotchas

- **Trailing slash matters.** A URI like `<scheme>://b/p/file` is treated as a
  single blob; `<scheme>://b/p/dir/` is treated as a directory prefix and copied
  recursively. The ingest component's `Data` output is shaped accordingly —
  downstream tasks must read it as a file vs. a directory.
- **Unique filenames.** Stage each variant under a unique path (a per-session or
  per-round prefix, or a content hash in the filename). If you overwrite the same
  URI between runs, an earlier run that re-reads it can pick up the wrong bytes,
  and you lose the ability to tell two runs apart by their input. One artifact,
  one immutable path.
- **Reference a local component by URL when iterating.** For a registered
  component you normally pin `componentRef: {digest: <DIGEST>}` (reproducible) or
  `componentRef: {name: "<Name>"}` (resolves to the latest published version at
  hydrate time). While you're still editing a component locally, swap the
  digest/name for a file reference so the pipeline picks up your edits without a
  publish round-trip:
  ```yaml
  componentRef:
    url: "file:///abs/path/to/component.yaml"
  ```
  Swap back to a `digest:` (or `name:`) once the component is published and stable.
- **No ingest component? Read the URI in a Python component.** If your backend
  registers no ingest component, generate a small one from a Python source file
  that fetches the URI and writes a `Data` output:
  `tangle sdk components generate from-python fetch.py --image <registry/img:tag>`
  (build/push the image yourself; `set-container-image` is a stub — do not use
  it). See [`agents/builder.md`](../agents/builder.md).
- **Don't hand-roll throwaway ingest.** If you find yourself shelling out to copy
  bytes inside an unrelated container, stop — factor it into a reusable ingest
  component (or your backend's, if it has one) so the dependency is visible in the
  graph.
- **Privacy.** Be deliberate about where you stage. Treat any shared store as
  broadly readable. PII, customer data, credentials, or embargoed models belong in
  a private, access-controlled location — never in a shared corpus or registry.
  For preserving an artifact for *indefinite reuse* (with provenance) rather than
  one-shot ingest, see [`references/data-sources.md`](data-sources.md).
