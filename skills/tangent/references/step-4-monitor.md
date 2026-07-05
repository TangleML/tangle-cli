# Step 4: Monitor

Do NOT exit until all runs are DONE or permanently failed. Keep slots filled.

## Loop

```
LOOP:
  1. Light-poll all runs (graph-state CLI, cheap status summary)
  2. Inspect completed/failed runs (pipeline-runs details --include-execution-state)
  3. Process completed runs → queue for Step 5
  4. Process failed runs → launch debugger agent
  5. Backfill open slots → go to Step 2 for new experiments
  6. Wait → dispatch or sleep, loop
```

## Checking Run Status (Light Polling)

**Use the purpose-built status/graph-state CLI for status checks — NOT
`pipeline-runs wait` or `pipeline-runs details`.** `pipeline-runs status` returns a
run plus a derived status summary, and `pipeline-runs graph-state` returns the
per-task graph execution state — both far cheaper than the full execution tree.
`pipeline-runs details --include-execution-state` returns the entire execution tree
(very large). Only use the heavy call when you need to debug or extract execution_ids.

Prefer the CLI over hand-rolling a Python poll loop (the `TangleApiClient` verified
surface is `pipeline_runs_get(run_id)` and `find_existing_components(...)`; for status
and graph state, the CLI commands below are the supported path).

Light status for a single run (run + derived status summary):
```bash
tangle sdk pipeline-runs status RUN_ID
```

Graph execution state (per-task status counts; takes an EXECUTION_ID):
```bash
tangle sdk pipeline-runs graph-state EXECUTION_ID
```

For multiple runs, call `status` once per run id:
```bash
for rid in RUN_1 RUN_2 RUN_3; do
  echo "$rid:"
  tangle sdk pipeline-runs status "$rid"
done
```

`status` surfaces the run's execution id; pass that to `graph-state` when you need the
per-task breakdown for a specific run.

Mark runs exceeding 2x `scenario.timing.total_seconds` as STUCK and replace.

## Post-Completion Inspection

When a run completes (SUCCEEDED or FAILED), immediately run:
```bash
tangle sdk pipeline-runs details RUN_ID --include-execution-state
```

This returns the execution tree with per-component status. Check for:
1. Any component in FAILED or SYSTEM_ERROR state (the overall run may show SUCCEEDED
   if the failed component was optional or non-blocking)
2. Components with unexpectedly short execution times (may indicate silent failures)
3. The training/tuning component specifically — note its execution_id for log fetching
   in Step 5

If any component failed unexpectedly, launch the debugger subagent even if the
overall pipeline status is SUCCEEDED.

Record the execution_id of key components (training, evaluation) in the session log
alongside the run_id. Step 5 will need these for detailed analysis.

## Failed Runs

**Launch the debugger as a subagent using the Agent tool.** Read `agents/debugger.md`
and pass its full content as the agent prompt, with this task context appended:
```
---
Task context:
Run ID: <run_id>
Failure playbook: <scenario.failure_playbook as YAML>
Pipeline task mapping: <task → source file>
Write snapshot to: <SCENARIO_DIR>/logs/failures/<run_id>.md
Return one-line: "<FAILURE_TYPE>: <description> → <action>"
```

Act on the diagnosis:

| Failure Type | Action | Budget Impact |
|-------------|--------|---------------|
| **PERMISSION** | Fix per diagnosis, resubmit | No cost |
| **INFRA** | Retry if transient, fix if persistent | No cost |
| **CONFIG** | Fix per diagnosis, resubmit | No cost |
| **TRAINING** | Record as result (failure IS data) | Already counted |
| **EVAL** | Fix per diagnosis, resubmit | No cost |
| **UNKNOWN** | Escalate to user | No cost |

## Cancelling a Run

Only cancel when the user asks or a run is blocking resources.
```bash
tangle sdk pipeline-runs cancel RUN_ID
```

## Waiting

Use `dispatch` for non-blocking wait (if available), or sleep between light polls:
```
dispatch({ command: "tangle sdk pipeline-runs wait <run_id> --max-wait <interval> --poll-interval 10 --exit-on-first-failure" })
```
Interval: 180s (short pipelines), 600s (medium), 900s (long). `wait` defaults to
`--max-wait 600` and `--poll-interval 10` if you omit them.
On wake, light-poll ALL active runs (`pipeline-runs status`), not just the awaited one.

## Gate — do NOT proceed to Step 5 until all pass:
- [ ] All runs resolved (SUCCEEDED, permanently FAILED, or STUCK-replaced)
- [ ] No RUNNING runs remain
- [ ] Each completed run inspected with `pipeline-runs details --include-execution-state`
- [ ] Key component execution_ids recorded in session log
- [ ] Each failed run has a debugger snapshot in `logs/failures/`
- [ ] Each failed run has a `run_failed` event logged
- [ ] Backfill exhausted (no open slots with pending experiments and budget)
- [ ] **Reload + review**: re-read this step file and `agents/debugger.md`; agent confirms it remembers them
