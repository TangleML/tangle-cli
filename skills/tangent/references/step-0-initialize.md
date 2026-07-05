# Step 0: Initialize

Pure setup — load everything before the experiment loop. No analysis, no decisions.

## Set up the `tangle` CLI

Before anything else, make sure you can invoke the CLI. The skills drive the OSS
core, run from a checkout of the `tangle-cli` repo:

```bash
uv run tangle --help
```

If that fails, work through `references/setup.md` first (checkout, `uv tool install tangle-cli`, or `uvx --from tangle-cli tangle …`; base-url/auth). Once `uv run tangle --help` or installed `tangle --help` works, discover the available commands:

```bash
uv run tangle quickstart
```

> For an installed CLI, prefer `uv tool install tangle-cli` and invoke `tangle …` or `tangle-cli …`; for one-off execution, use `uvx --from tangle-cli tangle …`.

## Scenario Directory

All experiment state lives in the scenario directory. Set the absolute path:

```
SCENARIO_DIR=<path_to_scenario_directory>
```

From a checkout, all `tangle` commands run via `uv run tangle …`. From an
installed CLI, use `tangle …` / `tangle-cli …`; for one-off execution, use
`uvx --from tangle-cli tangle …`. All file reads/writes use absolute
`SCENARIO_DIR` paths. These are different locations — don't confuse them.

## No Scenario Yet? Build One

If the user doesn't have a scenario directory, or `scenario.yaml` doesn't exist
at `SCENARIO_DIR`, **run the scenario builder interview yourself**.

Read `agents/scenario-builder.md` and follow its instructions directly — do NOT
spawn it as a subagent. The scenario builder is a multi-turn interview that
requires user interaction at every phase. Subagents cannot interact with the
user, so spawning it would skip the interview entirely.

Set the target directory to `SCENARIO_DIR` and work through all 7 phases.
Once the interview completes and files are generated, continue with Load State
below.

## Load State

1. Read `scenario.yaml` — target metric, search space, experiment actions, failure playbook, timing. **Use scenario.yaml for all paths and parameters — never hardcode.**
2. Read `MEMORY.md` — best config, key lessons, session index. If it has prior findings, don't repeat experiments.
3. Read `case_studies/*.md` if they exist — past experiment reports on this scenario
4. Read today's `sessions/YYYY-MM-DD.md` if it exists (resume)
5. Read all skills in `<scenario_dir>/skills/` (experiment-playbook, metrics-guide, etc.)
6. Ensure `logs/` and `sessions/` directories exist

### Resume: Check Active Runs

If MEMORY.md "Active Runs" lists runs from a prior session, light-poll each with
the CLI: `uv run tangle sdk pipeline-runs status RUN_ID` (run + derived status
summary), and `uv run tangle sdk pipeline-runs graph-state EXECUTION_ID` for the
per-task graph state. Classify: all tasks terminal → Step 5, any RUNNING →
Step 4, any FAILED → Step 4 (debugger). This replaces Step 1-3 when resuming.

## Bootstrap Pipeline

### If no `pipeline.yaml` exists in `SCENARIO_DIR`:

**Option A: User has pipeline source code (preferred)**
If the repo already has a pipeline YAML with `name:` or `digest:` component refs
(no inline `spec:` blocks), ask the user for its path and copy it:
```bash
cp <path-to-pipeline.yaml> $SCENARIO_DIR/pipeline.yaml
```
This is preferred because `--hydrate` on submit (the default) will always resolve
the latest published component versions when the refs are `name:`/`digest:` only.

**Option B: Export from baseline run**
If no source pipeline exists, export the root spec from the baseline run:
```bash
uv run tangle sdk pipeline-runs export BASELINE_RUN_ID --output $SCENARIO_DIR/pipeline.yaml
```
`export` writes the root spec as-is. There is no `--dehydrate` flag: if the
exported YAML carries full inline `spec:` blocks, `--hydrate` on submit is a no-op
for those tasks (it has nothing to resolve). That is fine — hand-edit the exported
spec and submit it directly. If you want submit to pick up the latest published
versions, replace an inline `spec:` block with a `name:` or `digest:` ref before
submitting.

### Then:
1. Parse pipeline.yaml to build task → source file mapping (see researcher agent for code discovery)
2. Record the baseline config and metrics under `$SCENARIO_DIR` (note the baseline `run_id` and any artifact `uri`s so you can re-reference them; see `references/data-sources.md` if your backend provides reusable data-source components)
3. Initialize `$SCENARIO_DIR/logs/events.jsonl` (create if it doesn't exist)

## Gate — do NOT proceed to Step 1 until all pass:
- [ ] `uv run tangle --help` works (CLI reachable; see `references/setup.md` if not)
- [ ] `uv run tangle quickstart` ran successfully
- [ ] `SCENARIO_DIR` set to absolute path
- [ ] `scenario.yaml` read and understood
- [ ] `MEMORY.md` read
- [ ] Scenario skills loaded
- [ ] `references/tangle-tools.md` read (Submission Rules, light polling, CLI reference)
- [ ] `references/event-log.md` read (event types and schemas)
- [ ] `pipeline.yaml` exists in `SCENARIO_DIR` (either copied with `name:`/`digest:` refs, or exported and hand-edited)
- [ ] `logs/` and `sessions/` directories exist
- [ ] `step_transition` event logged
- [ ] **Reload + review**: re-read `SKILL.md`, `references/tangle-tools.md`, and `references/event-log.md`; agent confirms it remembers them before starting Step 1
