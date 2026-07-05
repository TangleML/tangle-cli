# Step 5: Evaluate

Two parts: collect metrics, then analyze.

## Part 1: Collect Metrics

For each completed run:
1. Get artifact metadata for the eval task with `tangle sdk artifacts get RUN_ID -q '{"tasks": {"<EvalTask>": ["<metric_output>"]}}'`. This returns metadata records (`{id, uri, size, hash}`) — it does **not** download bytes. Read the `uri` scheme-agnostically (under the OSS backend it is typically `hf://…`); never assume a particular storage scheme. To read the metric JSON contents, follow the signed-URL fetch recipe in [`OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §5: `artifacts get` → `tangle api artifacts signed-artifact-url` → fetch with `curl -L` (or `huggingface_hub` for `hf://` URIs).
2. Extract target metric (`scenario.metrics.target.path`) and common metrics
3. Check guard metrics (`scenario.metrics.guards`)
4. Remove from MEMORY.md "Active Runs"

Present results sorted by target metric. Include all common metrics.

**Statistical significance** (assumes fixed eval set):
- < 0.3%: likely noise. > 1.0%: almost certainly real.

### Optional: Fetch Training Logs

For the best run and any anomalous results, fetch container logs from the training component:
```bash
tangle sdk pipeline-runs logs EXECUTION_ID
```
Use the execution_id recorded in Step 4 (logs are keyed by EXECUTION_ID, not run id). Look for:
- Training convergence (loss progression)
- Early stopping triggers
- Warning messages about data quality
- OOM or resource warnings

For OOM, eviction, scheduling, or "pod not found" events, the Tangle backend does not store these — consult your launcher's runtime (see [`OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §7 and [`references/event-log.md`](event-log.md)).

## Part 2: ML-Driven Analysis

For the best run + any unexpected results, analyze deeper:
1. **Error Analysis** — fetch predictions (via the §5 signed-URL recipe), compare vs baseline per-example, identify biggest movers
2. **SHAP Diff** — compare feature importances between runs
3. **Metric Decomposition** — per-segment breakdown, check guards per-segment
4. **Direction Proposal** — propose next experiments based on findings

The Direction Proposal feeds into Step 1 of the next round.

Promote significant findings to MEMORY.md.

## Gate — do NOT proceed to Step 6 until all pass:
- [ ] Metrics fetched and extracted for every completed `run_id`
- [ ] Guard metrics checked for every run
- [ ] Results comparison table printed to user (keyed by `run_id`)
- [ ] MEMORY.md "Active Runs" cleared for all completed `run_id`s
- [ ] Session log `Run Log` updated with outcome per `run_id`
- [ ] ML analysis performed on best run (error, SHAP, segments)
- [ ] Direction Proposal written (what to try next round)
- [ ] `run_complete`, `analysis`, and `step_transition` events logged
- [ ] **Reload + review**: re-read this step file and [`references/event-log.md`](event-log.md); agent confirms it remembers them
