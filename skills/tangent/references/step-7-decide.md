# Step 7: Decide

## Review Gate

**Launch the reviewer as a subagent using the Agent tool.** Read `agents/reviewer.md`
and pass its full content as the agent prompt, with this task context appended:
```
---
Task context:
Scenario: <scenario_name>
Best run: <best_run_id>           # best-performing run of this round
Baseline run: <baseline_run_id>
Scenario dir: <SCENARIO_DIR>
Report path: <SCENARIO_DIR>/logs/report-<best_run_id>.md
Write review to: <SCENARIO_DIR>/logs/review-<best_run_id>.md
Return one-line: "<VERDICT>: <summary>"
```

The same `<best_run_id>` keys the report (Step 6), this review, and the final
`learning-<best_run_id>.json` record below.

Act on the verdict:
- **APPROVE** — proceed with convergence check below
- **CONCERNS** — address the issues, then re-evaluate
- **BLOCK** — fix blocking issues first (go to Step 1)

## Convergence

Check `scenario.budget.convergence`: min_improvement met? Budget exhausted?
patience_rounds without improvement?

Before stopping on a plateau: have you tried feature selection, data actions,
analysis actions, or pipeline modifications?

**If done**: copy `logs/report-<best_run_id>.md` to
`case_studies/<YYYY-MM-DD>-<slug>.md`, update status to final outcome
(SUCCESS / MARGINAL / NO_IMPROVEMENT / REGRESSION).

**If not done**: go to Step 1.

## Record final learning to the corpus

After every round (regardless of outcome), write a structured `learning.json`
locally and record it to the learnings corpus. Negative results are signal too —
do not skip recording on regression or failure.

**Use `<best_run_id>` (the round's representative run) as the file key.** Multi-run
rounds key by the best-performing run; single-run rounds key by that run.

Write `$SCENARIO_DIR/logs/learning-<best_run_id>.json` with the schema documented in
[`references/knowledge-corpus.md`](knowledge-corpus.md) (scenario, run_id,
hypothesis, config_diff, primary_metric, all_metrics, outcome, lesson,
next_direction). For multi-run rounds, include sibling run_ids and their metrics in
the `all_metrics` block so the corpus retains the full round.

The corpus is a local directory by default (`LEARNINGS_DIR`, default
`$SCENARIO_DIR/learnings/`), with an optional shared HuggingFace dataset tier; see
[`OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §6 and
[`references/knowledge-corpus.md`](knowledge-corpus.md) for the full recipe. Record
the learning with a plain copy into the corpus directory:

```bash
mkdir -p "$LEARNINGS_DIR/<scenario>"
cp "$SCENARIO_DIR/logs/learning-<best_run_id>.json" \
   "$LEARNINGS_DIR/<scenario>/learning-<best_run_id>.json"
```

If recording fails (e.g. the corpus directory is unwritable), log a
`learning_record_failed` event and keep going — the local copy under
`$SCENARIO_DIR/logs/` is the source of truth and a future session can retry.

## Gate — do NOT finalize until all pass:
- [ ] **`review-<best_run_id>.md` exists at `$SCENARIO_DIR/logs/`** (verify with Read — per-round file; if missing, reviewer did not run)
- [ ] Verdict is APPROVE (not CONCERNS or BLOCK)
- [ ] Convergence criteria evaluated against scenario.budget
- [ ] `learning-<best_run_id>.json` written under `$SCENARIO_DIR/logs/` and recorded to `$LEARNINGS_DIR/<scenario>/` (or `learning_record_failed` event logged)
- [ ] If converged: `logs/report-<best_run_id>.md` copied to `case_studies/`, status updated
- [ ] `report_generated` event logged (if converged)
- [ ] **Reload + review**: re-read this step file and [`references/knowledge-corpus.md`](knowledge-corpus.md); agent confirms it remembers them before looping back to Step 1 (or finalizing)
