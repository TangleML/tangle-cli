# Promoting and reusing data sources

> **Conditional pattern — backend-dependent.** The data-source components
> described here are **not part of the OSS `tangle` core**. They are a *pattern*
> some backends provide for preserving a run's output for indefinite reuse. If
> your backend does **not** register components like these, skip this file and
> use the lightweight reuse pattern in [Gotchas](#gotchas) instead: record a
> producer run's `run_id` and the artifact `uri` locally, and re-reference them.
> Never treat the data-source family as a prerequisite for any workflow step.

*If your backend provides components like these*, the canonical way to preserve a
Tangle run's output for indefinite reuse — or to consume someone else's preserved
artifact in a new run — is a **data-source family of components**:

| Component (if provided) | Use it to | Inputs → Outputs |
|---|---|---|
| `Promote to data source` | Snapshot a local file/directory artifact into a stable, ID-addressable storage location with provenance metadata | `input` (`Data`), `resource_name` (`String`) → `data_source_id` (`String`) |
| `Load data source` | Materialize a previously-promoted snapshot back into a downstream task | `data_source_id` (`String`) → `output` (`Data`), `metadata` (`Json`) |
| `Find data source` | Resolve the latest `data_source_id` for a known `(author, resource_name)` pair, optionally bounded by created-at date | `author` (`String`), `resource_name` (`String`), optional `min_created_at` / `max_created_at` (`String`) → `data_source_id` (`String`) |

A "data source" is opinionated about *preservation*: every promote records
a verbatim provenance metadata blob alongside the payload (producer's
identity, timestamp, source kind, file count, the producing `pipeline_run_id`,
…) so a consumer can always trace data back to the experiment that made
it. Where the bytes actually live and how the ID is constructed are
internal to the components — treat the `data_source_id` as opaque.

This is **different from `uploading-artifacts.md`**:

- Generic ingest (staging a local file for a single experiment) covers
  **ephemeral ingest** — no provenance, no naming convention beyond a
  per-session prefix.
- `Promote to data source` / `Load data source` / `Find data source`
  cover **persistent reuse** — a curated artifact you want to find,
  share, and re-run against next week or next quarter.

Use the lightweight ingest path when the artifact only matters for one
experiment. Promote when you would be sad to lose the artifact, or when you
expect another experiment (yours or someone else's) to consume it.

## ⚠️ Sensitive data — read before promoting

**Promoted data persists indefinitely and every backend user may be able to
read it.** Treat every promote like a public publish to the entire user
base: anyone may be able to `Load data source` your `data_source_id`, today or
years from now. Promotes are also hard to undo — once a `data_source_id`
is published and another pipeline depends on it, deleting the underlying
payload breaks that pipeline.

**Do NOT promote artifacts that contain:**

- PII (names, emails, addresses, payment data, IPs, device IDs that
  resolve to people).
- Confidential or contractually restricted data.
- Embargoed model weights, unreleased product information, internal-only
  scoring rubrics where a leak could change behaviour.
- Anything that would require special authorization to query elsewhere —
  it requires the same care here.
- Credentials, tokens, keys, anything sourced from `secrets`.

**If you are not sure, do not promote.** Ask the user. If the artifact
is mixed (small public summary alongside a sensitive raw dataset),
promote only the cleaned subset and leave the raw data in a private,
access-controlled location.

When in doubt, escalate to the data owner before promoting.

## ⚠️ Do NOT recurrently promote

**A pipeline that includes `Promote to data source` should not be driven by a
recurring trigger.** Every run would mint a fresh snapshot that persists
indefinitely — a daily cadence produces 365 effectively-identical snapshots a
year, all loadable by every user, none of which the storage will ever expire
on its own. That floods the storage and makes legitimate `Find data source`
queries useless.

> There is no scheduler command in the OSS core; this is a conceptual caution
> about wiring promote into *any* recurring or automated submission path you
> may build downstream.

Promote belongs in **interactive or one-off submissions**: a human (or
an agent on a human's behalf) decided this particular artifact is worth
preserving. If you genuinely need a periodically-refreshed data source
— e.g. a weekly catalog snapshot — get explicit sign-off from the user
*and* a plan for cleaning up older snapshots before wiring promote into
a recurring pipeline.

When iterating on a pipeline that contains a promote task, drop the
promote (or short-circuit it) until you're submitting a run whose output
you actually want preserved.

## The `data_source_id`

The `data_source_id` is an opaque string identifying one snapshot.
`Promote to data source` and `Find data source` both *emit* one;
`Load data source` *consumes* one. Pass it verbatim — don't parse or
construct it yourself. Save it to the scenario's `MEMORY.md` or session
log so other runs can reuse it.

(`Find data source` is keyed by `(author, resource_name)` — not by
`data_source_id` — see Recipe D.)

A re-promote of the same `resource_name` **never overwrites** an earlier
snapshot — you always get a fresh `data_source_id`, and the previous one
remains loadable.

## Recipe A — Promote a Tangle task output, so it can be reused

When a task produces an artifact (curated eval set, fine-tuned model
checkpoint, generated annotations) that you want to keep beyond the
run's TTL or share with other scenarios.

1. **Confirm the artifact is safe to publish** — see the sensitive-data
   warning above. If unsure, ask the user before adding the promote task.

2. **Add `Promote to data source` downstream** of the producer task:

   ```yaml
   tasks:
     promoteEvalSet:
       componentRef:
         name: "Promote to data source"
         # Optional: pin by digest after first use for reproducibility.
       arguments:
         input:
           taskOutput: {taskId: buildEvalSet, outputName: dataset}
         resource_name:
           constant: "ranking-eval-v3"
   ```

3. **Optional: capture the resulting `data_source_id` after the run.**
   Two ways:

   - **Read the promote task's logs.** The component prints `PROMOTED.
     data_source_id = <id>` near the end of its own task log — fetch
     with `uv run tangle sdk pipeline-runs logs <execution_id>`. Simplest
     and works without modifying the pipeline.
   - **Wire it into a downstream task.** Connect the promote task's
     `data_source_id` output (a `String`) into another task that prints
     or annotates it. Useful if you want the value to flow into the
     pipeline graph (e.g. a follow-up `Load data source` in the same
     run) rather than be recovered out-of-band.

   Don't try to recover the value with `uv run tangle sdk artifacts get` —
   that returns artifact *metadata* (`uri`/`size`/`hash`), not the contents
   of scalar `String` outputs.

4. **Record it.** Once the run completes, add the `data_source_id` to the
   scenario's `MEMORY.md` (or the session log) so the next round — or a
   teammate — can reference it without re-discovering it.

## Recipe B — Promote a local file (ingest first, then promote)

`Promote to data source` consumes a pipeline task output (`Data`), not a
local filesystem path. If you're starting with a file or directory on
your workstation — a curated eval set you built locally, a checkpoint
you downloaded from elsewhere, an annotation bundle — first get the bytes
into a task output, **then** run a one-off promote pipeline.

1. **Confirm the artifact is safe to publish** — same checklist as
   Recipe A. If unsure, ask the user.

2. **Ingest the local artifact** so it becomes a pipeline task output.
   Follow the generic ingest pattern in `uploading-artifacts.md`
   (file-vs-directory trailing-slash semantics, unique-filename guidance,
   and the `componentRef` wiring shape). The exact ingest component depends
   on what your backend provides; treat its output (`Data`) as the input to
   the promote task.

3. **Build a 2-task promote pipeline** wiring your ingest task into
   `Promote to data source`:

   ```yaml
   tasks:
     ingestEvalSet:
       componentRef:
         name: "<your backend's ingest component>"
       arguments:
         # ...ingest args per uploading-artifacts.md...
     promoteEvalSet:
       componentRef:
         name: "Promote to data source"
       arguments:
         input:
           taskOutput: {taskId: ingestEvalSet, outputName: Data}
         resource_name:
           constant: "ranking-eval-v3"
   ```

4. **Submit it as a one-off interactive run** — do **not** wire this into
   a recurring trigger (see the prohibition above; every recurring run would
   mint a fresh permanent snapshot). After completion, recover the
   `data_source_id` via the promote task's logs (Recipe A step 3) and
   record it in `MEMORY.md`.

The `builder` agent's "Preserving a run's output as a reusable
data source" workflow has more detail on wiring and submission, and
`uploading-artifacts.md` walks the ingest step.

## Recipe C — Load someone else's (or a previous round's) data source

When a downstream task needs an artifact that was previously promoted —
for example, a baseline eval set, a frozen feature snapshot, a model
checkpoint another scenario published.

1. **Get the `data_source_id`.** Three ways:

   - It was recorded in your scenario's `MEMORY.md` / session log from an
     earlier run.
   - A teammate handed it to you directly.
   - You discovered it with `Find data source` (see Recipe D).

2. **Add `Load data source`** as a graph input or upstream task:

   ```yaml
   tasks:
     loadEvalSet:
       componentRef:
         name: "Load data source"
       arguments:
         data_source_id:
           constant: "<opaque data_source_id string>"
           # Or graphInput: {inputName: eval_set_id} to make it a run-config parameter.

     myEvaluator:
       arguments:
         eval_data:
           taskOutput: {taskId: loadEvalSet, outputName: output}
         eval_metadata:
           taskOutput: {taskId: loadEvalSet, outputName: metadata}
   ```

3. **Inspect the metadata if needed.** The `metadata` output is the
   verbatim provenance record — useful when a downstream task needs to
   confirm `source_kind`, see when the data was produced, or chain back
   to the producing `pipeline_run_id`.

## Recipe D — Find an existing data source before promoting your own

Before promoting a new snapshot, check whether something similar already
exists. Reuse beats duplication.

`Find data source` is a pipeline component, not a CLI command. Wire it
upstream of `Load data source` and let it resolve to the latest snapshot
matching an `(author, resource_name)` pair, optionally bounded by date.
The `author` is the sanitized form of the producing user's identity —
e.g. a `first.last` local part typically resolves to `first-last` (dots
become dashes). Try a few likely variations if the first guess misses.

```yaml
tasks:
  findEvalSet:
    componentRef:
      name: "Find data source"
    arguments:
      author:
        constant: "first-last"
      resource_name:
        constant: "ranking-eval-v3"
      # Optional, both inclusive, ISO date or full datetime:
      # min_created_at:
      #   constant: "2026-05-01"
      # max_created_at:
      #   constant: "2026-05-31"

  loadEvalSet:
    componentRef:
      name: "Load data source"
    arguments:
      data_source_id:
        taskOutput: {taskId: findEvalSet, outputName: data_source_id}
```

`Find data source` fails the task (and writes no output) if no snapshot
matches — so if your pipeline branches on "exists vs. needs producing",
keep the find/load chain in a sub-pipeline that's safe to fail, or run
it in a separate exploratory submission first to confirm the ID before
wiring it into the main flow.

If you don't have an `(author, resource_name)` guess at all, check the
scenario's `MEMORY.md` and session logs for previously-recorded
`data_source_id`s, or ask the teammate who ran the producing experiment.
There is no general "list everything" path — discovery is intentionally
scoped through `Find data source`.

## Recipe E — Pass `data_source_id` through the run config

Declare the ID as a top-level graph input, wire it to `Load data source`,
and supply it per submission with `--arg` (or `--args-json`):

```yaml
# pipeline.yaml
inputs:
  - name: eval_set_id
    type: String

tasks:
  loadEvalSet:
    componentRef:
      name: "Load data source"
    arguments:
      data_source_id:
        graphInput: {inputName: eval_set_id}
```

```bash
uv run tangle sdk pipeline-runs submit pipeline.yaml \
  --arg eval_set_id="<opaque data_source_id string>"
```

## Gotchas

- **No data-source components? Use the lightweight pattern.** If your
  backend doesn't register these components, you can still reuse a prior
  run's output: record the producer's `run_id` and the artifact `uri`
  (read it scheme-agnostically from `uv run tangle sdk artifacts get RUN_ID`)
  in `MEMORY.md`, and re-reference that `uri` directly. URIs are
  backend-agnostic and may look like `hf://datasets/<org>/<repo>@main/<path>`
  — read the `uri` field; do not assume a scheme.
- **`Load data source` reproduces the original shape.** If the producer
  promoted a single file, the `output` is a single file. If a directory,
  `output` is a directory tree. The consumer must read it accordingly.
- **`Find data source` returns "latest" by creation timestamp.** If you
  want a frozen, version-pinned reference, copy the `data_source_id` into
  `MEMORY.md` or pass it through the run config (Recipe E) instead of
  resolving it dynamically every run.
- **The metadata's `pipeline_run_id` is the producer's run, not yours.**
  Useful for tracing provenance from a downstream consumer back to the
  experiment that generated the artifact.
- **Promote is for outputs another run will load.** Don't promote
  one-shot artifacts — let the run's TTL clean them up. Promotes persist
  indefinitely and may be visible to every backend user.
- **Don't store credentials, tokens, or anything sourced from
  `secrets`.** See the sensitive-data warning above and `secrets.md`.
