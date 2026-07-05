# Iterating on pipelines from existing runs

When modifying and re-running an existing pipeline (e.g. change params for failed tasks):

1. **Export the run**:
   ```bash
   tangle sdk pipeline-runs export <run_id> --output /tmp/pipeline.yaml
   ```
   This writes the run's root pipeline spec to `/tmp/pipeline.yaml` (omit `--output` to print
   to stdout). The export is the spec **as-is** — there is no `--dehydrate` flag and no
   separate `.config.yaml`. Run arguments are not exported as a config file; you re-supply
   them at submit time with `--arg`/`--args-json`/`--config` (step 6).

2. **Inspect execution statuses**:
   ```bash
   tangle sdk pipeline-runs details <run_id> --include-execution-state
   ```
   to identify failed/cancelled/skipped executions. For a quick run + derived status
   summary, use `tangle sdk pipeline-runs status <run_id>`.

3. **Understand the YAML structure**: Tangle pipelines are nested subgraphs. Inputs flow
   through the hierarchy via `graphInput` wiring: top-level task output → subgraph input →
   nested subgraph input → leaf task argument. Trace the wiring at each level before
   modifying. For pipeline and component schema details, use `tangle sdk pipelines
   --help` / `tangle sdk components --help`, browse the curated standard library
   (`tangle sdk published-components library`), and consult the public docs at
   `github.com/TangleML/website/tree/master/docs`.

4. **Modify the pipeline**: Edit the exported YAML directly. To replace a component, swap
   its component reference (a published `digest:` or a local `url: file://`) with a new
   `url: file://` reference pointing to the replacement component file.

5. **Preview**: render the structure before submitting —
   ```bash
   tangle sdk pipelines diagram /tmp/pipeline.yaml   # Mermaid
   tangle sdk pipelines layout /tmp/pipeline.yaml    # auto-layout
   ```
   and validate it parses:
   ```bash
   tangle sdk pipelines validate /tmp/pipeline.yaml
   ```

6. **Submit** (see Submission Rules in `references/tangle-tools.md`):
   ```bash
   tangle sdk pipeline-runs submit /tmp/pipeline.yaml \
     --arg <key>=<value> \
     --annotation session=<session> --annotation round=<round>
   ```
   Hydration is the default — the submit resolves component references for you, so there
   is no "dehydrate first" guard to run. Supply run arguments inline with `--arg K=V`
   (repeatable), or `--args-json '<JSON>'` for structured/nested args, or a `--config`
   file for CLI-option defaults (base-url/auth/log-type — not run args). `submit` never
   waits; to block on completion, follow up with:
   ```bash
   tangle sdk pipeline-runs wait <RUN_ID> --max-wait 600 --poll-interval 10
   ```
